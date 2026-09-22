"""The rules that run without asking anybody (platform/07 §5, platform/02 §4, T-606).

Governance is what the company does *deterministically*, between measuring a cycle and
reviewing it. It reads numbers that already exist and applies rules that were written down in
advance. **No model is called and none can be**: the whole point of these rules is that they
hold when the CEO is slow, wrong, absent, or arguing.

Three of them, in the order they run:

1. **Kill criteria.** A project or a business whose own stated criteria are breached is paused.
   Its criteria were fixed when it was approved, which is what makes this fair: the number to
   beat was chosen before anyone knew whether it would be met.
2. **An agent that keeps failing is paused.** Three final failures in a row and it stops being
   given work. An agent that cannot do its job is cheaper stopped than retried.
3. **Spending near a cap is announced.** The cost guard refuses at 100%; this says so at 80%,
   so the approach is visible before the wall.

**Pausing is not killing.** Governance can stop work; only a person can end a project or a
business, and the CEO can only propose it (platform/02 §4). That asymmetry is deliberate: a
rule that fires on a bad week should be able to stop the bleeding, and should not be able to
close a business.

Everything it does goes through the command pipeline like anything else, so a pause a rule
decided is recorded next to a pause a person decided, with the rule as its reason.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autora.company import events as company_events
from autora.company.commands import CommandBus
from autora.company.reporting import history, latest
from autora.db.models import (
    Agent,
    AgentRun,
    AgentRunState,
    AgentStatus,
    Budget,
    BusinessUnit,
    BusinessUnitState,
    Cycle,
    KpiScope,
    Project,
    ProjectState,
)
from autora.runtime.actor import Actor
from autora.runtime.events.outbox import emit
from autora.runtime.events.schema import new_event

log = logging.getLogger(__name__)

FAILURES_BEFORE_PAUSE = 3
"""Final failures in a row. Three, because two can be one bad input twice."""

NOT_THE_AGENT_S_FAULT = ("ProviderError", "LeaseExpired", "BudgetExhausted", "Aborted:")
"""Failures that say nothing about whether the agent can do its job (D-020).

The model provider timed out, the worker was killed and the lease expired, the company ran out
of budget, the task manager aborted the run. Counting these would mean **a bad afternoon at the
provider suspends the company's executive** — which is what the first real-model soak did on
2026-09-21: three 504s in a row and the CEO was paused for the rest of the week.

They are skipped, not counted as successes: three of the agent's own failures in a row still
pause it, however many outages happened in between. Matched by prefix, because an abort carries
its reason (``Aborted:timeout``)."""

WARN_AT = 0.8
"""Of a cap. The guard refuses at 1.0; this is the warning before the wall."""

OPS = {
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


@dataclass(frozen=True)
class Breach:
    """A criterion that was met, and the number that met it."""

    scope: str
    scope_id: uuid.UUID
    name: str
    metric: str
    op: str
    threshold: float
    observed: float
    cycles: int = 1

    def __str__(self) -> str:
        return f"{self.metric} is {self.observed:g}, which is {self.op} {self.threshold:g}" + (
            f" for {self.cycles} cycles" if self.cycles > 1 else ""
        )


@dataclass
class Governed:
    """What governance did this cycle — and what it looked at, which is the more common case."""

    paused_projects: list[uuid.UUID] = field(default_factory=list)
    paused_units: list[uuid.UUID] = field(default_factory=list)
    paused_agents: list[uuid.UUID] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    breaches: list[Breach] = field(default_factory=list)
    checked: dict[str, int] = field(default_factory=dict)
    """How many of each thing the rules were applied to. Zero everywhere is still a record."""

    def __bool__(self) -> bool:
        return bool(
            self.paused_projects or self.paused_units or self.paused_agents or self.warnings
        )

    def as_record(self) -> dict[str, Any]:
        """The cycle's ``governance`` column: small, flat, and true on a quiet day."""
        return {
            "checked": dict(self.checked),
            "paused_projects": [str(i) for i in self.paused_projects],
            "paused_business_units": [str(i) for i in self.paused_units],
            "paused_agents": [str(i) for i in self.paused_agents],
            "warnings": list(self.warnings),
            "breaches": [str(breach) for breach in self.breaches],
        }


@dataclass
class Governance:
    """Runs the rules. Does not commit, and never calls a model."""

    commands: CommandBus

    @property
    def actor(self) -> Actor:
        return Actor.system("governance")

    def stage_hook(self):
        """Registered on the way into REVIEWING, before the CEO's review: the CEO should read a
        snapshot in which the rules have already fired, not argue with them afterwards."""

        async def govern(session: AsyncSession, cycle: Cycle) -> None:
            await self.review(session, cycle)

        return govern

    async def review(self, session: AsyncSession, cycle: Cycle) -> Governed:
        done = Governed()
        await self._kill_criteria(session, cycle, done)
        await self._failing_agents(session, cycle, done)
        await self._near_the_cap(session, cycle, done)
        # written every cycle, fired or not: otherwise a quiet day and a day nobody governed
        # look exactly the same afterwards (AC-14)
        cycle.governance = done.as_record()
        await session.flush()
        return done

    # --- 1. what its own criteria said -------------------------------------------------------

    async def _kill_criteria(self, session: AsyncSession, cycle: Cycle, done: Governed) -> None:
        projects = (
            await session.scalars(
                select(Project).where(
                    Project.company_id == cycle.company_id,
                    Project.state == ProjectState.ACTIVE.value,
                )
            )
        ).all()
        done.checked["projects"] = len(projects)
        for project in projects:
            breach = await self._breached(
                session,
                cycle,
                criteria=project.kill_criteria,
                scope=KpiScope.PROJECT,
                scope_name="project",
                scope_id=project.id,
                project_id=project.id,
            )
            if breach is None:
                continue
            done.breaches.append(breach)
            if await self._pause_project(session, cycle, project, breach):
                done.paused_projects.append(project.id)

        units = (
            await session.scalars(
                select(BusinessUnit).where(
                    BusinessUnit.company_id == cycle.company_id,
                    BusinessUnit.state == BusinessUnitState.ACTIVE.value,
                )
            )
        ).all()
        done.checked["business_units"] = len(units)
        for unit in units:
            breach = await self._breached(
                session,
                cycle,
                criteria=unit.kill_criteria,
                scope=KpiScope.BUSINESS_UNIT,
                scope_name="business unit",
                scope_id=unit.id,
                business_unit_id=unit.id,
            )
            if breach is None:
                continue
            done.breaches.append(breach)
            unit.state = BusinessUnitState.PAUSED.value
            await session.flush()
            await emit(
                session,
                new_event(
                    company_events.BusinessUnitPaused(
                        key=unit.key,
                        name=unit.name,
                        reason=f"kill criteria: {breach}",
                        trigger="kill_criteria",
                    ),
                    company_id=cycle.company_id,
                    actor=self.actor,
                    aggregate_type="business_unit",
                    aggregate_id=unit.id,
                    cycle_id=cycle.id,
                ),
            )
            done.paused_units.append(unit.id)
            # its projects stop with it: a paused business is not one that keeps spending
            for project in projects:
                if project.business_unit_id == unit.id and project.id not in done.paused_projects:
                    if await self._pause_project(session, cycle, project, breach):
                        done.paused_projects.append(project.id)

    async def _breached(
        self,
        session: AsyncSession,
        cycle: Cycle,
        *,
        criteria: dict[str, Any] | None,
        scope: KpiScope,
        scope_name: str,
        scope_id: uuid.UUID,
        project_id: uuid.UUID | None = None,
        business_unit_id: uuid.UUID | None = None,
    ) -> Breach | None:
        """Whether this scope's own auto-pause criterion is met, by its own numbers."""
        rule = (criteria or {}).get("auto_pause_if")
        if not isinstance(rule, dict):
            return None
        after = (criteria or {}).get("evaluate_after_cycles")
        if isinstance(after, int) and cycle.seq < after:
            return None  # too early to judge: it was given that many cycles to work
        metric, op, threshold = rule.get("metric"), rule.get("op"), rule.get("value")
        if not metric or op not in OPS:
            log.warning("%s %s has an unreadable auto_pause_if: %r", scope_name, scope_id, rule)
            return None
        needed = int(rule.get("consecutive_cycles", 1) or 1)
        rows = await history(
            session,
            cycle.company_id,
            scope=scope,
            project_id=project_id,
            business_unit_id=business_unit_id,
            limit=needed,
        )
        values = [_number(row.metrics.get(metric)) for row in rows]
        if len(values) < needed or any(value is None for value in values):
            return None  # not measured (often enough) yet: silence is not a breach
        limit = _number(threshold)
        if limit is None or not all(OPS[op](value, limit) for value in values):
            return None
        return Breach(
            scope=scope_name,
            scope_id=scope_id,
            name=str(metric),
            metric=str(metric),
            op=str(op),
            threshold=limit,
            observed=values[0],
            cycles=needed,
        )

    async def _pause_project(
        self, session: AsyncSession, cycle: Cycle, project: Project, breach: Breach
    ) -> bool:
        if project.state != ProjectState.ACTIVE.value:
            return False
        result = await self.commands.submit(
            session,
            "PauseProject",
            {"project_id": str(project.id), "reason": f"kill criteria: {breach}"},
            company_id=cycle.company_id,
            actor=self.actor,
            idempotency_key=f"cycle:{cycle.id}:autopause:project:{project.id}",
        )
        return result.done

    # --- 2. an agent that keeps failing --------------------------------------------------------

    async def _failing_agents(self, session: AsyncSession, cycle: Cycle, done: Governed) -> None:
        agents = (
            await session.scalars(
                select(Agent).where(
                    Agent.company_id == cycle.company_id,
                    Agent.status == AgentStatus.ACTIVE.value,
                )
            )
        ).all()
        done.checked["agents"] = len(agents)
        for agent in agents:
            judged = await self._runs_that_judge(session, agent)
            if len(judged) < FAILURES_BEFORE_PAUSE:
                continue
            if any(state != AgentRunState.FAILED.value for state in judged):
                continue
            result = await self.commands.submit(
                session,
                "PauseAgent",
                {
                    "agent_id": str(agent.id),
                    "reason": f"{FAILURES_BEFORE_PAUSE} runs in a row failed",
                },
                company_id=cycle.company_id,
                actor=self.actor,
                idempotency_key=f"cycle:{cycle.id}:autopause:agent:{agent.id}",
            )
            if result.done:
                done.paused_agents.append(agent.id)

    async def _runs_that_judge(self, session: AsyncSession, agent: Agent) -> list[str]:
        """The agent's most recent runs, with the ones that judge nothing left out.

        Reads a few more than it needs and drops the outages, so a streak of the agent's own
        failures is still found underneath them (D-020).
        """
        rows = (
            await session.execute(
                select(AgentRun.state, AgentRun.error)
                .where(AgentRun.agent_id == agent.id)
                .order_by(AgentRun.created_at.desc())
                .limit(FAILURES_BEFORE_PAUSE * 4)
            )
        ).all()
        judged = []
        for state, error in rows:
            if state == AgentRunState.FAILED.value and _is_infrastructure(error):
                continue
            judged.append(state)
            if len(judged) == FAILURES_BEFORE_PAUSE:
                break
        return judged

    # --- 3. spending near a cap ------------------------------------------------------------------

    async def _near_the_cap(self, session: AsyncSession, cycle: Cycle, done: Governed) -> None:
        from autora.company.ledger import Ledger

        budgets = (
            await session.scalars(
                select(Budget).where(
                    Budget.company_id == cycle.company_id, Budget.hard_cap.is_(True)
                )
            )
        ).all()
        ledger = Ledger()
        done.checked["budgets"] = len(budgets)
        for budget in budgets:
            if budget.amount <= 0:
                continue
            spent = await self._spent_for(session, cycle, budget, ledger)
            ratio = float(spent / budget.amount)
            if ratio < WARN_AT or ratio >= 1:
                continue  # under the warning line, or already at the wall the guard defends
            await emit(
                session,
                new_event(
                    company_events.BudgetWarning(
                        budget_id=budget.id,
                        project_id=budget.project_id,
                        business_unit_id=budget.business_unit_id,
                        period=budget.period,
                        spent=spent,
                        cap=budget.amount,
                        ratio=round(ratio, 4),
                    ),
                    company_id=cycle.company_id,
                    actor=self.actor,
                    aggregate_type="budget",
                    aggregate_id=budget.id,
                    cycle_id=cycle.id,
                ),
            )
            done.warnings.append(f"{int(ratio * 100)}% of a {budget.period} budget is spent")

    async def _spent_for(
        self, session: AsyncSession, cycle: Cycle, budget: Budget, ledger
    ) -> Decimal:
        """What this envelope has cost in its own window (the cycle's, for a cycle budget)."""
        since = cycle.started_at if budget.period == "cycle" else None
        measured = await latest(
            session,
            cycle.company_id,
            scope=KpiScope.PROJECT if budget.project_id else KpiScope.BUSINESS_UNIT,
            project_id=budget.project_id,
            business_unit_id=budget.business_unit_id,
        )
        if measured is not None and measured.cycle_id == cycle.id:
            cost = _number(measured.metrics.get("cost"))
            if cost is not None:
                return Decimal(str(cost))
        return await ledger.spent(
            session,
            cycle.company_id,
            project_id=budget.project_id,
            since=since,
        )


def _is_infrastructure(error: dict[str, Any] | None) -> bool:
    """Did this run fail for a reason that is not the agent's? (D-020)"""
    name = str((error or {}).get("error_class") or "")
    return any(name.startswith(prefix) for prefix in NOT_THE_AGENT_S_FAULT)


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(Decimal(str(value)))
    except (InvalidOperation, TypeError, ValueError):
        return None
