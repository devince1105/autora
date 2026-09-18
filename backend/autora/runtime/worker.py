"""The worker loop: claims tasks for agents and runs them; keeps the runtime healthy (T-213).

One worker process runs this loop. Several processes can run side by side: every claim is a
``FOR UPDATE SKIP LOCKED`` on the task queue and every agent has at most one open run (DB
index), so they never step on each other.

Each tick:
- **maintenance** (every ``maintenance_interval``): reclaim expired leases (crashed or stalled
  workers), expire overdue approvals, fire due schedules. Each job commits on its own; one
  failing does not stop the others or the loop.
- **dispatch**: for every active agent whose role has a behavior and that this worker is not
  already running, try to claim its next task (or resume its approved run) and start the run as
  an asyncio task, up to ``concurrency`` runs at once.

There is no event dispatcher yet: nothing in Phase 2 reacts to events asynchronously (workflow
propagation happens inside the task transaction). It arrives with the first event handler.

Shutdown (``stop`` set): no new claims; in-flight runs get ``grace`` seconds to finish, then are
cancelled. Their leases expire and another worker re-runs them, so a hard kill is also safe.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from autora.db.models import ActivityState, Agent, AgentActivity, AgentStatus
from autora.runtime.agent_runner import AgentRunner, RunOutcome
from autora.runtime.approvals import ApprovalService
from autora.runtime.scheduler import Scheduler
from autora.runtime.task_manager import AgentBusy, Claim, TaskManager

log = logging.getLogger("autora.worker")


@dataclass
class Worker:
    worker_id: str
    session_factory: async_sessionmaker[AsyncSession]
    task_manager: TaskManager
    runner: AgentRunner
    approvals: ApprovalService
    scheduler: Scheduler | None = None
    concurrency: int = 4
    poll_interval: float = 1.0
    maintenance_interval: float = 15.0
    grace: float = 30.0
    company_ids: frozenset[uuid.UUID] | None = None
    """Only run agents of these companies (None: all). Lets tests and shards share a database."""
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    on_outcome: Callable[[RunOutcome], Awaitable[None]] | None = None
    _running: dict[uuid.UUID, asyncio.Task[RunOutcome | None]] = field(default_factory=dict)
    _last_maintenance: datetime | None = None

    # --- loop ------------------------------------------------------------------------------

    async def run_forever(self, stop: asyncio.Event) -> None:
        log.info("worker %s started (concurrency=%d)", self.worker_id, self.concurrency)
        while not stop.is_set():
            try:
                await self.tick()
            except Exception:  # noqa: BLE001 - a transient DB error must not end the process
                log.exception("worker tick failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.poll_interval)
            except TimeoutError:
                pass
        await self.shutdown()

    async def tick(self) -> int:
        """One pass: maintenance if due, then claim and start runs. Returns runs started."""
        now = self.clock()
        if self._last_maintenance is None or now - self._last_maintenance >= timedelta(
            seconds=self.maintenance_interval
        ):
            await self.maintain()
            self._last_maintenance = now
        return await self.dispatch()

    async def run_until_idle(self, max_ticks: int = 1000) -> None:
        """Tick until nothing is running and nothing can be claimed (tests, one-off drains)."""
        for _ in range(max_ticks):
            started = await self.tick()
            if not self._running:
                if started == 0:
                    return
                continue
            await asyncio.wait(list(self._running.values()), return_when=asyncio.FIRST_COMPLETED)
        raise RuntimeError(f"worker not idle after {max_ticks} ticks")

    async def shutdown(self) -> None:
        if not self._running:
            return
        log.info("waiting up to %.0fs for %d run(s)", self.grace, len(self._running))
        _, pending = await asyncio.wait(list(self._running.values()), timeout=self.grace)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    # --- maintenance -----------------------------------------------------------------------

    async def maintain(self) -> None:
        async def reap(session: AsyncSession) -> None:
            reaped = await self.task_manager.reap_expired_leases(session)
            if reaped:
                log.warning("reclaimed %d expired lease(s): %s", len(reaped), reaped)

        async def expire(session: AsyncSession) -> None:
            expired = await self.approvals.expire_due(session)
            if expired:
                log.info("expired %d approval(s)", len(expired))

        for name, job in (("reap_leases", reap), ("expire_approvals", expire)):
            try:
                async with self.session_factory() as session:
                    await job(session)
                    await session.commit()
            except Exception:  # noqa: BLE001
                log.exception("maintenance job %s failed", name)
        if self.scheduler is not None:
            try:
                await self.scheduler.tick()
            except Exception:  # noqa: BLE001
                log.exception("scheduler tick failed")

    # --- dispatch --------------------------------------------------------------------------

    async def dispatch(self) -> int:
        started = 0
        for agent in await self._candidates():
            if len(self._running) >= self.concurrency:
                break
            claim = await self._claim(agent)
            if claim is None:
                continue
            self._running[agent.id] = asyncio.create_task(
                self._run(claim), name=f"run-{claim.run.id}"
            )
            started += 1
        return started

    async def _candidates(self) -> list[Agent]:
        roles = self.runner.behaviors.roles()
        if not roles:
            return []
        async with self.session_factory() as session:
            agents = (
                await session.scalars(
                    select(Agent)
                    .join(AgentActivity, AgentActivity.agent_id == Agent.id)
                    .where(
                        Agent.status == AgentStatus.ACTIVE,
                        Agent.role.in_(roles),
                        AgentActivity.state != ActivityState.PAUSED,
                        *(
                            [Agent.company_id.in_(self.company_ids)]
                            if self.company_ids is not None
                            else []
                        ),
                    )
                    .order_by(Agent.created_at)
                )
            ).all()
        return [a for a in agents if a.id not in self._running]

    async def _claim(self, agent: Agent) -> Claim | None:
        try:
            async with self.session_factory() as session:
                claim = await self.task_manager.claim_next(session, agent, self.worker_id)
                await session.commit()
        except AgentBusy:
            return None  # its run is waiting for approval, or another worker runs it
        return claim

    async def _run(self, claim: Claim) -> RunOutcome | None:
        try:
            outcome = await self.runner.run(claim)
            log.info(
                "run %s (%s, task %s attempt %d): %s%s",
                claim.run.id,
                claim.agent.role,
                claim.task.name,
                claim.task.attempt,
                outcome.status,
                f" {outcome.error_class}: {outcome.message}" if outcome.error_class else "",
            )
            if self.on_outcome is not None:
                await self.on_outcome(outcome)
            return outcome
        except Exception:  # noqa: BLE001 - the runner already handles work failures
            log.exception("run %s crashed", claim.run.id)
            return None
        finally:
            self._running.pop(claim.agent.id, None)
