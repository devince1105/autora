"""What the memberships add up to: the numbers behind the revenue on the dashboard (T-707, D-024).

The company sells a year (or a month) of access for one payment. Revenue alone — the money in —
does not say whether the business is healthy: a good month could be everybody renewing at once
and nobody new, and next year's income is whoever is still a member then. So beside the money
this counts the people:

- **payments** in the period, split into **new members** (a customer's first payment — in the
  company, or for one business in that business) and **renewals** (every payment after that);
- **members**: customers who hold access at the end of the period;
- **expiring members**: members at the end whose access runs out within the next 30 days — the
  people a renewal reminder is for;
- **lapsed members**: customers whose access ran out during the period and who had not paid again
  by its end;
- **average payment**: membership revenue over payments.

Everything is counted from ``payments``, not from the ``memberships`` row. A payment records the
stretch of access it bought (``grants_from`` → ``grants_until``) and payments are never edited, so
"was this customer a member on that day" has an exact answer for any day, however long ago. The
membership row only keeps where the stretches add up to *now* — a renewal moves its expiry and
forgets the old one — so it cannot say who was a member at the end of last month's cycle.

Industry-neutral, like the rest of ``company``: the same numbers describe a newsletter, a course or
a gym. Both the cycle's report (``reporting``, which the CEO reads through the snapshot) and the
dashboard ask this module, so the two cannot disagree.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import Date, and_, cast, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from autora.company.memberships import REVENUE_CATEGORY
from autora.db.models import Payment, Transaction, TransactionKind

MONEY = Decimal("0.000001")

EXPIRING_WITHIN = timedelta(days=30)
"""How far ahead "about to expire" looks: long enough to remind somebody, short enough to matter."""


@dataclass(frozen=True)
class MembershipNumbers:
    payments: int
    new_members: int
    renewals: int
    members: int
    expiring_members: int
    lapsed_members: int
    membership_revenue: Decimal
    average_payment: Decimal | None
    """None when nobody paid: an average of nothing is not zero."""

    def as_metrics(self) -> dict[str, object]:
        """The numbers under the names the reports store them by (bare: the core's own)."""
        return {
            "payments": self.payments,
            "new_members": self.new_members,
            "renewals": self.renewals,
            "members": self.members,
            "expiring_members": self.expiring_members,
            "lapsed_members": self.lapsed_members,
            "membership_revenue": str(self.membership_revenue),
            "average_payment": None if self.average_payment is None else str(self.average_payment),
        }


def _scoped(company_id: uuid.UUID, business_unit_id: uuid.UUID | None) -> list:
    where = [Payment.company_id == company_id, Payment.grants_until.is_not(None)]
    if business_unit_id is not None:
        where.append(Payment.business_unit_id == business_unit_id)
    return where


async def membership_numbers(
    session: AsyncSession,
    company_id: uuid.UUID,
    *,
    since: datetime,
    until: datetime,
    business_unit_id: uuid.UUID | None = None,
) -> MembershipNumbers:
    """The memberships' numbers for a period, for the company or one of its businesses."""
    scoped = _scoped(company_id, business_unit_id)
    in_period = and_(Payment.paid_at >= since, Payment.paid_at <= until)

    # a customer's first payment: nothing of theirs paid before it (ties broken by id, so exactly
    # one). For a business, first *in that business* — somebody who already reads the newsroom and
    # now joins the school is new to the school
    earlier = aliased(Payment)
    before = [
        earlier.customer_id == Payment.customer_id,
        earlier.company_id == Payment.company_id,
        (earlier.paid_at < Payment.paid_at)
        | ((earlier.paid_at == Payment.paid_at) & (earlier.id < Payment.id)),
    ]
    if business_unit_id is not None:
        before.append(earlier.business_unit_id == Payment.business_unit_id)
    first = ~exists().where(*before)
    payments, new_members = (
        await session.execute(
            select(func.count(), func.count().filter(first))
            .select_from(Payment)
            .where(*scoped, in_period)
        )
    ).one()

    # the money those payments became, in the ledger's currency — the ledger is where the
    # conversion happened (D-023), so it is where the amount is read
    revenue = await session.scalar(
        select(func.coalesce(func.sum(Transaction.amount), 0))
        .select_from(Payment)
        .join(Transaction, Transaction.id == Payment.transaction_id)
        .where(
            *scoped,
            in_period,
            Transaction.kind == TransactionKind.REVENUE.value,
            Transaction.category == REVENUE_CATEGORY,
        )
    )
    revenue = Decimal(revenue or 0).quantize(MONEY)

    # where each customer's access stood at the end of the period: the furthest stretch that any
    # payment made by then had bought, and whether a stretch covers the end itself
    paid_by_end = [*scoped, Payment.paid_at <= until]
    reach = (
        select(
            Payment.customer_id.label("customer_id"),
            func.max(Payment.grants_until).label("ends"),
            func.count()
            .filter(and_(Payment.grants_from <= until, Payment.grants_until > until))
            .label("covering"),
        )
        .where(*paid_by_end)
        .group_by(Payment.customer_id)
        .subquery()
    )
    members, expiring, lapsed = (
        await session.execute(
            select(
                func.count().filter(reach.c.covering > 0),
                func.count().filter(
                    and_(reach.c.covering > 0, reach.c.ends <= until + EXPIRING_WITHIN)
                ),
                func.count().filter(and_(reach.c.ends > since, reach.c.ends <= until)),
            ).select_from(reach)
        )
    ).one()

    return MembershipNumbers(
        payments=int(payments),
        new_members=int(new_members),
        renewals=int(payments) - int(new_members),
        members=int(members),
        expiring_members=int(expiring),
        lapsed_members=int(lapsed),
        membership_revenue=revenue,
        average_payment=(revenue / payments).quantize(MONEY) if payments else None,
    )


@dataclass(frozen=True)
class DayRevenue:
    day: date
    amount: Decimal


async def daily_revenue(
    session: AsyncSession, company_id: uuid.UUID, *, days: int, until: datetime
) -> list[DayRevenue]:
    """All revenue per UTC day for the ``days`` days ending with ``until``'s, oldest first — every
    day present, a day with no money as zero, so a chart of it has no gaps that look like data."""
    last = until.date()
    first = last - timedelta(days=days - 1)
    day = cast(func.timezone("UTC", Transaction.occurred_at), Date)
    rows = await session.execute(
        select(day, func.sum(Transaction.amount))
        .where(
            Transaction.company_id == company_id,
            Transaction.kind == TransactionKind.REVENUE.value,
            day >= first,
            Transaction.occurred_at <= until,
        )
        .group_by(day)
    )
    by_day = {d: Decimal(amount).quantize(MONEY) for d, amount in rows}
    return [
        DayRevenue(
            first + timedelta(days=i),
            by_day.get(first + timedelta(days=i), Decimal(0).quantize(MONEY)),
        )
        for i in range(days)
    ]
