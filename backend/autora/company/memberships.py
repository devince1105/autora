"""Paying once for a stretch of access (T-701, D-024).

The company's first revenue model: a product sold for a price that buys a year (or a month) of
access, paid once through a payment provider. Nothing is billed again. Somebody who wants
another year pays again, and that payment extends what they have.

Three rules carry the design:

- **Every purchase is idempotent by the provider's reference.** A provider notifies more than
  once, and a second notification must change nothing — not a second payment, not a second
  ledger row, not a second year.
- **The payment is the fact; the membership is what it bought.** Each payment records the
  stretch it granted (``grants_from`` → ``grants_until``); the membership row keeps only where
  that adds up to today. Renewing early loses nothing: the new year starts where the old ends.
- **No personal data here**, as for customers (D-018). The customer's reference is whatever
  the site knows them by (a reader id), never an email or a name.

Whether somebody may read is ``expires_at > now``, asked at the moment it matters. Writing
EXPIRED and churning the customer is bookkeeping, done once a day by the cycle.

Deliberately absent: refunds, upgrades between products, gifting, tax. When a real case needs
one, it becomes its own function rather than a flag on these.
"""

from __future__ import annotations

import calendar
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from autora.company import customers
from autora.company import events as company_events
from autora.company.ledger import MONEY, Ledger
from autora.db.models import (
    Customer,
    CustomerKind,
    Cycle,
    Membership,
    MembershipState,
    Payment,
    Price,
    PriceInterval,
    PriceState,
    Product,
    ProductState,
    TransactionKind,
    TransactionSource,
)
from autora.infra.ids import uuid7
from autora.infra.money import base_currency
from autora.runtime.actor import Actor
from autora.runtime.events.outbox import emit
from autora.runtime.events.schema import new_event
from autora.runtime.fsm import StateMachine, transitions

MS = MembershipState

MEMBERSHIP_FSM = StateMachine(
    entity_type="membership",
    states=MembershipState,
    initial=MS.ACTIVE,
    transitions=transitions({MS.ACTIVE: [MS.EXPIRED], MS.EXPIRED: [MS.ACTIVE]}),
)
"""EXPIRED goes back to ACTIVE when they buy again: the same row, a new stretch."""

REVENUE_CATEGORY = "membership"

PRODUCT_KEY = "membership"
"""The key of the product a reader buys. One per company, so the site can find what to sell."""


class MembershipError(Exception):
    pass


def payment_key(provider: str, external_ref: str) -> str:
    """The ledger's idempotency key for a provider's payment. One charge, one row, ever."""
    return f"payment:{provider}:{external_ref}"


def add_interval(start: datetime, interval: PriceInterval | str) -> datetime:
    """The same day next month or next year; the last day of the month when there is none
    (a year bought on 29 February ends on 28 February)."""
    months = 12 if PriceInterval(interval) == PriceInterval.YEAR else 1
    total = start.month - 1 + months
    year, month = start.year + total // 12, total % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return start.replace(year=year, month=month, day=day)


# --- prices -------------------------------------------------------------------------------------


async def add_price(
    session: AsyncSession,
    product: Product,
    *,
    amount: Decimal,
    interval: PriceInterval = PriceInterval.YEAR,
    currency: str | None = None,
) -> Price:
    """Offer a product at a price. A retired product is not for sale at any price."""
    if product.state == ProductState.RETIRED.value:
        raise MembershipError(f"product {product.key!r} is retired and cannot be priced")
    amount = Decimal(amount).quantize(MONEY)
    if amount <= 0:
        raise MembershipError(f"a price must be positive, got {amount}")
    price = Price(
        company_id=product.company_id,
        product_id=product.id,
        amount=amount,
        currency=currency or base_currency(),
        interval=interval.value,
    )
    session.add(price)
    await session.flush()
    return price


async def retire_price(session: AsyncSession, price: Price) -> Price:
    """Stop selling at this price. What was bought at it stays bought."""
    price.state = PriceState.RETIRED.value
    await session.flush()
    return price


async def offer(
    session: AsyncSession, company_id: uuid.UUID, *, key: str = PRODUCT_KEY
) -> Price | None:
    """What a company's membership costs today, or None when it is not for sale.

    The newest active price of a live product wins: raising the price is adding one and retiring
    the old, so that what was bought at the old price keeps pointing at the price it was bought
    at. Nobody's year changes because the next year costs more.
    """
    return await session.scalar(
        select(Price)
        .join(Product, Product.id == Price.product_id)
        .where(
            Price.company_id == company_id,
            Price.state == PriceState.ACTIVE.value,
            Product.key == key,
            Product.state != ProductState.RETIRED.value,
        )
        .order_by(Price.created_at.desc())
        .limit(1)
    )


# --- buying -------------------------------------------------------------------------------------


async def purchase(
    session: AsyncSession,
    *,
    price: Price,
    customer_ref: str,
    provider: str,
    external_ref: str,
    actor: Actor,
    amount: Decimal | None = None,
    currency: str | None = None,
    paid_at: datetime | None = None,
    ledger: Ledger | None = None,
) -> tuple[Payment, bool]:
    """Money arrived for ``price``: record it, book it, and grant what it bought.

    Returns ``(payment, created)``. The same provider reference again returns the first payment
    and ``False`` and writes nothing. ``amount`` is what the provider says was charged, the
    price's amount when omitted; checking that the two agree is the caller's job, because only
    the caller knows what the order said.
    """
    existing = await by_provider_ref(session, provider, external_ref)
    if existing is not None:
        return existing, False
    if price.state != PriceState.ACTIVE.value:
        raise MembershipError("this price is retired; nothing new can be bought at it")
    product = await session.get(Product, price.product_id)
    assert product is not None
    paid_at = paid_at or datetime.now(UTC)
    amount = Decimal(price.amount if amount is None else amount).quantize(MONEY)
    currency = currency or price.currency

    customer = await _customer(session, product, customer_ref, actor=actor, at=paid_at)
    membership, grants_from, extended = await _membership(session, product, customer, paid_at)
    grants_until = add_interval(grants_from, price.interval)
    membership.expires_at = grants_until
    if membership.state != MS.ACTIVE.value:
        await MEMBERSHIP_FSM.transition(
            session, membership, MS.ACTIVE, actor=actor, reason="bought again"
        )
    await session.flush()

    payment_id = uuid7()
    transaction = await (ledger or Ledger()).record(
        session,
        company_id=product.company_id,
        kind=TransactionKind.REVENUE,
        category=REVENUE_CATEGORY,
        amount=amount,
        currency=currency,
        idempotency_key=payment_key(provider, external_ref),
        actor=actor,
        business_unit_id=product.business_unit_id,
        product_id=product.id,
        customer_id=customer.id,
        occurred_at=paid_at,
        source=TransactionSource.INTEGRATION,
        ref_type="payment",
        ref_id=payment_id,
    )
    if transaction is None:
        raise MembershipError(
            f"the ledger already has {payment_key(provider, external_ref)!r} but no payment "
            "points at it; refusing to guess which is right"
        )
    payment = Payment(
        id=payment_id,
        company_id=product.company_id,
        business_unit_id=product.business_unit_id,
        customer_id=customer.id,
        price_id=price.id,
        membership_id=membership.id,
        grants_from=grants_from,
        grants_until=grants_until,
        transaction_id=transaction.id,
        provider=provider,
        external_ref=external_ref,
        amount=amount,
        currency=currency,
        paid_at=paid_at,
    )
    session.add(payment)
    await session.flush()

    for payload, aggregate_type, aggregate_id in (
        (
            company_events.MembershipGranted(
                customer_id=customer.id,
                business_unit_id=product.business_unit_id,
                product_id=product.id,
                payment_id=payment.id,
                grants_from=grants_from,
                grants_until=grants_until,
                extended=extended,
            ),
            "membership",
            membership.id,
        ),
        (
            company_events.PaymentReceived(
                payment_id=payment.id,
                transaction_id=transaction.id,
                customer_id=customer.id,
                business_unit_id=product.business_unit_id,
                membership_id=membership.id,
                provider=provider,
                amount=amount,
                currency=currency,
            ),
            "payment",
            payment.id,
        ),
    ):
        await emit(
            session,
            new_event(
                payload,
                company_id=product.company_id,
                actor=actor,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
            ),
        )
    return payment, True


async def _customer(
    session: AsyncSession, product: Product, customer_ref: str, *, actor: Actor, at: datetime
) -> Customer:
    """Whoever is paying, made a customer if they were not, reopened if they had left."""
    customer = await customers.by_external_ref(session, product.company_id, customer_ref)
    if customer is None:
        return await customers.acquire(
            session,
            company_id=product.company_id,
            external_ref=customer_ref,
            kind=CustomerKind.SUBSCRIBER,
            actor=actor,
            business_unit_id=product.business_unit_id,
            product_id=product.id,
            acquired_at=at,
        )
    return await customers.reactivate(session, customer, actor=actor, returned_at=at)


async def _membership(
    session: AsyncSession, product: Product, customer: Customer, paid_at: datetime
) -> tuple[Membership, datetime, bool]:
    """The row this purchase adds to, where its stretch starts, and whether it extends one.

    Still valid: the new stretch starts where the old one ends, so paying early loses nothing.
    Lapsed, or never had one: it starts now.
    """
    membership = await session.scalar(
        select(Membership).where(
            Membership.customer_id == customer.id, Membership.product_id == product.id
        )
    )
    if membership is None:
        membership = Membership(
            company_id=product.company_id,
            business_unit_id=product.business_unit_id,
            customer_id=customer.id,
            product_id=product.id,
            state=MS.ACTIVE.value,
            started_at=paid_at,
            expires_at=paid_at,  # set by the caller, before any flush, once it knows the interval
        )
        session.add(membership)
        return membership, paid_at, False
    if membership.expires_at > paid_at:
        return membership, membership.expires_at, True
    membership.started_at = paid_at
    return membership, paid_at, False


# --- lapsing ------------------------------------------------------------------------------------


async def expire_due(
    session: AsyncSession, company_id: uuid.UUID, *, now: datetime, actor: Actor
) -> list[Membership]:
    """Write down what ran out, and churn whoever it left with nothing. Safe to repeat.

    A customer with another product still running has not left, so they are not churned.
    """
    due = list(
        await session.scalars(
            select(Membership)
            .where(
                Membership.company_id == company_id,
                Membership.state == MS.ACTIVE.value,
                Membership.expires_at <= now,
            )
            .order_by(Membership.expires_at)
            .with_for_update(skip_locked=True)
        )
    )
    for membership in due:
        await MEMBERSHIP_FSM.transition(
            session, membership, MS.EXPIRED, actor=actor, reason="not renewed"
        )
        await emit(
            session,
            new_event(
                company_events.MembershipExpired(
                    customer_id=membership.customer_id,
                    business_unit_id=membership.business_unit_id,
                    product_id=membership.product_id,
                    expired_at=membership.expires_at,
                ),
                company_id=company_id,
                actor=actor,
                aggregate_type="membership",
                aggregate_id=membership.id,
            ),
        )
        if not await _has_access(session, membership.customer_id, now):
            customer = await session.get(Customer, membership.customer_id)
            assert customer is not None
            await customers.churn(
                session,
                customer,
                actor=actor,
                reason="membership expired",
                churned_at=membership.expires_at,
            )
    return due


def stage_hook():
    """Expire on the way into MEASURING, before reporting counts who is paying."""

    async def expire(session: AsyncSession, cycle: Cycle) -> None:
        await expire_due(
            session, cycle.company_id, now=datetime.now(UTC), actor=Actor.system("memberships")
        )

    return expire


# --- reading ------------------------------------------------------------------------------------


async def has_access(
    session: AsyncSession,
    customer_id: uuid.UUID,
    product_id: uuid.UUID,
    *,
    at: datetime | None = None,
) -> bool:
    """May they read it, now? The one question the site asks."""
    expires_at = await session.scalar(
        select(Membership.expires_at).where(
            Membership.customer_id == customer_id, Membership.product_id == product_id
        )
    )
    return expires_at is not None and expires_at > (at or datetime.now(UTC))


async def access_until(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    customer_ref: str,
    at: datetime | None = None,
) -> datetime | None:
    """Until when is whoever this reference belongs to a member of this company? None: not.

    Takes the reference rather than a customer, because the caller (the site) knows a reader,
    not a customer, and somebody who has never paid has no customer row at all. The furthest
    date wins when they hold more than one membership.
    """
    return await session.scalar(
        select(func.max(Membership.expires_at))
        .join(Customer, Customer.id == Membership.customer_id)
        .where(
            Membership.company_id == company_id,
            Customer.external_ref == customer_ref,
            Membership.expires_at > (at or datetime.now(UTC)),
        )
    )


async def _has_access(session: AsyncSession, customer_id: uuid.UUID, at: datetime) -> bool:
    live = await session.scalar(
        select(func.count())
        .select_from(Membership)
        .where(Membership.customer_id == customer_id, Membership.expires_at > at)
    )
    return bool(live)


async def members(
    session: AsyncSession,
    company_id: uuid.UUID,
    *,
    business_unit_id: uuid.UUID | None = None,
    at: datetime | None = None,
) -> int:
    """How many memberships are running at a moment — by their dates, not by ``state``."""
    stmt = (
        select(func.count())
        .select_from(Membership)
        .where(
            Membership.company_id == company_id,
            Membership.expires_at > (at or datetime.now(UTC)),
        )
    )
    if business_unit_id is not None:
        stmt = stmt.where(Membership.business_unit_id == business_unit_id)
    return int(await session.scalar(stmt) or 0)


async def by_provider_ref(
    session: AsyncSession, provider: str, external_ref: str
) -> Payment | None:
    return await session.scalar(
        select(Payment).where(Payment.provider == provider, Payment.external_ref == external_ref)
    )
