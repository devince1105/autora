"""The company's operating loop: one cycle a day, five stages (platform/02 §2, T-601).

    PLANNING -> EXECUTING -> MEASURING -> REVIEWING -> DONE

The cycle is the **only root that starts agent work**. Nothing else creates a workflow on its
own: an agent finishing its task never makes new work for itself (anti-runaway gate 1). So the
loop is what makes the company autonomous, and the two properties below are what keep it from
running away.

**It cannot hang.** Every stage carries a deadline. When the deadline passes the stage is left
anyway and what was still unfinished is recorded in ``CYCLE_STAGE_TIMEOUT`` (gate 6). A stage
is also left early, before its deadline, once its work is done — EXECUTING ends when the last
workflow the plan started is finished.

**It cannot be pushed.** Advancing is not triggered by an event a handler could emit at will:
the runner is polled (by the worker's maintenance loop and by the daily schedule), reads the
state of the world, and decides. There is no event dispatcher in the runtime yet, so this is
also the only shape that works today.

What this module does *not* decide: what the plan contains and what the review concludes. Those
come from the CEO agent (T-605) through the hooks below, and until it exists a cycle simply
plans nothing and completes at once — a company with no CEO still keeps time, which is what the
fallback path does when the CEO fails (``CYCLE_PLAN_FALLBACK``, T-607).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from autora.company import events as company_events
from autora.db.models import (
    Company,
    CompanyStatus,
    Cycle,
    CycleStage,
    Schedule,
    Task,
    WorkflowRun,
)
from autora.db.repositories import companies as company_repo
from autora.runtime.actor import Actor
from autora.runtime.events.outbox import emit
from autora.runtime.events.schema import new_event
from autora.runtime.fsm import StateMachine, transitions
from autora.runtime.lifecycles import TASK_FSM, WORKFLOW_RUN_FSM
from autora.runtime.scheduler import create_schedule

log = logging.getLogger(__name__)

S = CycleStage
CYCLE_FSM = StateMachine(
    entity_type="cycle",
    states=CycleStage,
    initial=S.PLANNING,
    state_attr="stage",
    transitions=transitions(
        {
            S.PLANNING: [S.EXECUTING],
            S.EXECUTING: [S.MEASURING],
            S.MEASURING: [S.REVIEWING],
            S.REVIEWING: [S.DONE],
        }
    ),
)
"""Strictly forward, no branches: a cycle that starts always ends, in order.

A stage that fails does not send the cycle backwards or sideways — the failure is recorded and
the cycle carries on (platform/02: a failed review still reaches DONE and the next snapshot
says the review is missing)."""

STAGE_ORDER = (S.PLANNING, S.EXECUTING, S.MEASURING, S.REVIEWING, S.DONE)
OPEN_STAGES = tuple(stage for stage in STAGE_ORDER if stage is not S.DONE)

DEFAULT_STAGE_MINUTES = {
    S.PLANNING: 60,
    S.EXECUTING: 13 * 60,
    S.MEASURING: 60,
    S.REVIEWING: 60,
}
"""How long a stage may take before it is advanced anyway.

Read as a day that starts at 06:00 (the scheduled cycle start): planning until 07:00, the work
of the day until 20:00 — the execution deadline platform/02 names — then measuring and the
review. A company may shorten these with the policy ``company.cycle_stage_minutes``
(``{"EXECUTING": 30}``); it is minutes per stage, and stages left out keep the default."""

STAGE_MINUTES_POLICY = "company.cycle_stage_minutes"
CYCLE_START_SCHEDULE = "company.cycle_start"
"""Schedule handler key: fires once a day and starts the cycle."""
CYCLE_START_CRON = "0 6 * * *"
"""06:00 in the company's timezone (platform/02 §2). A company may run it at another hour by
editing its schedule row; the runner does not care when it is called."""

Clock = Callable[[], datetime]
StageHook = Callable[[AsyncSession, Cycle], Awaitable[None]]
"""Runs when a stage is entered, inside the advancing transaction. Raising leaves the cycle in
the stage it was in, so the next tick tries again — hooks must be idempotent."""
StageCheck = Callable[[AsyncSession, Cycle], Awaitable[bool]]
"""Is this stage's work finished? False keeps the cycle there until the deadline passes.

A stage with no check registered is finished the moment it is entered, because everything it
had to do ran in its ``on_enter`` hooks, inside that transaction. A stage whose work is a task
some agent has to pick up (PLANNING waiting for the CEO, EXECUTING waiting for the day's
workflows) registers a check and is held there until the check passes or the deadline does."""


class CycleError(Exception):
    pass


class CycleAlreadyOpen(CycleError):
    def __init__(self, cycle: Cycle):
        self.cycle = cycle
        super().__init__(f"cycle {cycle.seq} is still open in {cycle.stage}")


async def ensure_cycle_schedule(
    session: AsyncSession,
    company_id: uuid.UUID,
    *,
    cron: str = CYCLE_START_CRON,
    timezone: str = "UTC",
    now: datetime | None = None,
) -> Schedule:
    """Give a company its daily cycle, or return the one it already has.

    This is what makes a company autonomous (AC-11): from here on nobody has to start anything
    — the schedule opens a cycle, the cycle plans, and the plan starts the day's work.
    """
    existing = await session.scalar(
        select(Schedule).where(
            Schedule.company_id == company_id, Schedule.name == CYCLE_START_SCHEDULE
        )
    )
    return existing or await create_schedule(
        session,
        company_id=company_id,
        name=CYCLE_START_SCHEDULE,
        cron=cron,
        handler=CYCLE_START_SCHEDULE,
        timezone=timezone,
        now=now,
    )


@dataclass
class CycleRunner:
    """Keeps every company's cycle moving. One instance per worker.

    ``start`` is called by the daily schedule; ``tick`` by the worker's maintenance loop, which
    is also what advances a stage whose deadline has passed. Both take the row ``FOR UPDATE``,
    so two workers cannot advance the same cycle twice.
    """

    clock: Clock = field(default=lambda: datetime.now(UTC))
    on_enter: dict[CycleStage, list[StageHook]] = field(default_factory=dict)
    is_done: dict[CycleStage, StageCheck] = field(default_factory=dict)

    @property
    def actor(self) -> Actor:
        return Actor.system("cycle-runner")

    def when_entering(self, stage: CycleStage, hook: StageHook) -> None:
        """Register work to do on entering ``stage`` (PLANNING: the CEO plans; MEASURING: the
        ledger settles and reporting writes the KPI snapshot; REVIEWING: the CEO reviews)."""
        self.on_enter.setdefault(stage, []).append(hook)

    def finishes_when(self, stage: CycleStage, check: StageCheck) -> None:
        """Let ``stage`` end early, as soon as ``check`` says its work is done."""
        if stage in self.is_done:
            raise CycleError(f"{stage} already has a completion check")
        self.is_done[stage] = check

    # --- starting ---------------------------------------------------------------------------

    async def start(self, session: AsyncSession, company_id: uuid.UUID) -> Cycle:
        """Open the company's next cycle in PLANNING. Does not commit.

        Raises ``CycleAlreadyOpen`` when the previous one has not reached DONE: a company runs
        one cycle at a time, and a stuck cycle must be seen, not silently lapped.
        """
        open_cycle = await self.open_cycle(session, company_id, lock=True)
        if open_cycle is not None:
            raise CycleAlreadyOpen(open_cycle)
        now = self.clock()
        last_seq = await session.scalar(
            select(func.max(Cycle.seq)).where(Cycle.company_id == company_id)
        )
        stage_minutes = await self._stage_minutes(session, company_id)
        cycle = Cycle(
            company_id=company_id,
            seq=(last_seq or 0) + 1,
            stage=S.PLANNING.value,
            started_at=now,
            stage_deadline=now + timedelta(minutes=stage_minutes[S.PLANNING]),
        )
        session.add(cycle)
        await session.flush()
        await emit(
            session,
            new_event(
                company_events.CycleStarted(
                    seq=cycle.seq, stage=S.PLANNING, deadline=cycle.stage_deadline
                ),
                company_id=company_id,
                actor=self.actor,
                aggregate_type="cycle",
                aggregate_id=cycle.id,
                cycle_id=cycle.id,
            ),
        )
        await self._enter(session, cycle)
        return cycle

    def schedule_handler(self):
        """The daily schedule's handler (registered under ``CYCLE_START_SCHEDULE``).

        A cycle that is still open is not an error here: the schedule fired while yesterday's
        work is unfinished, which the deadlines will resolve. It is logged and skipped so the
        schedule keeps its cadence.
        """

        async def handler(session: AsyncSession, schedule, scheduled_for: datetime) -> None:
            try:
                await self.start(session, schedule.company_id)
            except CycleAlreadyOpen as still_open:
                log.warning("company %s: %s, not starting another", schedule.company_id, still_open)

        return handler

    # --- advancing --------------------------------------------------------------------------

    async def tick(self, session: AsyncSession, company_id: uuid.UUID) -> Cycle | None:
        """Advance the company's open cycle as far as it can go right now. Does not commit.

        Returns the cycle it moved (in its new stage), or None when there is nothing open or
        the current stage is neither finished nor past its deadline.
        """
        cycle = await self.open_cycle(session, company_id, lock=True)
        if cycle is None:
            return None
        moved = False
        while cycle.stage != S.DONE:
            overdue = await self._overdue(session, cycle)
            if not overdue and not await self._finished(session, cycle):
                break
            await self._advance(session, cycle, timed_out=overdue)
            moved = True
        return cycle if moved else None

    async def _advance(self, session: AsyncSession, cycle: Cycle, *, timed_out: bool) -> None:
        was = CycleStage(cycle.stage)
        nxt = STAGE_ORDER[STAGE_ORDER.index(was) + 1]
        if timed_out:
            await self._record_timeout(session, cycle, was)
        now = self.clock()
        if nxt is S.DONE:
            cycle.ended_at = now
        await CYCLE_FSM.transition(
            session,
            cycle,
            nxt,
            actor=self.actor,
            reason="deadline passed" if timed_out else None,
        )
        stage_minutes = await self._stage_minutes(session, cycle.company_id)
        cycle.stage_deadline = (
            None if nxt is S.DONE else now + timedelta(minutes=stage_minutes[nxt])
        )
        await emit(
            session,
            new_event(
                company_events.CycleStageChanged(
                    from_stage=was, to_stage=nxt, deadline=cycle.stage_deadline
                ),
                company_id=cycle.company_id,
                actor=self.actor,
                aggregate_type="cycle",
                aggregate_id=cycle.id,
                cycle_id=cycle.id,
            ),
        )
        if nxt is S.DONE:
            await emit(
                session,
                new_event(
                    company_events.CycleCompleted(seq=cycle.seq),
                    company_id=cycle.company_id,
                    actor=self.actor,
                    aggregate_type="cycle",
                    aggregate_id=cycle.id,
                    cycle_id=cycle.id,
                ),
            )
        await self._enter(session, cycle)

    async def _record_timeout(self, session: AsyncSession, cycle: Cycle, stage: CycleStage) -> None:
        """Say what was left behind. The tasks are not cancelled: work already running keeps
        its lease and finishes into the next stage, and the event is what makes that visible."""
        unfinished = (
            await session.scalars(
                select(Task.id).where(
                    Task.cycle_id == cycle.id,
                    Task.state.not_in([s.value for s in TASK_FSM.terminal_states()]),
                )
            )
        ).all()
        await emit(
            session,
            new_event(
                company_events.CycleStageTimeout(stage=stage, cancelled_task_ids=list(unfinished)),
                company_id=cycle.company_id,
                actor=self.actor,
                aggregate_type="cycle",
                aggregate_id=cycle.id,
                cycle_id=cycle.id,
            ),
        )

    async def _enter(self, session: AsyncSession, cycle: Cycle) -> None:
        for hook in self.on_enter.get(CycleStage(cycle.stage), ()):
            await hook(session, cycle)

    # --- reading ----------------------------------------------------------------------------

    async def open_cycle(
        self, session: AsyncSession, company_id: uuid.UUID, *, lock: bool = False
    ) -> Cycle | None:
        stmt = (
            select(Cycle)
            .where(Cycle.company_id == company_id, Cycle.stage != S.DONE.value)
            .order_by(Cycle.seq.desc())
            .limit(1)
        )
        if lock:
            stmt = stmt.with_for_update()
        return await session.scalar(stmt)

    async def _overdue(self, session: AsyncSession, cycle: Cycle) -> bool:
        return cycle.stage_deadline is not None and self.clock() >= cycle.stage_deadline

    async def _finished(self, session: AsyncSession, cycle: Cycle) -> bool:
        check = self.is_done.get(CycleStage(cycle.stage))
        return True if check is None else await check(session, cycle)

    async def _stage_minutes(
        self, session: AsyncSession, company_id: uuid.UUID
    ) -> dict[CycleStage, int]:
        override = await company_repo.get_policy(session, company_id, STAGE_MINUTES_POLICY) or {}
        minutes = dict(DEFAULT_STAGE_MINUTES)
        for stage, value in override.items():
            try:
                minutes[CycleStage(stage)] = int(value)
            except (ValueError, TypeError):
                log.warning("company %s: ignoring %s=%r", company_id, stage, value)
        return minutes


async def work_is_finished(session: AsyncSession, cycle: Cycle) -> bool:
    """EXECUTING is over when every workflow this cycle started has finished.

    A cycle that started nothing (no CEO yet, or a plan with no workflows) is finished at once,
    which is what lets an empty company still complete its cycles.
    """
    open_runs = await session.scalar(
        select(func.count())
        .select_from(WorkflowRun)
        .where(
            WorkflowRun.cycle_id == cycle.id,
            WorkflowRun.state.not_in([s.value for s in WORKFLOW_RUN_FSM.terminal_states()]),
        )
    )
    return not open_runs


def maintenance_job(
    runner: CycleRunner, company_ids: frozenset[uuid.UUID] | None = None
) -> Callable[[AsyncSession], Awaitable[None]]:
    """The worker's periodic job that moves every active company's cycle along.

    Each company advances in its own savepoint, so one company whose stage hook fails does not
    hold up the others; the failure is logged and retried on the next tick.
    """

    async def job(session: AsyncSession) -> None:
        stmt = select(Company.id).where(Company.status == CompanyStatus.ACTIVE.value)
        if company_ids is not None:
            stmt = stmt.where(Company.id.in_(company_ids))
        for company_id in (await session.scalars(stmt)).all():
            try:
                async with session.begin_nested():
                    moved = await runner.tick(session, company_id)
            except Exception:  # noqa: BLE001 - one company's stage hook must not stop the rest
                log.exception("advancing the cycle of company %s failed", company_id)
                continue
            if moved is not None:
                log.info("company %s: cycle %d now %s", company_id, moved.seq, moved.stage)

    return job
