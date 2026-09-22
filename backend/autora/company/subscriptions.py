"""Prices, subscriptions and the payments they bring in (T-701, D-022).

The company's first revenue model: a product sold for a price per interval, through a payment
provider. The provider owns the truth — it bills, retries failed cards, cancels — and these
functions are how what it says becomes the company's own record. The webhook that calls them is
T-702; nothing here knows which provider it is, only its name as a token.

Three rules carry the design:

- **Every write is idempotent by the provider's reference.** A provider delivers the same event
  more than once, and a second delivery must change nothing — not a second subscription, not a
  second payment, not a second ledger row.
- **A payment is money, a subscription is a promise.** Only ``record_payment`` touches the
  ledger. A subscription going ACTIVE earns nothing until the provider says money arrived.
- **No personal data**, as for customers (D-018): references and amounts, never a person.

Deliberately absent: refunds, proration, trials that convert, coupons, tax. When a provider
event needs one of them, it becomes its own function rather than a flag on these.
"""

from __future__ import annotations

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
    Payment,
    Price,
    PriceInterval,
    PriceState,
    Product,
    ProductState,
    Subscription,
    SubscriptionState,
    TransactionKind,
    TransactionSource,
)
from autora.infra.ids import uuid7
from autora.infra.money import base_currency
from autora.runtime.actor import Actor
from autora.runtime.events.outbox import emit
from autora.runtime.events.schema import new_event
from autora.runtime.fsm import StateMachine, transitions

SS = SubscriptionState

SUBSCRIPTION_FSM = StateMachine(
    entity_type="subscription",
    states=SubscriptionState,
    initial=SS.ACTIVE,
    transitions=transitions(
        {
            SS.TRIALING: [SS.ACTIVE, SS.PAST_DUE, SS.CANCELED],
            SS.ACTIVE: [SS.PAST_DUE, SS.CANCELED],
            SS.PAST_DUE: [SS.ACTIVE, SS.CANCELED],
        }
    ),
)
"""PAST_DUE goes back to ACTIVE when a retried charge succeeds. CANCELED is the end: somebody
who comes back starts a new subscription, so the old one still says when they left."""

STARTING_STATES = (SS.TRIALING, SS.ACTIVE)
PAYING_STATES = (SS.ACTIVE.value, SS.PAST_DUE.value)
"""PAST_DUE still counts as paying: the provider is retrying, and most retries succeed."""

REVENUE_CATEGORY = "subscription"


class SubscriptionError(Exception):
    pass


def payment_key(provider: str, external_ref: str) -> str:
    """The ledger's idempotency key for a provider's payment. One charge, one row, ever."""
    return f"payment:{provider}:{external_ref}"


# --- prices -------------------------------------------------------------------------------------


async def add_price(
    session: AsyncSession,
    product: Product,
    *,
    amount: Decimal,
    interval: PriceInterval,
    provider: str,
    currency: str | None = None,
    external_ref: str | None = None,
) -> Price:
    """Offer a product at a price. A retired product is not for sale at any price."""
    if product.state == ProductState.RETIRED.value:
        raise SubscriptionError(f"product {product.key!r} is retired and cannot be priced")
    amount = Decimal(amount).quantize(MONEY)
    if amount <= 0:
        raise SubscriptionError(f"a price must be positive, got {amount}")
    price = Price(
        company_id=product.company_id,
        product_id=product.id,
        amount=amount,
        currency=currency or base_currency(),
        interval=interval.value,
        provider=provider,
        external_ref=external_ref,
    )
    session.add(price)
    await session.flush()
    return price


async def retire_price(session: AsyncSession, price: Price) -> Price:
    """Stop selling at this price. Subscriptions already on it keep it."""
    price.state = PriceState.RETIRED.value
    await session.flush()
    return price


async def price_by_external_ref(
    session: AsyncSession, company_id: uuid.UUID, provider: str, external_ref: str
) -> Price | None:
    return await session.scalar(
        select(Price).where(
            Price.company_id == company_id,
            Price.provider == provider,
            Price.external_ref == external_ref,
        )
    )


# --- subscriptions ------------------------------------------------------------------------------


async def start(
    session: AsyncSession,
    *,
    price: Price,
    customer_ref: str,
    external_ref: str,
    actor: Actor,
    state: SubscriptionState = SS.ACTIVE,
    started_at: datetime | None = None,
    current_period_end: datetime | None = None,
) -> Subscription:
    """Record a customer beginning to pay ``price``. Idempotent by the provider's reference.

    The customer is acquired on the way if this is their first subscription — a subscriber is
    a customer, and there is no other door into the table for one.
    """
    if state not in STARTING_STATES:
        raise SubscriptionError(f"a subscription starts TRIALING or ACTIVE, not {state}")
    existing = await by_external_ref(session, price.company_id, price.provider, external_ref)
    if existing is not None:
        return existing
    if price.state != PriceState.ACTIVE.value:
        raise SubscriptionError("this price is retired; nobody new can subscribe at it")
    product = await session.get(Product, price.product_id)
    assert product is not None
    started_at = started_at or datetime.now(UTC)
    customer = await customers.acquire(
        session,
        company_id=price.company_id,
        external_ref=customer_ref,
        kind=CustomerKind.SUBSCRIBER,
        actor=actor,
        business_unit_id=product.business_unit_id,
        product_id=product.id,
        acquired_at=started_at,
    )
    subscription = Subscription(
        company_id=price.company_id,
        business_unit_id=product.business_unit_id,
        customer_id=customer.id,
        product_id=product.id,
        price_id=price.id,
        state=state.value,
        provider=price.provider,
        external_ref=external_ref,
        started_at=started_at,
        current_period_end=current_period_end,
    )
    session.add(subscription)
    await session.flush()
    await emit(
        session,
        new_event(
            company_events.SubscriptionStarted(
                customer_id=customer.id,
                business_unit_id=product.business_unit_id,
                product_id=product.id,
                price_id=price.id,
                state=state.value,
                provider=price.provider,
                external_ref=external_ref,
            ),
            company_id=price.company_id,
            actor=actor,
            aggregate_type="subscription",
            aggregate_id=subscription.id,
        ),
    )
    return subscription


async def move(
    session: AsyncSession,
    subscription: Subscription,
    to: SubscriptionState,
    *,
    actor: Actor,
    reason: str | None = None,
    at: datetime | None = None,
    current_period_end: datetime | None = None,
) -> Subscription:
    """Follow the provider to a new state. The same state again only refreshes the period.

    Canceling the customer's last live subscription churns the customer: that is what "they
    stopped paying" means for somebody whose only way of paying is subscribing.
    """
    if current_period_end is not None:
        subscription.current_period_end = current_period_end
    if subscription.state == to.value:
        await session.flush()
        return subscription
    from_state = subscription.state
    at = at or datetime.now(UTC)
    if to == SS.CANCELED:
        subscription.canceled_at = at
    await SUBSCRIPTION_FSM.transition(session, subscription, to, actor=actor, reason=reason)
    await emit(
        session,
        new_event(
            company_events.SubscriptionStateChanged(
                customer_id=subscription.customer_id,
                business_unit_id=subscription.business_unit_id,
                from_state=from_state,
                to_state=to.value,
                reason=reason,
            ),
            company_id=subscription.company_id,
            actor=actor,
            aggregate_type="subscription",
            aggregate_id=subscription.id,
        ),
    )
    if to == SS.CANCELED and not await _still_subscribed(session, subscription.customer_id):
        customer = await session.get(Customer, subscription.customer_id)
        assert customer is not None
        await customers.churn(session, customer, actor=actor, reason=reason, churned_at=at)
    return subscription


async def _still_subscribed(session: AsyncSession, customer_id: uuid.UUID) -> bool:
    live = await session.scalar(
        select(func.count())
        .select_from(Subscription)
        .where(Subscription.customer_id == customer_id, Subscription.state != SS.CANCELED.value)
    )
    return bool(live)


async def by_external_ref(
    session: AsyncSession, company_id: uuid.UUID, provider: str, external_ref: str
) -> Subscription | None:
    return await session.scalar(
        select(Subscription).where(
            Subscription.company_id == company_id,
            Subscription.provider == provider,
            Subscription.external_ref == external_ref,
        )
    )


async def paying_subscriptions(
    session: AsyncSession,
    company_id: uuid.UUID,
    *,
    business_unit_id: uuid.UUID | None = None,
) -> int:
    stmt = (
        select(func.count())
        .select_from(Subscription)
        .where(Subscription.company_id == company_id, Subscription.state.in_(PAYING_STATES))
    )
    if business_unit_id is not None:
        stmt = stmt.where(Subscription.business_unit_id == business_unit_id)
    return int(await session.scalar(stmt) or 0)


# --- payments -----------------------------------------------------------------------------------


async def record_payment(
    session: AsyncSession,
    subscription: Subscription,
    *,
    external_ref: str,
    amount: Decimal,
    actor: Actor,
    currency: str | None = None,
    paid_at: datetime | None = None,
    ledger: Ledger | None = None,
) -> tuple[Payment, bool]:
    """Turn money the provider says arrived into a payment and a revenue transaction.

    Returns ``(payment, created)``; a second delivery of the same charge returns the first
    payment and ``False``, and writes nothing. The amount is what the provider charged, not the
    price: prorations and discounts make them differ, and the ledger records what happened.
    """
    provider = subscription.provider
    currency = currency or base_currency()
    existing = await session.scalar(
        select(Payment).where(Payment.provider == provider, Payment.external_ref == external_ref)
    )
    if existing is not None:
        return existing, False
    amount = Decimal(amount).quantize(MONEY)
    paid_at = paid_at or datetime.now(UTC)
    payment_id = uuid7()
    transaction = await (ledger or Ledger()).record(
        session,
        company_id=subscription.company_id,
        kind=TransactionKind.REVENUE,
        category=REVENUE_CATEGORY,
        amount=amount,
        currency=currency,
        idempotency_key=payment_key(provider, external_ref),
        actor=actor,
        business_unit_id=subscription.business_unit_id,
        product_id=subscription.product_id,
        customer_id=subscription.customer_id,
        occurred_at=paid_at,
        source=TransactionSource.INTEGRATION,
        ref_type="payment",
        ref_id=payment_id,
    )
    if transaction is None:
        raise SubscriptionError(
            f"the ledger already has {payment_key(provider, external_ref)!r} but no payment "
            "points at it; refusing to guess which is right"
        )
    payment = Payment(
        id=payment_id,
        company_id=subscription.company_id,
        business_unit_id=subscription.business_unit_id,
        customer_id=subscription.customer_id,
        subscription_id=subscription.id,
        transaction_id=transaction.id,
        provider=provider,
        external_ref=external_ref,
        amount=amount,
        currency=currency,
        paid_at=paid_at,
    )
    session.add(payment)
    await session.flush()
    await emit(
        session,
        new_event(
            company_events.PaymentReceived(
                payment_id=payment.id,
                transaction_id=transaction.id,
                customer_id=subscription.customer_id,
                business_unit_id=subscription.business_unit_id,
                subscription_id=subscription.id,
                provider=provider,
                amount=amount,
                currency=currency,
            ),
            company_id=subscription.company_id,
            actor=actor,
            aggregate_type="payment",
            aggregate_id=payment.id,
        ),
    )
    return payment, True
