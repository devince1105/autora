"""Minimal KPI reporting for the dashboard (T-314). Phase 6's Reporting replaces it.

Computed on read with plain SQL, never by an LLM (platform/02). "Today" is the UTC day.

- **cash** = capital in + revenue - expenses - capital out (transfers move money inside the
  company and are ignored) - every model call's cost.
- **expenses_today** = today's expense transactions + today's model call costs.
- **model_cost_today** = today's model call costs (included in expenses_today).
- **revenue_today** = today's revenue transactions.
- **published_today** = today's ARTICLE_PUBLISHED events (T-512); counted from events, so the
  company layer needs nothing from the newsroom domain.
- **goal**: the active cycle goal due soonest (or the latest active one without a deadline).

Model costs are counted once, from ``model_calls``, the source of truth for model spend
(T-207). Until the ledger posts them (Phase 6, cycle MEASURING) they are not transactions; once
it does, the ``model_cost`` transactions are skipped here so nothing is counted twice.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from autora.db.models import (
    CompanyGoal,
    EventRecord,
    GoalLevel,
    GoalStatus,
    ModelCall,
    Transaction,
    TransactionKind,
)

MODEL_COST_CATEGORY = "model_cost"
PUBLISHED_EVENT = "ARTICLE_PUBLISHED"
ZERO = Decimal(0)


class GoalView(BaseModel):
    id: uuid.UUID
    title: str
    metric: str
    target: Decimal
    current: Decimal | None
    deadline: datetime | None


class Kpis(BaseModel):
    as_of: datetime
    currency: str = "USD"
    cash: Decimal
    revenue_today: Decimal
    expenses_today: Decimal
    model_cost_today: Decimal
    published_today: int | None = None
    """Articles published today (None only from older API versions)."""
    goal: GoalView | None = None


def day_start(now: datetime) -> datetime:
    return now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


async def load_kpis(
    session: AsyncSession, company_id: uuid.UUID, *, now: datetime | None = None
) -> Kpis:
    now = now or datetime.now(UTC)
    today = day_start(now)
    tomorrow = today + timedelta(days=1)

    ledger = Transaction.category != MODEL_COST_CATEGORY
    signed = case(
        (
            Transaction.kind.in_([TransactionKind.CAPITAL_IN, TransactionKind.REVENUE]),
            Transaction.amount,
        ),
        (
            Transaction.kind.in_([TransactionKind.CAPITAL_OUT, TransactionKind.EXPENSE]),
            -Transaction.amount,
        ),
        else_=ZERO,
    )
    is_today = (Transaction.occurred_at >= today) & (Transaction.occurred_at < tomorrow)
    balance, revenue, expenses = (
        await session.execute(
            select(
                func.coalesce(func.sum(signed), ZERO),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                (Transaction.kind == TransactionKind.REVENUE) & is_today,
                                Transaction.amount,
                            ),
                            else_=ZERO,
                        )
                    ),
                    ZERO,
                ),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                (Transaction.kind == TransactionKind.EXPENSE) & is_today,
                                Transaction.amount,
                            ),
                            else_=ZERO,
                        )
                    ),
                    ZERO,
                ),
            ).where(Transaction.company_id == company_id, Transaction.occurred_at <= now, ledger)
        )
    ).one()

    model_total, model_today = (
        await session.execute(
            select(
                func.coalesce(func.sum(ModelCall.cost_usd), ZERO),
                func.coalesce(
                    func.sum(case((ModelCall.created_at >= today, ModelCall.cost_usd), else_=ZERO)),
                    ZERO,
                ),
            ).where(ModelCall.company_id == company_id, ModelCall.created_at <= now)
        )
    ).one()

    published_today = await session.scalar(
        select(func.count())
        .select_from(EventRecord)
        .where(
            EventRecord.company_id == company_id,
            EventRecord.event_type == PUBLISHED_EVENT,
            EventRecord.occurred_at >= today,
            EventRecord.occurred_at <= now,
        )
    )

    goal = await session.scalar(
        select(CompanyGoal)
        .where(
            CompanyGoal.company_id == company_id,
            CompanyGoal.level == GoalLevel.CYCLE,
            CompanyGoal.status == GoalStatus.ACTIVE,
        )
        .order_by(CompanyGoal.deadline.asc().nulls_last(), CompanyGoal.created_at.desc())
        .limit(1)
    )

    return Kpis(
        as_of=now,
        cash=balance - model_total,
        revenue_today=revenue,
        expenses_today=expenses + model_today,
        model_cost_today=model_today,
        published_today=int(published_today or 0),
        goal=GoalView.model_validate(goal, from_attributes=True) if goal else None,
    )
