"""Taking a one-time payment, from the order to the year it buys (T-702, D-024).

The shape of it, which is the same for any provider:

1. somebody asks to become a member — an **order** is written, PENDING, with our own trade
   number on it;
2. they pay at the provider's page, which is the last this site sees of them for a moment;
3. the provider posts a **notification** back, server to server, carrying that trade number;
4. the notification is checked, the order is settled, and the payment buys a year.

What is checked in step 4, and why each one is there:

- the envelope (the provider's job, ``infra/payments``) — a notification nobody sealed is not a
  notification;
- the trade number names an order of ours, from this provider;
- the amount is the amount that was ordered — a notification that says a different number is
  either a mistake or somebody trying one;
- the provider says the payment succeeded.

Only then does the money become a payment, a ledger row and a membership, all in one
transaction. Repeating a notification (providers do) changes nothing: the payment is idempotent
by the provider's own reference, and an order that is already PAID is answered with what it
bought the first time.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autora.company import memberships
from autora.company.ledger import MONEY, Ledger
from autora.db.models import Order, OrderState, Payment, Price, PriceState
from autora.infra.ids import uuid7
from autora.runtime.actor import Actor

TRADE_NO_PREFIX = "AU"


class OrderError(Exception):
    pass


class NotificationRefused(OrderError):
    """The notification is not one we can act on. Never tell the caller which check failed."""


@dataclass(frozen=True)
class Settled:
    order: Order
    payment: Payment
    granted: bool
    """False when this notification had already been acted on: the repeat, not the first."""


def trade_number(order_id: uuid.UUID) -> str:
    """Ours, unique, short enough for a provider's field: ``AU`` and the order's id in hex."""
    return f"{TRADE_NO_PREFIX}{order_id.hex[-18:].upper()}"


async def open_order(
    session: AsyncSession,
    *,
    price: Price,
    customer_ref: str,
    provider: str,
    amount: Decimal | None = None,
) -> Order:
    """Write down what somebody is about to pay for. Does not commit."""
    if price.state != PriceState.ACTIVE.value:
        raise OrderError("this price is retired; nothing new can be bought at it")
    if not customer_ref.strip():
        raise OrderError("an order is for somebody; this one is for nobody")
    order_id = uuid7()
    order = Order(
        id=order_id,
        company_id=price.company_id,
        price_id=price.id,
        customer_ref=customer_ref,
        amount=Decimal(price.amount if amount is None else amount).quantize(MONEY),
        currency=price.currency,
        provider=provider,
        mer_trade_no=trade_number(order_id),
        state=OrderState.PENDING.value,
    )
    session.add(order)
    await session.flush()
    return order


async def by_trade_number(session: AsyncSession, provider: str, mer_trade_no: str) -> Order | None:
    return await session.scalar(
        select(Order).where(Order.provider == provider, Order.mer_trade_no == mer_trade_no)
    )


async def settle(
    session: AsyncSession,
    *,
    provider: str,
    mer_trade_no: str,
    external_ref: str,
    amount: Decimal,
    actor: Actor,
    paid_at: datetime | None = None,
    ledger: Ledger | None = None,
) -> Settled:
    """A payment the provider says happened: check it against its order, then grant the year.

    Raises ``NotificationRefused`` when it does not match an order of ours, for the amount that
    was ordered. The caller answers the provider the same way either way — a handler that says
    *why* it refused is a handler that helps somebody find a way through.
    """
    order = await by_trade_number(session, provider, mer_trade_no)
    if order is None:
        raise NotificationRefused(f"no {provider} order {mer_trade_no!r}")
    amount = Decimal(amount).quantize(MONEY)
    if amount != order.amount:
        raise NotificationRefused(
            f"order {mer_trade_no} is for {order.amount}, the notification says {amount}"
        )
    price = await session.get(Price, order.price_id)
    assert price is not None

    payment, created = await memberships.purchase(
        session,
        price=price,
        customer_ref=order.customer_ref,
        provider=provider,
        external_ref=external_ref,
        actor=actor,
        amount=amount,
        currency=order.currency,
        paid_at=paid_at or datetime.now(UTC),
        ledger=ledger,
    )
    if order.state != OrderState.PAID.value:
        order.state = OrderState.PAID.value
        order.payment_id = payment.id
        await session.flush()
    return Settled(order=order, payment=payment, granted=created)


async def abandon(session: AsyncSession, order: Order, *, state: OrderState) -> Order:
    """A payment that failed or an order nobody finished. Kept: what was tried is worth knowing."""
    if order.state == OrderState.PAID.value:
        raise OrderError("an order that was paid cannot be abandoned")
    order.state = state.value
    await session.flush()
    return order
