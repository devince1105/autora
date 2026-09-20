"""Who pays this company, and what they are worth (ARCHITECTURE_V2_1 §7, T-612).

Two operations and a few queries. The point of the table is not to manage customers — a payment
provider does that — but to make one question answerable: **what does each business, product
and customer actually earn?** Revenue is a transaction with a customer on it; everything else
here is arithmetic over that.

**No personal data lives here.** A customer is an ``external_ref`` (the provider's id) and a
kind. No name, no email, no card. That is a deliberate boundary, not an omission: the company's
agents read this table, and there is nothing in it for them to leak. A deletion request goes to
the provider, and what stays behind is a row that says a payment happened — which is a
financial fact the company must keep anyway.

Deliberately absent: invoices, tax, receivables, reconciliation, currency conversion. When
there is enough revenue to need them they will be their own thing, not a widening of this.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from autora.company import events as company_events
from autora.db.models import Customer, CustomerKind, Transaction, TransactionKind
from autora.runtime.actor import Actor
from autora.runtime.events.outbox import emit
from autora.runtime.events.schema import new_event

MONEY = Decimal("0.000001")


class CustomerError(Exception):
    pass


async def acquire(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    external_ref: str,
    kind: CustomerKind,
    actor: Actor,
    business_unit_id: uuid.UUID | None = None,
    product_id: uuid.UUID | None = None,
    acquired_at: datetime | None = None,
) -> Customer:
    """Record somebody who started paying. Idempotent by the provider's reference.

    A webhook that arrives twice must not make two customers, and the provider's id is the only
    thing both deliveries agree on — so it is the key.
    """
    if not external_ref.strip():
        raise CustomerError("a customer is the provider's reference; an empty one is nobody")
    existing = await by_external_ref(session, company_id, external_ref)
    if existing is not None:
        return existing
    customer = Customer(
        company_id=company_id,
        business_unit_id=business_unit_id,
        external_ref=external_ref,
        kind=kind.value,
        acquired_at=acquired_at or datetime.now(UTC),
    )
    session.add(customer)
    await session.flush()
    await emit(
        session,
        new_event(
            company_events.CustomerAcquired(
                external_ref=external_ref,
                kind=kind.value,
                business_unit_id=business_unit_id,
                product_id=product_id,
            ),
            company_id=company_id,
            actor=actor,
            aggregate_type="customer",
            aggregate_id=customer.id,
        ),
    )
    return customer


async def churn(
    session: AsyncSession,
    customer: Customer,
    *,
    actor: Actor,
    reason: str | None = None,
    churned_at: datetime | None = None,
) -> Customer:
    """Record that they stopped paying. The row stays: what they earned still happened."""
    if customer.churned_at is not None:
        return customer
    customer.churned_at = churned_at or datetime.now(UTC)
    await session.flush()
    stayed = customer.churned_at - customer.acquired_at
    await emit(
        session,
        new_event(
            company_events.CustomerChurned(
                external_ref=customer.external_ref,
                kind=customer.kind,
                business_unit_id=customer.business_unit_id,
                reason=reason,
                days=max(0, stayed.days),
            ),
            company_id=customer.company_id,
            actor=actor,
            aggregate_type="customer",
            aggregate_id=customer.id,
        ),
    )
    return customer


async def by_external_ref(
    session: AsyncSession, company_id: uuid.UUID, external_ref: str
) -> Customer | None:
    return await session.scalar(
        select(Customer).where(
            Customer.company_id == company_id, Customer.external_ref == external_ref
        )
    )


async def paying(
    session: AsyncSession,
    company_id: uuid.UUID,
    *,
    business_unit_id: uuid.UUID | None = None,
    at: datetime | None = None,
) -> int:
    """How many customers were paying at a moment. Churned ones are counted until they left."""
    at = at or datetime.now(UTC)
    stmt = (
        select(func.count())
        .select_from(Customer)
        .where(
            Customer.company_id == company_id,
            Customer.acquired_at <= at,
            (Customer.churned_at.is_(None)) | (Customer.churned_at > at),
        )
    )
    if business_unit_id is not None:
        stmt = stmt.where(Customer.business_unit_id == business_unit_id)
    return int(await session.scalar(stmt) or 0)


async def revenue_by_customer(
    session: AsyncSession,
    company_id: uuid.UUID,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 20,
) -> list[tuple[Customer, Decimal]]:
    """What each customer paid in a window, most first — §7's last unanswered question.

    Revenue with no customer on it is simply left out: the question is "which customers earn
    us what", and a total that quietly folded in anonymous revenue would answer a different one.
    """
    stmt = (
        select(Customer, func.sum(Transaction.amount))
        .join(Transaction, Transaction.customer_id == Customer.id)
        .where(
            Customer.company_id == company_id,
            Transaction.kind == TransactionKind.REVENUE.value,
            *([Transaction.occurred_at >= since] if since else []),
            *([Transaction.occurred_at <= until] if until else []),
        )
        .group_by(Customer.id)
        .order_by(func.sum(Transaction.amount).desc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    return [(customer, Decimal(total or 0).quantize(MONEY)) for customer, total in rows]
