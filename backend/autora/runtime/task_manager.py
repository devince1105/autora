"""Task manager: the work queue and the lifecycle of tasks and their agent runs (T-202).

The ``tasks`` table is the queue. Workers claim with ``FOR UPDATE SKIP LOCKED`` and hold a lease;
every state change goes through ``TASK_FSM`` / ``AGENT_RUN_FSM`` (audited) and emits its event in
the caller's transaction. All methods take a session and never commit: the caller owns the
transaction, so a task change, its events and the agent's activity land together or not at all.

Rules that keep the queue honest:
- **One claim, one token.** A claim returns a token stored on the task. A worker whose lease was
  reclaimed (crash, stall) fails with ``LeaseLost`` when it tries to finish; nothing is
  overwritten. The reaper is the only thing that reclaims leases.
- **One open run per agent** (DB unique index). An agent is one person with one activity.
- **Attempts are consumed by claims**, whatever ends the run: a crash cannot loop forever, and a
  run that ends because a budget was exhausted also counts (the run row for that attempt exists,
  so the attempt cannot be reused). ``release_blocked`` fails a task that has none left.
- **Retries back off**: ``available_at`` = now + base * 2^(attempt-1), capped.
- **FAILED is final.** A retryable failure returns the task to READY.
- **The queue is company-scoped.** Every claim query filters on the agent's company.

Dependency propagation (which downstream tasks become READY, what gets cancelled when a task
fails for good) belongs to the workflow engine (``autora.runtime.dag``, T-203). It plugs in
through ``on_task_finished`` / ``on_task_failed``.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from autora.db.models import (
    AGENT_RUN_TERMINAL,
    ActivityState,
    Agent,
    AgentActivity,
    AgentRun,
    AgentRunState,
    Task,
    TaskState,
)
from autora.infra.ids import uuid7
from autora.runtime.activity import effective_state, set_activity
from autora.runtime.actor import Actor
from autora.runtime.events import catalog as ev
from autora.runtime.events.outbox import emit
from autora.runtime.events.schema import EventPayload, new_event
from autora.runtime.lifecycles import AGENT_RUN_FSM, TASK_FSM

AbortReason = Literal["budget", "policy", "human"]

FinishedHook = Callable[[AsyncSession, Task], Awaitable[Sequence[ev.UnlockedTask]]]
"""Called inside ``succeed`` after the task is SUCCEEDED; returns the tasks it unlocked."""
FailedHook = Callable[[AsyncSession, Task], Awaitable[None]]
"""Called inside ``fail``/``abort``/``cancel`` after the task reached a terminal failure."""


class TaskManagerError(Exception):
    pass


class LeaseLost(TaskManagerError):
    """The claim is no longer valid: the lease expired and was reclaimed, or the task moved on."""


class AgentBusy(TaskManagerError):
    """The agent already has an unfinished run."""


@dataclass(frozen=True)
class Claim:
    task: Task
    run: AgentRun
    agent: Agent
    token: uuid.UUID
    resumed: bool
    """True when an existing run (e.g. after approval) was resumed instead of a new attempt."""


@dataclass
class TaskManager:
    lease: timedelta = timedelta(minutes=5)
    retry_base: timedelta = timedelta(seconds=10)
    retry_max: timedelta = timedelta(minutes=5)
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    on_task_finished: FinishedHook | None = None
    on_task_failed: FailedHook | None = None
    actor: Actor = field(default_factory=lambda: Actor.system("task_manager"))

    # --- creating tasks --------------------------------------------------------------------

    async def add_task(
        self,
        session: AsyncSession,
        *,
        company_id: uuid.UUID,
        project_id: uuid.UUID,
        name: str,
        display_name: str,
        required_role: str,
        workflow_run_id: uuid.UUID | None = None,
        cycle_id: uuid.UUID | None = None,
        depends_on: Sequence[uuid.UUID] = (),
        input: dict[str, Any] | None = None,
        output_schema_ref: str | None = None,
        budget_usd: Decimal | None = None,
        max_attempts: int = 3,
        priority: int = 100,
        ready: bool | None = None,
        task_id: uuid.UUID | None = None,
    ) -> Task:
        """Create a task. It is READY when it has no dependencies (or ``ready=True``), else
        PENDING; a PENDING task tells idle agents of its role that they are waiting upstream."""
        task = Task(
            id=task_id or uuid7(),
            company_id=company_id,
            project_id=project_id,
            workflow_run_id=workflow_run_id,
            cycle_id=cycle_id,
            name=name,
            display_name=display_name,
            required_role=required_role,
            state=TaskState.PENDING.value,
            depends_on=list(depends_on),
            input=input or {},
            output_schema_ref=output_schema_ref,
            budget_usd=budget_usd,
            max_attempts=max_attempts,
            priority=priority,
        )
        session.add(task)
        await session.flush()
        await self._emit(
            session,
            task,
            ev.TaskCreated(
                name=name,
                display_name=display_name,
                required_role=required_role,
                depends_on=list(depends_on),
                workflow_run_id=workflow_run_id,
                budget_usd=budget_usd,
            ),
        )
        if ready if ready is not None else not depends_on:
            await self.mark_ready(session, task)
        else:
            await self._mark_idle_agents_waiting(session, task)
        return task

    async def mark_ready(self, session: AsyncSession, task: Task) -> None:
        """PENDING -> READY (dependencies satisfied). Used by the DAG when upstream succeeds."""
        await TASK_FSM.transition(session, task, TaskState.READY, actor=self.actor)
        await self._emit(session, task, ev.TaskReady(required_role=task.required_role))

    # --- claiming --------------------------------------------------------------------------

    async def claim_next(self, session: AsyncSession, agent: Agent, worker_id: str) -> Claim | None:
        """Claim the best READY task for ``agent``'s role in its company, or None.

        Raises ``AgentBusy`` if the agent already has an unfinished run.
        """
        now = self.clock()
        open_run = await self._open_run(session, agent.id)
        if open_run is not None:
            # The only unfinished run an agent may pick up again is its own run that was
            # suspended for approval and whose task has been released back to READY.
            if open_run.state != AgentRunState.WAITING_APPROVAL:
                raise AgentBusy(f"agent {agent.id} already has an unfinished run")
            task = await session.scalar(
                select(Task)
                .where(Task.id == open_run.task_id, Task.state == TaskState.READY)
                .with_for_update(skip_locked=True)
            )
            if task is None:
                raise AgentBusy(f"agent {agent.id} is waiting for an approval")
            return await self._take(session, task, agent, worker_id, now, resume=open_run)

        suspended_elsewhere = (
            select(AgentRun.id)
            .where(AgentRun.task_id == Task.id, AgentRun.state == AgentRunState.WAITING_APPROVAL)
            .exists()
        )
        task = await session.scalar(
            select(Task)
            .where(
                Task.company_id == agent.company_id,
                Task.required_role == agent.role,
                Task.state == TaskState.READY,
                Task.attempt < Task.max_attempts,
                or_(Task.available_at.is_(None), Task.available_at <= now),
                ~suspended_elsewhere,
            )
            .order_by(Task.priority, Task.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if task is None:
            return None
        return await self._take(session, task, agent, worker_id, now, resume=None)

    async def _take(
        self,
        session: AsyncSession,
        task: Task,
        agent: Agent,
        worker_id: str,
        now: datetime,
        *,
        resume: AgentRun | None,
    ) -> Claim:
        """Lease ``task`` to ``worker_id``: resume ``resume`` or start a new attempt."""
        run = resume
        resumed = run is not None
        if run is None:
            task.attempt += 1
            run = AgentRun(
                id=uuid7(),
                company_id=task.company_id,
                task_id=task.id,
                agent_id=agent.id,
                attempt=task.attempt,
                state=AgentRunState.CREATED.value,
                input=task.input,
            )
            session.add(run)

        token = uuid7()
        task.lease_owner = worker_id
        task.lease_until = now + self.lease
        task.lease_token = token
        task.available_at = None
        await TASK_FSM.transition(session, task, TaskState.RUNNING, actor=self.actor)
        await session.flush()
        await self._emit(
            session,
            task,
            ev.TaskStarted(run_id=run.id, agent_id=agent.id, attempt=task.attempt),
            run=run,
        )
        return Claim(task=task, run=run, agent=agent, token=token, resumed=resumed)

    async def heartbeat(self, session: AsyncSession, claim: Claim) -> datetime:
        task = await self._locked_task(session, claim)
        task.lease_until = self.clock() + self.lease
        return task.lease_until

    # --- finishing -------------------------------------------------------------------------

    async def succeed(
        self,
        session: AsyncSession,
        claim: Claim,
        output: dict[str, Any] | None,
        *,
        output_summary: str | None = None,
    ) -> list[ev.UnlockedTask]:
        task = await self._locked_task(session, claim)
        run = await self._run_of(session, claim)
        now = self.clock()

        task.output = output
        self._release(task)
        await TASK_FSM.transition(session, task, TaskState.SUCCEEDED, actor=self.actor)
        unlocked = list(await self.on_task_finished(session, task)) if self.on_task_finished else []
        await self._emit(
            session,
            task,
            ev.TaskSucceeded(run_id=run.id, output_ref=output_summary, unlocks=unlocked),
            run=run,
        )

        run.output = output
        run.handoff = [{"to_role": u.required_role, "task_id": str(u.task_id)} for u in unlocked]
        await self._finish_run(session, run, AgentRunState.COMPLETED, now)
        await set_activity(
            session,
            claim.agent,
            ev.AgentRunCompleted(
                output_summary=output_summary,
                cost_usd=run.cost_usd,
                steps=run.steps_count,
                duration_ms=_ms(run.started_at or run.created_at, now),
                handoff=[ev.Handoff(to_role=u.required_role, task_id=u.task_id) for u in unlocked],
            ),
            actor=self.actor,
            run_id=run.id,
            task_id=task.id,
            workflow_run_id=task.workflow_run_id,
            task_name=task.display_name,
        )
        return unlocked

    async def fail(
        self,
        session: AsyncSession,
        claim: Claim,
        *,
        error_class: str,
        message: str,
        retryable: bool,
    ) -> bool:
        """Record a failed run. Returns True when the task will be retried."""
        task = await self._locked_task(session, claim)
        run = await self._run_of(session, claim)
        now = self.clock()

        run.error = {"error_class": error_class, "message": message[:2000]}
        await self._finish_run(session, run, AgentRunState.FAILED, now)
        retry = retryable and task.attempt < task.max_attempts
        failed = ev.AgentRunFailed(
            error_class=error_class, message=message[:2000], attempt=task.attempt, final=not retry
        )
        self._release(task)

        if retry:
            await self._requeue(session, task, run, failed)
            return True
        await TASK_FSM.transition(session, task, TaskState.FAILED, actor=self.actor)
        await self._emit(
            session,
            task,
            ev.TaskFailed(run_id=run.id, final=True, error_class=error_class),
            run=run,
        )
        await self._activity(session, claim.agent, failed, task, run)
        if self.on_task_failed:
            await self.on_task_failed(session, task)
        return False

    async def abort(
        self,
        session: AsyncSession,
        claim: Claim,
        *,
        reason: AbortReason,
        message: str | None = None,
    ) -> None:
        """End a run for a reason that is not the work itself.

        budget: the task is BLOCKED_BUDGET until ``release_blocked`` re-queues it;
        policy: the task fails for good; human: the task is cancelled.
        """
        task = await self._locked_task(session, claim)
        run = await self._run_of(session, claim)
        await self._abort_run(session, run, reason, message)
        self._release(task)

        if reason == "budget":
            await TASK_FSM.transition(session, task, TaskState.BLOCKED_BUDGET, actor=self.actor)
            await self._emit(session, task, ev.TaskBlocked(reason="budget"), run=run)
            await set_activity(
                session,
                claim.agent,
                ev.AgentWaiting(reason="budget", blocked_task_id=task.id),
                actor=self.actor,
                task_id=task.id,
                workflow_run_id=task.workflow_run_id,
                task_name=task.display_name,
            )
            return

        if reason == "policy":
            await TASK_FSM.transition(session, task, TaskState.FAILED, actor=self.actor)
            await self._emit(
                session, task, ev.TaskFailed(run_id=run.id, final=True, error_class="PolicyDenied"),
                run=run,
            )  # fmt: skip
            await self._activity(
                session, claim.agent,
                ev.AgentRunAborted(reason="policy", message=message), task, run,
            )  # fmt: skip
        else:
            await TASK_FSM.transition(session, task, TaskState.CANCELLED, actor=self.actor)
            await self._emit(session, task, ev.TaskCancelled(reason=message or "human"), run=run)
            await self._activity(session, claim.agent, ev.AgentIdle(reason="run_ended"), task, run)
        if self.on_task_failed:
            await self.on_task_failed(session, task)

    async def cancel(self, session: AsyncSession, task: Task, *, reason: str) -> None:
        """Cancel a task from outside a claim (operator, upstream failure, stage timeout)."""
        task = await session.scalar(select(Task).where(Task.id == task.id).with_for_update())
        if TASK_FSM.is_terminal(task.state):
            return
        # RUNNING, or suspended for approval (WAITING_APPROVAL / released READY): end the run.
        run = await session.scalar(
            select(AgentRun).where(
                AgentRun.task_id == task.id, AgentRun.state.notin_(AGENT_RUN_TERMINAL)
            )
        )
        if run is not None:
            await self._abort_run(session, run, "human", reason)
        # Agents waiting on this task (upstream, or a suspended run's own agent). The run's agent
        # is freed through the run below, so it is not "cleared" a second time.
        waiting_agents = [
            agent
            for agent in await self._agents_waiting_on(session, task)
            if run is None or agent.id != run.agent_id
        ]
        self._release(task)
        await TASK_FSM.transition(session, task, TaskState.CANCELLED, actor=self.actor)
        await self._emit(session, task, ev.TaskCancelled(reason=reason), run=run)
        if run is not None:
            agent = await session.get(Agent, run.agent_id)
            await self._activity(session, agent, ev.AgentIdle(reason="run_ended"), task, run)
        for agent in waiting_agents:
            await set_activity(
                session, agent, ev.AgentIdle(reason="waiting_cleared"), actor=self.actor
            )
        if self.on_task_failed:
            await self.on_task_failed(session, task)

    # --- approvals (used by autora.runtime.approvals) --------------------------------------

    async def suspend_for_approval(
        self, session: AsyncSession, claim: Claim, approval_id: uuid.UUID
    ) -> None:
        """A running agent needs a human decision: release the worker, keep the run open."""
        task = await self._locked_task(session, claim)
        run = await self._run_of(session, claim)
        for hop in AGENT_RUN_FSM.shortest_path(run.state, AgentRunState.WAITING_APPROVAL):
            await AGENT_RUN_FSM.transition(session, run, hop, actor=self.actor)
        self._release(task)
        await TASK_FSM.transition(session, task, TaskState.WAITING_APPROVAL, actor=self.actor)
        await self._emit(
            session, task, ev.TaskWaiting(reason="approval", approval_id=approval_id), run=run
        )
        await set_activity(
            session,
            claim.agent,
            ev.AgentWaiting(reason="approval", approval_id=approval_id, blocked_task_id=task.id),
            actor=self.actor,
            run_id=run.id,
            task_id=task.id,
            workflow_run_id=task.workflow_run_id,
            task_name=task.display_name,
        )

    async def await_approval(
        self, session: AsyncSession, task: Task, approval_id: uuid.UUID
    ) -> None:
        """A human task node (no agent run) starts waiting for its decision."""
        await TASK_FSM.transition(session, task, TaskState.WAITING_APPROVAL, actor=self.actor)
        await self._emit(session, task, ev.TaskWaiting(reason="approval", approval_id=approval_id))

    async def release_after_approval(self, session: AsyncSession, task: Task) -> None:
        """Approved: the task is claimable again; its suspended run resumes on the same attempt."""
        await TASK_FSM.transition(session, task, TaskState.READY, actor=self.actor)
        await self._emit(session, task, ev.TaskReady(required_role=task.required_role))

    async def complete_without_run(
        self,
        session: AsyncSession,
        task: Task,
        output: dict[str, Any] | None,
        *,
        output_summary: str | None = None,
    ) -> list[ev.UnlockedTask]:
        """Finish a human/service node that has no agent run (e.g. an approved approval node)."""
        task.output = output
        await TASK_FSM.transition(session, task, TaskState.SUCCEEDED, actor=self.actor)
        unlocked = list(await self.on_task_finished(session, task)) if self.on_task_finished else []
        await self._emit(
            session,
            task,
            ev.TaskSucceeded(run_id=None, output_ref=output_summary, unlocks=unlocked),
        )
        return unlocked

    # --- maintenance -----------------------------------------------------------------------

    async def release_blocked(self, session: AsyncSession, company_id: uuid.UUID) -> int:
        """BLOCKED_BUDGET -> READY for a company (after its budget was raised)."""
        tasks = (
            await session.scalars(
                select(Task)
                .where(Task.company_id == company_id, Task.state == TaskState.BLOCKED_BUDGET)
                .with_for_update(skip_locked=True)
            )
        ).all()
        for task in tasks:
            if task.attempt < task.max_attempts:
                await self.mark_ready(session, task)
                continue
            await TASK_FSM.transition(session, task, TaskState.FAILED, actor=self.actor)
            await self._emit(
                session, task, ev.TaskFailed(run_id=None, final=True, error_class="BudgetExhausted")
            )
            if self.on_task_failed:
                await self.on_task_failed(session, task)
        return len(tasks)

    async def reap_expired_leases(self, session: AsyncSession, limit: int = 100) -> list[uuid.UUID]:
        """Take back leases of crashed or stalled workers. Their run is aborted (timeout); the
        task is retried if it has attempts left, otherwise it fails for good."""
        now = self.clock()
        tasks = (
            await session.scalars(
                select(Task)
                .where(Task.state == TaskState.RUNNING, Task.lease_until < now)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).all()
        reaped = []
        for task in tasks:
            run = await session.scalar(
                select(AgentRun).where(
                    AgentRun.task_id == task.id, AgentRun.state.notin_(AGENT_RUN_TERMINAL)
                )
            )
            agent = await session.get(Agent, run.agent_id) if run else None
            expired = ev.AgentRunAborted(
                reason="timeout", message=f"lease expired at {task.lease_until.isoformat()}"
            )
            if run is not None:
                await self._abort_run(session, run, "timeout", expired.message)
            self._release(task)
            if task.attempt < task.max_attempts:
                await self._requeue(session, task, run, expired)
            else:
                await TASK_FSM.transition(session, task, TaskState.FAILED, actor=self.actor)
                final = ev.TaskFailed(
                    run_id=run.id if run else None, final=True, error_class="LeaseExpired"
                )
                await self._emit(session, task, final, run=run)
                if agent is not None:
                    await self._activity(session, agent, expired, task, run)
                if self.on_task_failed:
                    await self.on_task_failed(session, task)
            reaped.append(task.id)
        return reaped

    # --- internals -------------------------------------------------------------------------

    async def _requeue(
        self, session: AsyncSession, task: Task, run: AgentRun | None, ended: EventPayload
    ) -> None:
        """RUNNING -> READY with backoff, after a non-final failure or an expired lease."""
        task.available_at = self.clock() + self._backoff(task.attempt)
        await TASK_FSM.transition(session, task, TaskState.READY, actor=self.actor)
        error_class = getattr(ended, "error_class", "LeaseExpired")
        await self._emit(
            session, task,
            ev.TaskFailed(run_id=run.id if run else None, final=False, error_class=error_class),
            run=run,
        )  # fmt: skip
        await self._emit(session, task, ev.TaskReady(required_role=task.required_role))
        if run is not None:
            # The non-final failure/abort is trace data, not an activity change (the agent is free).
            await self._emit(session, task, ended, run=run)
            agent = await session.get(Agent, run.agent_id)
            await self._activity(session, agent, ev.AgentIdle(reason="run_ended"), task, run)

    async def _abort_run(
        self, session: AsyncSession, run: AgentRun, reason: str, message: str | None
    ) -> None:
        run.error = {"error_class": f"Aborted:{reason}", "message": (message or "")[:2000]}
        await self._finish_run(session, run, AgentRunState.ABORTED, self.clock())

    async def _finish_run(
        self, session: AsyncSession, run: AgentRun, target: AgentRunState, now: datetime
    ) -> None:
        """Move a run to a terminal state along the legal path.

        ``finished_at`` must be NULL on every non-terminal hop and set on the terminal one
        (DB: finished_iff_terminal), so it is assigned right before the last transition.
        """
        path = AGENT_RUN_FSM.shortest_path(run.state, target)
        for hop in path[:-1]:
            await AGENT_RUN_FSM.transition(session, run, hop, actor=self.actor)
        run.finished_at = now
        await AGENT_RUN_FSM.transition(session, run, path[-1], actor=self.actor)

    async def _activity(
        self,
        session: AsyncSession,
        agent: Agent,
        payload: EventPayload,
        task: Task,
        run: AgentRun | None,
    ) -> None:
        await set_activity(
            session,
            agent,
            payload,
            actor=self.actor,
            run_id=run.id if run else None,
            task_id=task.id,
            workflow_run_id=task.workflow_run_id,
            task_name=task.display_name,
        )

    async def _locked_task(self, session: AsyncSession, claim: Claim) -> Task:
        task = await session.scalar(select(Task).where(Task.id == claim.task.id).with_for_update())
        if task is None or task.state != TaskState.RUNNING or task.lease_token != claim.token:
            raise LeaseLost(f"claim on task {claim.task.id} is no longer valid")
        return task

    async def _run_of(self, session: AsyncSession, claim: Claim) -> AgentRun:
        run = await session.get(AgentRun, claim.run.id, with_for_update=True)
        if run is None or run.state in AGENT_RUN_TERMINAL:
            raise LeaseLost(f"run {claim.run.id} already finished")
        return run

    async def _open_run(self, session: AsyncSession, agent_id: uuid.UUID) -> AgentRun | None:
        return await session.scalar(
            select(AgentRun).where(
                AgentRun.agent_id == agent_id, AgentRun.state.notin_(AGENT_RUN_TERMINAL)
            )
        )

    async def _mark_idle_agents_waiting(self, session: AsyncSession, task: Task) -> None:
        upstream_roles = sorted(
            {
                role
                for role in await session.scalars(
                    select(Task.required_role).where(Task.id.in_(task.depends_on))
                )
            }
        )
        rows = await session.execute(
            select(Agent, AgentActivity)
            .join(AgentActivity, AgentActivity.agent_id == Agent.id)
            .where(Agent.company_id == task.company_id, Agent.role == task.required_role)
        )
        for agent, activity in rows:
            if effective_state(activity, self.clock()) is not ActivityState.IDLE:
                continue
            await set_activity(
                session,
                agent,
                ev.AgentWaiting(
                    reason="upstream", blocked_task_id=task.id, waiting_on_roles=upstream_roles
                ),
                actor=self.actor,
                task_id=task.id,
                workflow_run_id=task.workflow_run_id,
                task_name=task.display_name,
            )

    async def _agents_waiting_on(self, session: AsyncSession, task: Task) -> list[Agent]:
        rows = await session.execute(
            select(Agent, AgentActivity)
            .join(AgentActivity, AgentActivity.agent_id == Agent.id)
            .where(
                Agent.company_id == task.company_id,
                AgentActivity.state == ActivityState.WAITING.value,
            )
        )
        return [
            agent
            for agent, activity in rows
            if activity.detail.get("blocked_task_id") == str(task.id)
        ]

    def _backoff(self, attempt: int) -> timedelta:
        return min(self.retry_max, self.retry_base * (2 ** max(attempt - 1, 0)))

    @staticmethod
    def _release(task: Task) -> None:
        task.lease_owner = None
        task.lease_until = None
        task.lease_token = None

    async def _emit(
        self, session: AsyncSession, task: Task, payload: EventPayload, run: AgentRun | None = None
    ) -> None:
        await emit(
            session,
            new_event(
                payload,
                company_id=task.company_id,
                actor=self.actor,
                aggregate_type="agent_run"
                if run is not None and payload.event_type.startswith("AGENT_")
                else "task",
                aggregate_id=run.id
                if run is not None and payload.event_type.startswith("AGENT_")
                else task.id,
                agent_id=run.agent_id if run is not None else None,
                task_id=task.id,
                run_id=run.id if run is not None else None,
                workflow_run_id=task.workflow_run_id,
                cycle_id=task.cycle_id,
                correlation_id=task.workflow_run_id,
            ),
        )


def _ms(start: datetime, end: datetime) -> int:
    return max(0, int((end - start).total_seconds() * 1000))
