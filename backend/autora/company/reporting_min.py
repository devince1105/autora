"""Minimal KPI reporting for the dashboard (T-314). Phase 6's Reporting replaces it.

Computed on read with plain SQL, never by an LLM (platform/02). "Today" is the UTC day.

- **cash** = capital in + revenue - expenses - capital out (transfers move money inside the
  company and are ignored) - every model call's cost.
- **expenses_today** = today's expense transactions + today's model call costs.
- **model_cost_today** = today's model call costs (included in expenses_today).
- **revenue_today** = today's revenue transactions.
- **domain_metrics** = whatever the registered domains counted for today, under their own names
  (``newsroom.published_articles``). The company layer does not know what any of them mean —
  that is the point: it stores numbers, domains define them (T-603).
- **goal**: the active cycle goal due soonest (or the latest active one without a deadline).
- **revenue** (T-707): the last 30 days — the money per day, who joined, who renewed, who lapsed —
  and where the memberships stand now: members, and the ones whose access runs out within 30
  days. The same numbers the cycle's report carries, from ``company.revenue``.

Model costs are counted once, from ``model_calls``, the source of truth for model spend
(T-207). Until the ledger posts them (Phase 6, cycle MEASURING) they are not transactions; once
it does, the ``model_cost`` transactions are skipped here so nothing is counted twice.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from autora.company import memberships
from autora.company import revenue as membership_revenue
from autora.company.reporting import Reporting, Window
from autora.db.models import (
    CompanyGoal,
    GoalLevel,
    GoalStatus,
    ModelCall,
    Transaction,
    TransactionKind,
)
from autora.infra.money import Fx

MODEL_COST_CATEGORY = "model_cost"
ZERO = Decimal(0)


class GoalView(BaseModel):
    id: uuid.UUID
    title: str
    metric: str
    target: Decimal
    current: Decimal | None
    deadline: datetime | None


class DayRevenueView(BaseModel):
    day: date
    amount: Decimal


class OfferView(BaseModel):
    amount: Decimal
    currency: str
    interval: str


class RevenueView(BaseModel):
    """The last ``REVENUE_DAYS`` days of money and members, and the memberships now."""

    days: int
    total: Decimal
    """All revenue in those days, in the base currency."""
    daily: list[DayRevenueView]
    """One entry per UTC day, oldest first, a quiet day as zero."""
    payments: int
    new_members: int
    renewals: int
    lapsed_members: int
    members: int
    """Customers holding access now."""
    expiring_members: int
    """Members now whose access runs out within 30 days: who a renewal reminder is for."""
    average_payment: Decimal | None
    offer: OfferView | None = None
    """What a year costs today; None when nothing is for sale."""


REVENUE_DAYS = 30


class Kpis(BaseModel):
    as_of: datetime
    currency: str
    """The base currency (D-023). Model costs are converted into it from the meter's USD."""
    cash: Decimal
    revenue_today: Decimal
    expenses_today: Decimal
    model_cost_today: Decimal
    domain_metrics: dict[str, Any] = {}
    """Today's numbers from each domain, prefixed with the domain that defined them."""
    goal: GoalView | None = None
    revenue: RevenueView | None = None


def day_start(now: datetime) -> datetime:
    return now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


async def load_kpis(
    session: AsyncSession,
    company_id: uuid.UUID,
    *,
    now: datetime | None = None,
    reporting: Reporting | None = None,
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

    domain_metrics: dict[str, Any] = {}
    if reporting is not None:
        window = Window(company_id=company_id, since=today, until=now)
        domain_metrics = {
            key: value
            for key, value in (await reporting.metrics_for(session, window)).items()
            if "." in key  # the core's own figures are already fields above
        }

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

    revenue_view = await load_revenue(session, company_id, now=now)

    fx = Fx.from_settings()
    model_total, model_today = fx.metered(model_total), fx.metered(model_today)
    return Kpis(
        as_of=now,
        currency=fx.base,
        cash=balance - model_total,
        revenue_today=revenue,
        expenses_today=expenses + model_today,
        model_cost_today=model_today,
        domain_metrics=domain_metrics,
        goal=GoalView.model_validate(goal, from_attributes=True) if goal else None,
        revenue=revenue_view,
    )


async def load_revenue(
    session: AsyncSession, company_id: uuid.UUID, *, now: datetime
) -> RevenueView:
    """The revenue block: the last ``REVENUE_DAYS`` days, and the memberships as they stand now."""
    since = now - timedelta(days=REVENUE_DAYS)
    numbers = await membership_revenue.membership_numbers(
        session, company_id, since=since, until=now
    )
    daily = await membership_revenue.daily_revenue(
        session, company_id, days=REVENUE_DAYS, until=now
    )
    price = await memberships.offer(session, company_id)
    return RevenueView(
        days=REVENUE_DAYS,
        total=sum((d.amount for d in daily), Decimal(0)),
        daily=[DayRevenueView(day=d.day, amount=d.amount) for d in daily],
        payments=numbers.payments,
        new_members=numbers.new_members,
        renewals=numbers.renewals,
        lapsed_members=numbers.lapsed_members,
        members=numbers.members,
        expiring_members=numbers.expiring_members,
        average_payment=numbers.average_payment,
        offer=OfferView(amount=price.amount, currency=price.currency, interval=price.interval)
        if price
        else None,
    )
