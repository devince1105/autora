"""T-702: an order, and the notification that turns it into a year.

The order exists so that a notification can be checked against something we wrote down before
the reader ever reached the payment page. Everything here is about that check: the trade number
has to be ours, the amount has to be the one we asked for, and a notification arriving twice —
which providers do — has to leave exactly one payment, one ledger row and one year behind it.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from autora.company import memberships, orders
from autora.company.organization import add_business_unit, add_product
from autora.db.models import (
    BusinessUnitState,
    Membership,
    OrderState,
    Payment,
    PriceInterval,
    PriceState,
    ProductState,
    Transaction,
)
from autora.runtime.actor import Actor
from tests.conftest import unique_company

OPERATOR = Actor.human("operator")
PAYUNI = Actor.system("payments:payuni")
DAY = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
READER = "reader:7"


async def _offer(db_session, *, amount="360", key=memberships.PRODUCT_KEY, state=ProductState.LIVE):
    company = await unique_company(db_session, "orders")
    unit = await add_business_unit(
        db_session, company_id=company.id, key="ai_media", name="AI Media",
        actor=OPERATOR, state=BusinessUnitState.ACTIVE,
    )  # fmt: skip
    product = await add_product(
        db_session, company_id=company.id, key=key, name="Membership",
        business_unit_id=unit.id, actor=OPERATOR, state=state,
    )  # fmt: skip
    price = await memberships.add_price(
        db_session, product, amount=Decimal(amount), interval=PriceInterval.YEAR
    )
    return company, price


async def _order(db_session, price, *, reader=READER):
    return await orders.open_order(
        db_session, price=price, customer_ref=reader, provider="payuni"
    )  # fmt: skip


async def _settle(db_session, order, *, amount=None, ref=None, at=DAY):
    return await orders.settle(
        db_session,
        provider="payuni",
        mer_trade_no=order.mer_trade_no,
        external_ref=ref or f"UNI{uuid.uuid4().hex[:10]}",
        amount=Decimal(order.amount if amount is None else amount),
        actor=PAYUNI,
        paid_at=at,
    )


# --- the trade number -----------------------------------------------------------------------


def test_a_trade_number_is_ours_and_says_so():
    number = orders.trade_number(uuid.uuid4())
    assert number.startswith(orders.TRADE_NO_PREFIX)
    assert len(number) == 20  # PAYUNi's MerTradeNo holds far more than this
    assert number.isalnum()


def test_two_orders_never_share_a_trade_number():
    made = {orders.trade_number(uuid.uuid4()) for _ in range(2000)}
    assert len(made) == 2000


# --- opening one ----------------------------------------------------------------------------


async def test_an_order_records_what_was_asked_for_before_anybody_pays(db_session):
    _, price = await _offer(db_session, amount="360")
    order = await _order(db_session, price)
    assert order.state == OrderState.PENDING.value
    assert order.amount == Decimal("360.00")
    assert order.currency == "TWD"
    assert order.customer_ref == READER
    assert order.payment_id is None


async def test_nothing_is_granted_by_opening_an_order(db_session):
    company, price = await _offer(db_session)
    await _order(db_session, price)
    await db_session.flush()
    assert (
        await memberships.access_until(db_session, company_id=company.id, customer_ref=READER)
        is None
    )


async def test_a_retired_price_cannot_be_ordered(db_session):
    _, price = await _offer(db_session)
    await memberships.retire_price(db_session, price)
    with pytest.raises(orders.OrderError, match="retired"):
        await _order(db_session, price)


async def test_an_order_is_for_somebody(db_session):
    _, price = await _offer(db_session)
    with pytest.raises(orders.OrderError, match="nobody"):
        await _order(db_session, price, reader="   ")


# --- what the offer lookup finds --------------------------------------------------------------


async def test_the_site_finds_the_membership_price(db_session):
    company, price = await _offer(db_session)
    found = await memberships.offer(db_session, company.id)
    assert found is not None and found.id == price.id


async def test_a_retired_price_is_not_on_offer(db_session):
    company, price = await _offer(db_session)
    await memberships.retire_price(db_session, price)
    assert await memberships.offer(db_session, company.id) is None


async def test_raising_the_price_offers_the_new_one_and_leaves_the_old_year_alone(db_session):
    company, old = await _offer(db_session, amount="360")
    order = await _order(db_session, old)
    await _settle(db_session, order)

    await memberships.retire_price(db_session, old)
    product = await db_session.get(memberships.Product, old.product_id)
    new = await memberships.add_price(db_session, product, amount=Decimal("480"))
    await db_session.flush()

    assert (await memberships.offer(db_session, company.id)).id == new.id
    assert old.state == PriceState.RETIRED.value
    payment = await db_session.scalar(select(Payment).where(Payment.price_id == old.id))
    assert payment is not None and payment.amount == Decimal("360.00")


async def test_a_company_with_nothing_for_sale_offers_nothing(db_session):
    company, _ = await _offer(db_session, key="daily")  # some other product, not the membership
    assert await memberships.offer(db_session, company.id) is None


# --- the notification -------------------------------------------------------------------------


async def test_a_paid_order_grants_the_year_and_points_at_its_payment(db_session):
    company, price = await _offer(db_session)
    order = await _order(db_session, price)
    settled = await _settle(db_session, order)

    assert settled.granted is True
    assert order.state == OrderState.PAID.value
    assert order.payment_id == settled.payment.id
    until = await memberships.access_until(
        db_session, company_id=company.id, customer_ref=READER, at=DAY
    )
    assert until == datetime(2027, 9, 22, 12, 0, tzinfo=UTC)


async def test_the_same_notification_twice_leaves_one_of_everything(db_session):
    company, price = await _offer(db_session)
    order = await _order(db_session, price)
    first = await _settle(db_session, order, ref="UNI-1")
    again = await _settle(db_session, order, ref="UNI-1")

    assert first.granted is True
    assert again.granted is False
    assert again.payment.id == first.payment.id
    assert await _count(db_session, Payment, Payment.company_id == company.id) == 1
    assert await _count(db_session, Transaction, Transaction.company_id == company.id) == 1
    assert await _count(db_session, Membership, Membership.company_id == company.id) == 1
    until = await memberships.access_until(
        db_session, company_id=company.id, customer_ref=READER, at=DAY
    )
    assert until == datetime(2027, 9, 22, 12, 0, tzinfo=UTC)


async def test_a_trade_number_we_never_issued_is_refused(db_session):
    with pytest.raises(orders.NotificationRefused, match="no payuni order"):
        await orders.settle(
            db_session,
            provider="payuni",
            mer_trade_no="AU000000000000000000",
            external_ref="UNI-9",
            amount=Decimal("360"),
            actor=PAYUNI,
        )


async def test_the_same_trade_number_at_another_provider_is_not_ours_either(db_session):
    _, price = await _offer(db_session)
    order = await _order(db_session, price)
    with pytest.raises(orders.NotificationRefused):
        await orders.settle(
            db_session,
            provider="somebody-else",
            mer_trade_no=order.mer_trade_no,
            external_ref="X-1",
            amount=order.amount,
            actor=PAYUNI,
        )


@pytest.mark.parametrize("said", ["1", "359", "3600", "0"])
async def test_an_amount_that_is_not_the_one_ordered_is_refused(db_session, said):
    company, price = await _offer(db_session, amount="360")
    order = await _order(db_session, price)
    with pytest.raises(orders.NotificationRefused, match="the notification says"):
        await _settle(db_session, order, amount=said)
    assert order.state == OrderState.PENDING.value
    assert await _count(db_session, Payment, Payment.company_id == company.id) == 0


async def test_a_refused_notification_grants_nothing(db_session):
    company, price = await _offer(db_session, amount="360")
    order = await _order(db_session, price)
    with pytest.raises(orders.NotificationRefused):
        await _settle(db_session, order, amount="1")
    assert (
        await memberships.access_until(db_session, company_id=company.id, customer_ref=READER)
        is None
    )


# --- the ones that never get paid --------------------------------------------------------------


async def test_a_failed_payment_is_written_down_rather_than_forgotten(db_session):
    _, price = await _offer(db_session)
    order = await _order(db_session, price)
    await orders.abandon(db_session, order, state=OrderState.FAILED)
    assert order.state == OrderState.FAILED.value
    assert order.payment_id is None


async def test_what_was_paid_for_cannot_be_abandoned(db_session):
    _, price = await _offer(db_session)
    order = await _order(db_session, price)
    await _settle(db_session, order)
    with pytest.raises(orders.OrderError, match="cannot be abandoned"):
        await orders.abandon(db_session, order, state=OrderState.EXPIRED)


async def test_a_notification_for_an_order_that_was_given_up_on_still_pays(db_session):
    """A card that came good after a timeout. The money is the fact; our guess was not."""
    company, price = await _offer(db_session)
    order = await _order(db_session, price)
    await orders.abandon(db_session, order, state=OrderState.EXPIRED)
    settled = await _settle(db_session, order)
    assert settled.granted is True
    assert order.state == OrderState.PAID.value
    assert (
        await memberships.access_until(
            db_session, company_id=company.id, customer_ref=READER, at=DAY
        )
        is not None
    )


async def _count(db_session, model, *where) -> int:
    return await db_session.scalar(select(func.count()).select_from(model).where(*where))
