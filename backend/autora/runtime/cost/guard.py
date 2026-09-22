"""Cost guard: refuse a model call before it can exceed a hard budget (T-209).

Budget scopes, checked before every call (logs/platform/02_COMPANY_MODEL.md §5, gate 2 and 4):

| scope   | limit                                   | spend counted                        |
|---------|-----------------------------------------|--------------------------------------|
| run     | ``agents.budget.per_run_usd``           | model calls of this run              |
| task    | ``tasks.budget_usd``                    | model calls of this task (all runs)  |
| project | ``budgets`` rows of the project, hard   | project's calls in the period window |
| company | ``budgets`` rows with no project, hard  | company's calls in the period window |

Spend = recorded ``model_calls.cost_usd`` + open reservations (in-flight calls), all in USD.
``budgets`` are in the base currency (TWD, D-023) and are converted to USD before comparing;
run and task caps are meter figures and already USD. A call is
refused if spend + its estimated cost exceeds any limit; the refusal is recorded as a
BUDGET_EXHAUSTED event (committed even though the call does not happen) and raised as
``BudgetExceeded``. Soft caps (``hard_cap = false``) are not enforced here; reporting uses them.

Concurrency: reservation happens under a per-company transaction advisory lock, so two
concurrent calls cannot both fit under the same remaining budget.

Known limits of this version:
- The estimate is conservative: input tokens ≈ characters / 4, plus the full ``max_output_tokens``.
- Period windows are UTC calendar windows, except ``cycle``: that one runs from the open
  cycle's ``started_at`` (T-601), so a budget "per cycle" is spent against the cycle the work
  actually belongs to, not against the calendar day it happens to fall in. A company with no
  open cycle falls back to the day window.
- Only model cost is counted. Tool costs join when tool usage is recorded in the ledger.
"""

from __future__ import annotations

import json
import math
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from autora.db.models import (
    Agent,
    Budget,
    CostReservation,
    Cycle,
    CycleStage,
    ModelCall,
    ModelCallStatus,
    Task,
)
from autora.infra.money import METER_CURRENCY, Fx
from autora.runtime.actor import Actor
from autora.runtime.events import catalog as ev
from autora.runtime.events.outbox import emit
from autora.runtime.events.schema import new_event
from autora.runtime.models.router import ModelBinding
from autora.runtime.models.types import ModelRequest, Usage

_LOCK_NAMESPACE = 0x43_4F_53  # int4 namespace distinct from the event lock


class BudgetExceeded(Exception):
    def __init__(
        self,
        scope: str,
        limit: Decimal,
        spent: Decimal,
        requested: Decimal,
        *,
        project_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
    ):
        self.scope = scope
        self.limit = limit
        self.spent = spent
        self.requested = requested
        self.project_id = project_id
        self.task_id = task_id
        super().__init__(
            f"{scope} budget exceeded: limit ${limit}, spent ${spent}, call needs ~${requested}"
        )


@dataclass(frozen=True)
class Reservation:
    id: uuid.UUID
    amount: Decimal


@dataclass(frozen=True)
class _Limit:
    scope: str
    amount: Decimal
    window_start: datetime | None
    project_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None


def estimate_cost(
    request: ModelRequest, binding: ModelBinding, chars_per_token: int = 4
) -> Decimal:
    chars = len(request.system or "")
    chars += sum(len(m.model_dump_json()) for m in request.messages)
    chars += sum(len(json.dumps(t.model_dump())) for t in request.tools)
    usage = Usage(
        input_tokens=math.ceil(chars / chars_per_token),
        output_tokens=request.max_output_tokens,
    )
    return binding.price.cost(usage)


def _window_start(period: str, now: datetime, cycle_started_at: datetime | None = None) -> datetime:
    day = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "cycle":
        return cycle_started_at or day
    if period == "day":
        return day
    if period == "month":
        return day.replace(day=1)
    raise ValueError(f"unknown budget period {period!r}")


async def _open_cycle_start(session: AsyncSession, company_id: uuid.UUID) -> datetime | None:
    """When the company's open cycle began. None before the first one, or between cycles."""
    return await session.scalar(
        select(Cycle.started_at)
        .where(Cycle.company_id == company_id, Cycle.stage != CycleStage.DONE.value)
        .order_by(Cycle.seq.desc())
        .limit(1)
    )


@dataclass
class DbCostGuard:
    session_factory: async_sessionmaker[AsyncSession]
    reservation_ttl: timedelta = timedelta(minutes=15)
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    fx: Fx = field(default_factory=Fx.from_settings)

    async def reserve(self, request: ModelRequest, binding: ModelBinding) -> Reservation | None:
        ctx = request.context
        amount = estimate_cost(request, binding)
        now = self.clock()

        async with self.session_factory() as session:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(:ns, hashtext(:company))"),
                {"ns": _LOCK_NAMESPACE, "company": str(ctx.company_id)},
            )
            for limit in await self._limits(session, request, now):
                spent = await self._spent(session, ctx.company_id, limit, now)
                if spent + amount > limit.amount:
                    await self._record_exhausted(session, request, limit, spent, amount)
                    await session.commit()
                    raise BudgetExceeded(
                        limit.scope,
                        limit.amount,
                        spent,
                        amount,
                        project_id=limit.project_id,
                        task_id=limit.task_id,
                    )

            if amount == 0:
                await session.commit()
                return None
            reservation = CostReservation(
                company_id=ctx.company_id,
                project_id=ctx.project_id,
                task_id=ctx.task_id,
                run_id=ctx.run_id,
                amount=amount,
            )
            session.add(reservation)
            await session.commit()
            return Reservation(id=reservation.id, amount=amount)

    async def settle(
        self,
        session: AsyncSession,
        reservation: Reservation | None,
        cost: Decimal,
        model_call_id: uuid.UUID,
    ) -> None:
        if reservation is None:
            return
        await session.execute(
            update(CostReservation)
            .where(CostReservation.id == reservation.id)
            .values(settled_at=self.clock(), model_call_id=model_call_id, actual_cost=cost)
        )

    async def release(self, session: AsyncSession, reservation: Reservation | None) -> None:
        if reservation is None:
            return
        await session.execute(
            update(CostReservation)
            .where(CostReservation.id == reservation.id)
            .values(released_at=self.clock())
        )

    # --- internals -------------------------------------------------------------------------

    async def _limits(
        self, session: AsyncSession, request: ModelRequest, now: datetime
    ) -> list[_Limit]:
        ctx = request.context
        limits: list[_Limit] = []

        if ctx.agent_id and ctx.run_id:
            agent = await session.get(Agent, ctx.agent_id)
            per_run = (agent.budget or {}).get("per_run_usd") if agent else None
            if per_run is not None:
                limits.append(_Limit("run", Decimal(str(per_run)), None, run_id=ctx.run_id))

        if ctx.task_id:
            task = await session.get(Task, ctx.task_id)
            if task is not None and task.budget_usd is not None:
                limits.append(_Limit("task", task.budget_usd, None, task_id=ctx.task_id))

        budget_filter = [Budget.company_id == ctx.company_id, Budget.hard_cap.is_(True)]
        cycle_start = await _open_cycle_start(session, ctx.company_id)
        if ctx.project_id:
            for budget in await session.scalars(
                select(Budget).where(*budget_filter, Budget.project_id == ctx.project_id)
            ):
                limits.append(
                    _Limit(
                        "project",
                        self._metered(budget),
                        _window_start(budget.period, now, cycle_start),
                        project_id=ctx.project_id,
                    )
                )
        for budget in await session.scalars(
            select(Budget).where(*budget_filter, Budget.project_id.is_(None))
        ):
            limits.append(
                _Limit(
                    "company",
                    self._metered(budget),
                    _window_start(budget.period, now, cycle_start),
                )
            )
        return limits

    def _metered(self, budget: Budget) -> Decimal:
        """A budget is written in the base currency, spend is metered in USD (D-023). Compare
        like with like: the budget moves, because it is one number and the meter is many."""
        return self.fx.convert(budget.amount, source=budget.currency, target=METER_CURRENCY)

    async def _spent(
        self, session: AsyncSession, company_id: uuid.UUID, limit: _Limit, now: datetime
    ) -> Decimal:
        def scoped(model):
            conditions = [model.company_id == company_id]
            if limit.run_id is not None:
                conditions.append(model.run_id == limit.run_id)
            if limit.task_id is not None:
                conditions.append(model.task_id == limit.task_id)
            if limit.project_id is not None:
                conditions.append(model.project_id == limit.project_id)
            if limit.window_start is not None:
                conditions.append(model.created_at >= limit.window_start)
            return conditions

        recorded = await session.scalar(
            select(func.coalesce(func.sum(ModelCall.cost_usd), 0)).where(
                *scoped(ModelCall), ModelCall.status == ModelCallStatus.OK
            )
        )
        in_flight = await session.scalar(
            select(func.coalesce(func.sum(CostReservation.amount), 0)).where(
                *scoped(CostReservation),
                CostReservation.settled_at.is_(None),
                CostReservation.released_at.is_(None),
                CostReservation.created_at >= now - self.reservation_ttl,
            )
        )
        return Decimal(recorded) + Decimal(in_flight)

    async def _record_exhausted(
        self,
        session: AsyncSession,
        request: ModelRequest,
        limit: _Limit,
        spent: Decimal,
        requested: Decimal,
    ) -> None:
        ctx = request.context
        aggregate = {
            "run": ("agent_run", ctx.run_id),
            "task": ("task", ctx.task_id),
            "project": ("project", ctx.project_id),
            "company": ("company", ctx.company_id),
        }[limit.scope]
        await emit(
            session,
            new_event(
                ev.BudgetExhausted(
                    scope=limit.scope,
                    project_id=ctx.project_id,
                    task_id=ctx.task_id,
                    limit=limit.amount,
                    spent=spent,
                    requested=requested,
                ),
                company_id=ctx.company_id,
                actor=Actor.system("cost_guard"),
                aggregate_type=aggregate[0],
                aggregate_id=aggregate[1] or ctx.company_id,
                agent_id=ctx.agent_id,
                task_id=ctx.task_id,
                run_id=ctx.run_id,
            ),
        )
