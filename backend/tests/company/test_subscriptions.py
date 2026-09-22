"""T-701: a subscription is a promise, a payment is money (D-022).

What the webhook in T-702 will lean on: every call is safe to repeat with the provider's
reference, only a payment reaches the ledger, and canceling the last subscription is the same
thing as the customer leaving.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from autora.company import customers, subscriptions
from autora.company.ledger import Ledger
from autora.company.organization import add_business_unit, add_product
from autora.db.models import (
    BusinessUnitState,
    Customer,
    EventRecord,
    Payment,
    PriceInterval,
    ProductState,
    StateTransition,
    SubscriptionState,
    Transaction,
    TransactionKind,
    TransactionSource,
)
from autora.runtime.actor import Actor
from autora.runtime.fsm import IllegalTransition
from tests.conftest import unique_company

OPERATOR = Actor.human("operator")
STRIPE = Actor.system("payments:stripe")
DAY = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
SS = SubscriptionState


async def _world(session, *, product_state=ProductState.LIVE):
    company = await unique_company(session, "subscriptions")
    unit = await add_business_unit(
        session, company_id=company.id, key="ai_media", name="AI Media",
        actor=OPERATOR, state=BusinessUnitState.ACTIVE,
    )  # fmt: skip
    product = await add_product(
        session, company_id=company.id, key="daily", name="Daily",
        business_unit_id=unit.id, actor=OPERATOR, state=product_state,
    )  # fmt: skip
    return company, unit, product


async def _price(session, product, amount="5"):
    return await subscriptions.add_price(
        session, product, amount=Decimal(amount), interval=PriceInterval.MONTH,
        provider="stripe", external_ref=f"price_{uuid.uuid4().hex[:8]}",
    )  # fmt: skip


async def _subscribe(session, price, *, customer_ref="cus_1", ref=None, state=SS.ACTIVE):
    return await subscriptions.start(
        session, price=price, customer_ref=customer_ref,
        external_ref=ref or f"sub_{uuid.uuid4().hex[:8]}", actor=STRIPE, state=state,
        started_at=DAY,
    )  # fmt: skip


async def _pay(session, sub, ref="in_1", amount="5", at=DAY):
    return await subscriptions.record_payment(
        session, sub, external_ref=ref, amount=Decimal(amount), actor=STRIPE, paid_at=at
    )


async def _events(session, company, event_type):
    return (
        await session.scalars(
            select(EventRecord).where(
                EventRecord.company_id == company.id, EventRecord.event_type == event_type
            )
        )
    ).all()


# --- prices ---------------------------------------------------------------------------------


async def test_a_retired_product_is_not_for_sale(db_session):
    _, _, product = await _world(db_session, product_state=ProductState.RETIRED)
    with pytest.raises(subscriptions.SubscriptionError, match="retired"):
        await _price(db_session, product)


async def test_nobody_new_subscribes_at_a_retired_price_but_the_old_ones_keep_it(db_session):
    _, _, product = await _world(db_session)
    price = await _price(db_session, product)
    kept = await _subscribe(db_session, price, ref="sub_old")
    await subscriptions.retire_price(db_session, price)

    with pytest.raises(subscriptions.SubscriptionError, match="retired"):
        await _subscribe(db_session, price, customer_ref="cus_2", ref="sub_new")
    assert kept.price_id == price.id
    # the provider re-sending the old subscription is not somebody new
    again = await _subscribe(db_session, price, ref="sub_old")
    assert again.id == kept.id


# --- starting -------------------------------------------------------------------------------


async def test_subscribing_makes_the_customer_and_says_so_once(db_session):
    company, unit, product = await _world(db_session)
    price = await _price(db_session, product)
    first = await _subscribe(db_session, price, ref="sub_1")
    again = await _subscribe(db_session, price, ref="sub_1")

    assert again.id == first.id
    customer = await db_session.get(Customer, first.customer_id)
    assert (customer.external_ref, customer.kind) == ("cus_1", "subscriber")
    assert customer.business_unit_id == unit.id
    assert (first.business_unit_id, first.product_id) == (unit.id, product.id)
    assert len(await _events(db_session, company, "SUBSCRIPTION_STARTED")) == 1
    assert len(await _events(db_session, company, "CUSTOMER_ACQUIRED")) == 1


async def test_a_second_subscription_is_the_same_customer(db_session):
    company, _, product = await _world(db_session)
    price = await _price(db_session, product)
    a = await _subscribe(db_session, price, ref="sub_a")
    b = await _subscribe(db_session, price, ref="sub_b")
    assert a.id != b.id and a.customer_id == b.customer_id
    assert await subscriptions.paying_subscriptions(db_session, company.id) == 2


@pytest.mark.parametrize("state", [SS.PAST_DUE, SS.CANCELED])
async def test_a_subscription_does_not_start_behind_or_over(db_session, state):
    _, _, product = await _world(db_session)
    price = await _price(db_session, product)
    with pytest.raises(subscriptions.SubscriptionError, match="starts"):
        await _subscribe(db_session, price, state=state)


async def test_starting_earns_nothing(db_session):
    """ACTIVE is the provider's promise to try charging. Revenue is when the charge works."""
    company, _, product = await _world(db_session)
    await _subscribe(db_session, await _price(db_session, product))
    assert await Ledger().balance(db_session, company.id) == 0


# --- following the provider -----------------------------------------------------------------


async def test_a_failed_card_and_a_retry_that_works(db_session):
    company, _, product = await _world(db_session)
    sub = await _subscribe(db_session, await _price(db_session, product))
    end = DAY + timedelta(days=31)

    await subscriptions.move(db_session, sub, SS.PAST_DUE, actor=STRIPE, reason="card_declined")
    assert await subscriptions.paying_subscriptions(db_session, company.id) == 1
    await subscriptions.move(db_session, sub, SS.ACTIVE, actor=STRIPE, current_period_end=end)

    assert (sub.state, sub.current_period_end) == ("ACTIVE", end)
    audited = (
        await db_session.scalars(
            select(StateTransition.to_state)
            .where(StateTransition.entity_id == sub.id)
            .order_by(StateTransition.id)  # uuid7: in the order written
        )
    ).all()
    assert audited == ["PAST_DUE", "ACTIVE"]
    assert len(await _events(db_session, company, "SUBSCRIPTION_STATE_CHANGED")) == 2


async def test_the_same_state_again_changes_nothing_but_the_period(db_session):
    """Providers resend. An update that says ACTIVE to an ACTIVE row is not a transition."""
    company, _, product = await _world(db_session)
    sub = await _subscribe(db_session, await _price(db_session, product))
    end = DAY + timedelta(days=31)
    await subscriptions.move(db_session, sub, SS.ACTIVE, actor=STRIPE, current_period_end=end)
    assert sub.current_period_end == end
    assert await _events(db_session, company, "SUBSCRIPTION_STATE_CHANGED") == []


async def test_canceled_is_the_end(db_session):
    _, _, product = await _world(db_session)
    sub = await _subscribe(db_session, await _price(db_session, product))
    await subscriptions.move(db_session, sub, SS.CANCELED, actor=STRIPE, at=DAY)
    assert sub.canceled_at == DAY
    with pytest.raises(IllegalTransition):
        await subscriptions.move(db_session, sub, SS.ACTIVE, actor=STRIPE)


async def test_canceling_the_last_subscription_is_the_customer_leaving(db_session):
    company, _, product = await _world(db_session)
    price = await _price(db_session, product)
    a = await _subscribe(db_session, price, ref="sub_a")
    b = await _subscribe(db_session, price, ref="sub_b")
    customer = await db_session.get(Customer, a.customer_id)

    await subscriptions.move(db_session, a, SS.CANCELED, actor=STRIPE, at=DAY)
    assert customer.churned_at is None, "they still pay for the other one"

    left = DAY + timedelta(days=3)
    await subscriptions.move(db_session, b, SS.CANCELED, actor=STRIPE, at=left, reason="price")
    assert customer.churned_at == left
    assert await customers.paying(db_session, company.id, at=left + timedelta(seconds=1)) == 0
    assert await subscriptions.paying_subscriptions(db_session, company.id) == 0


# --- payments -------------------------------------------------------------------------------


async def test_a_payment_is_revenue_from_that_customer_for_that_product(db_session):
    company, unit, product = await _world(db_session)
    sub = await _subscribe(db_session, await _price(db_session, product))
    payment, created = await _pay(db_session, sub, amount="5")

    assert created
    transaction = await db_session.get(Transaction, payment.transaction_id)
    assert transaction.kind == TransactionKind.REVENUE.value
    assert transaction.source == TransactionSource.INTEGRATION.value
    assert transaction.category == "subscription"
    assert transaction.amount == Decimal("5")
    assert (transaction.business_unit_id, transaction.product_id) == (unit.id, product.id)
    assert transaction.customer_id == sub.customer_id
    assert (transaction.ref_type, transaction.ref_id) == ("payment", payment.id)
    assert transaction.idempotency_key == "payment:stripe:in_1"
    received = await _events(db_session, company, "PAYMENT_RECEIVED")
    assert len(received) == 1
    assert len(await _events(db_session, company, "REVENUE_RECORDED")) == 1


async def test_the_same_charge_delivered_twice_is_one_payment_and_one_row(db_session):
    """The property T-702's acceptance line names. It starts here."""
    company, _, product = await _world(db_session)
    sub = await _subscribe(db_session, await _price(db_session, product))
    first, created = await _pay(db_session, sub, ref="in_dup")
    again, created_again = await _pay(db_session, sub, ref="in_dup")

    assert created and not created_again and again.id == first.id
    assert await Ledger().balance(db_session, company.id) == Decimal("5")
    count = await db_session.scalar(
        select(func.count()).select_from(Payment).where(Payment.company_id == company.id)
    )
    assert count == 1
    assert len(await _events(db_session, company, "PAYMENT_RECEIVED")) == 1


async def test_the_ledger_records_what_was_charged_not_the_list_price(db_session):
    """A prorated first month is less than the price, and the ledger says what happened."""
    company, _, product = await _world(db_session)
    sub = await _subscribe(db_session, await _price(db_session, product, amount="5"))
    await _pay(db_session, sub, ref="in_prorated", amount="2.17")
    assert await Ledger().balance(db_session, company.id) == Decimal("2.17")


async def test_each_customer_is_worth_what_they_paid(db_session):
    """T-612's question, now answered by real payments instead of hand-made rows."""
    company, _, product = await _world(db_session)
    price = await _price(db_session, product)
    loyal = await _subscribe(db_session, price, customer_ref="cus_loyal")
    brief = await _subscribe(db_session, price, customer_ref="cus_brief")
    for month in range(3):
        await _pay(db_session, loyal, ref=f"in_l{month}", at=DAY + timedelta(days=30 * month))
    await _pay(db_session, brief, ref="in_b0")

    ranked = await customers.revenue_by_customer(db_session, company.id)
    assert [(c.external_ref, total) for c, total in ranked] == [
        ("cus_loyal", Decimal("15")),
        ("cus_brief", Decimal("5")),
    ]


async def test_a_ledger_row_with_no_payment_behind_it_is_refused_not_guessed(db_session):
    """If the key is taken but no payment points at it, something wrote the ledger by hand.
    Returning quietly would lose a payment; writing another row is impossible. Say so."""
    company, _, product = await _world(db_session)
    sub = await _subscribe(db_session, await _price(db_session, product))
    await Ledger().record(
        db_session, company_id=company.id, kind=TransactionKind.REVENUE, category="subscription",
        amount=Decimal("5"), idempotency_key=subscriptions.payment_key("stripe", "in_orphan"),
        actor=OPERATOR, source=TransactionSource.HUMAN,
    )  # fmt: skip
    with pytest.raises(subscriptions.SubscriptionError, match="refusing to guess"):
        await _pay(db_session, sub, ref="in_orphan")
