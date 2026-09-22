"""D-024: pay once, read for a year.

What the PAYUNi notification handler (T-702) will lean on: a purchase is safe to repeat with the
provider's reference, paying early extends rather than overlaps, and a year running out is the
customer leaving — unless something else of theirs is still running.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from autora.company import customers, memberships
from autora.company.ledger import Ledger
from autora.company.organization import add_business_unit, add_product
from autora.db.models import (
    BusinessUnitState,
    Customer,
    EventRecord,
    Membership,
    Payment,
    PriceInterval,
    ProductState,
    StateTransition,
    Transaction,
    TransactionKind,
    TransactionSource,
)
from autora.runtime.actor import Actor
from tests.conftest import unique_company

OPERATOR = Actor.human("operator")
PAYUNI = Actor.system("payments:payuni")
DAY = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
YEAR_LATER = datetime(2027, 9, 22, 12, 0, tzinfo=UTC)


async def _world(session, *, product_state=ProductState.LIVE, key="daily"):
    company = await unique_company(session, "memberships")
    unit = await add_business_unit(
        session, company_id=company.id, key="ai_media", name="AI Media",
        actor=OPERATOR, state=BusinessUnitState.ACTIVE,
    )  # fmt: skip
    product = await _product(session, company, unit, key=key, state=product_state)
    return company, unit, product


async def _product(session, company, unit, *, key, state=ProductState.LIVE):
    return await add_product(
        session, company_id=company.id, key=key, name=key.title(),
        business_unit_id=unit.id, actor=OPERATOR, state=state,
    )  # fmt: skip


async def _price(session, product, amount="1200", interval=PriceInterval.YEAR):
    return await memberships.add_price(session, product, amount=Decimal(amount), interval=interval)


async def _buy(session, price, *, reader="reader:1", ref=None, at=DAY, amount=None):
    return await memberships.purchase(
        session, price=price, customer_ref=reader, provider="payuni",
        external_ref=ref or f"T{uuid.uuid4().hex[:12]}", actor=PAYUNI, paid_at=at,
        amount=None if amount is None else Decimal(amount),
    )  # fmt: skip


async def _events(session, company, event_type):
    return (
        await session.scalars(
            select(EventRecord).where(
                EventRecord.company_id == company.id, EventRecord.event_type == event_type
            )
        )
    ).all()


# --- the calendar ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "interval", "end"),
    [
        (DAY, PriceInterval.YEAR, YEAR_LATER),
        (datetime(2028, 2, 29, tzinfo=UTC), PriceInterval.YEAR, datetime(2029, 2, 28, tzinfo=UTC)),
        (datetime(2026, 1, 31, tzinfo=UTC), PriceInterval.MONTH, datetime(2026, 2, 28, tzinfo=UTC)),
        (
            datetime(2026, 12, 15, tzinfo=UTC),
            PriceInterval.MONTH,
            datetime(2027, 1, 15, tzinfo=UTC),
        ),
    ],
)
def test_a_year_is_the_same_day_next_year(start, interval, end):
    assert memberships.add_interval(start, interval) == end


# --- prices ---------------------------------------------------------------------------------


async def test_a_retired_product_is_not_for_sale(db_session):
    _, _, product = await _world(db_session, product_state=ProductState.RETIRED)
    with pytest.raises(memberships.MembershipError, match="retired"):
        await _price(db_session, product)


async def test_a_price_is_in_the_company_s_currency_unless_told(db_session):
    _, _, product = await _world(db_session)
    price = await _price(db_session, product)
    assert (price.currency, price.interval) == ("TWD", "year")


async def test_nothing_new_is_bought_at_a_retired_price(db_session):
    _, _, product = await _world(db_session)
    price = await _price(db_session, product)
    await memberships.retire_price(db_session, price)
    with pytest.raises(memberships.MembershipError, match="retired"):
        await _buy(db_session, price)


# --- buying ---------------------------------------------------------------------------------


async def test_paying_once_buys_a_year_and_makes_a_customer(db_session):
    company, unit, product = await _world(db_session)
    payment, created = await _buy(db_session, await _price(db_session, product), ref="T1")

    assert created
    membership = await db_session.get(Membership, payment.membership_id)
    assert (membership.state, membership.started_at, membership.expires_at) == (
        "ACTIVE", DAY, YEAR_LATER,
    )  # fmt: skip
    assert (payment.grants_from, payment.grants_until) == (DAY, YEAR_LATER)
    customer = await db_session.get(Customer, payment.customer_id)
    assert (customer.external_ref, customer.kind, customer.business_unit_id) == (
        "reader:1", "subscriber", unit.id,
    )  # fmt: skip
    assert await memberships.has_access(db_session, customer.id, product.id, at=DAY)
    assert await memberships.has_access(
        db_session, customer.id, product.id, at=YEAR_LATER - timedelta(seconds=1)
    )
    assert not await memberships.has_access(db_session, customer.id, product.id, at=YEAR_LATER)
    granted = await _events(db_session, company, "MEMBERSHIP_GRANTED")
    assert len(granted) == 1 and granted[0].payload["extended"] is False


async def test_the_money_is_revenue_from_that_customer_for_that_product(db_session):
    company, unit, product = await _world(db_session)
    payment, _ = await _buy(db_session, await _price(db_session, product), ref="T1")

    row = await db_session.get(Transaction, payment.transaction_id)
    assert (row.kind, row.source, row.category) == (
        TransactionKind.REVENUE.value, TransactionSource.INTEGRATION.value, "membership",
    )  # fmt: skip
    assert (row.amount, row.currency, row.source_amount) == (Decimal("1200"), "TWD", None)
    assert (row.business_unit_id, row.product_id, row.customer_id) == (
        unit.id, product.id, payment.customer_id,
    )  # fmt: skip
    assert (row.ref_type, row.ref_id, row.idempotency_key) == (
        "payment",
        payment.id,
        "payment:payuni:T1",
    )
    assert len(await _events(db_session, company, "PAYMENT_RECEIVED")) == 1
    assert len(await _events(db_session, company, "REVENUE_RECORDED")) == 1


async def test_the_same_notification_twice_is_one_payment_one_row_one_year(db_session):
    """The property T-702's acceptance line names. It starts here."""
    company, _, product = await _world(db_session)
    price = await _price(db_session, product)
    first, created = await _buy(db_session, price, ref="T-dup")
    again, created_again = await _buy(db_session, price, ref="T-dup", at=DAY + timedelta(hours=1))

    assert created and not created_again and again.id == first.id
    assert await Ledger().balance(db_session, company.id) == Decimal("1200")
    membership = await db_session.get(Membership, first.membership_id)
    assert membership.expires_at == YEAR_LATER, "a repeated notification is not a second year"
    count = await db_session.scalar(
        select(func.count()).select_from(Payment).where(Payment.company_id == company.id)
    )
    assert count == 1


async def test_the_ledger_records_what_was_charged(db_session):
    """Whether the charge matched the order is the handler's check; the ledger says what came."""
    company, _, product = await _world(db_session)
    await _buy(db_session, await _price(db_session, product, amount="1200"), amount="990")
    assert await Ledger().balance(db_session, company.id) == Decimal("990")


# --- renewing -------------------------------------------------------------------------------


async def test_renewing_early_adds_a_year_to_what_is_left(db_session):
    """Paying in June for a year that ends in September must not throw three months away."""
    company, _, product = await _world(db_session)
    price = await _price(db_session, product)
    first, _ = await _buy(db_session, price)
    renewal, _ = await _buy(db_session, price, at=DAY + timedelta(days=270))

    assert renewal.membership_id == first.membership_id
    assert (renewal.grants_from, renewal.grants_until) == (
        YEAR_LATER,
        YEAR_LATER.replace(year=2028),
    )
    membership = await db_session.get(Membership, first.membership_id)
    assert (membership.started_at, membership.expires_at) == (DAY, YEAR_LATER.replace(year=2028))
    granted = await _events(db_session, company, "MEMBERSHIP_GRANTED")
    assert [e.payload["extended"] for e in granted] == [False, True]
    assert await customers.paying(db_session, company.id, at=DAY + timedelta(days=500)) == 1


# --- lapsing and coming back ----------------------------------------------------------------


async def test_a_year_running_out_is_the_customer_leaving(db_session):
    company, _, product = await _world(db_session)
    payment, _ = await _buy(db_session, await _price(db_session, product))

    assert (
        await memberships.expire_due(
            db_session, company.id, now=YEAR_LATER - timedelta(seconds=1), actor=PAYUNI
        )
        == []
    )
    expired = await memberships.expire_due(
        db_session, company.id, now=YEAR_LATER + timedelta(hours=3), actor=PAYUNI
    )

    assert [m.id for m in expired] == [payment.membership_id]
    assert expired[0].state == "EXPIRED"
    customer = await db_session.get(Customer, payment.customer_id)
    assert customer.churned_at == YEAR_LATER, "they left when it ran out, not when we noticed"
    assert len(await _events(db_session, company, "MEMBERSHIP_EXPIRED")) == 1
    assert len(await _events(db_session, company, "CUSTOMER_CHURNED")) == 1
    audited = await db_session.scalar(
        select(StateTransition.to_state).where(StateTransition.entity_id == payment.membership_id)
    )
    assert audited == "EXPIRED"
    # and again changes nothing
    assert (
        await memberships.expire_due(
            db_session, company.id, now=YEAR_LATER + timedelta(days=1), actor=PAYUNI
        )
        == []
    )


async def test_access_ends_on_time_even_before_the_books_catch_up(db_session):
    """EXPIRED is written once a day. Reading is decided by the date, at the moment it is asked."""
    _, _, product = await _world(db_session)
    payment, _ = await _buy(db_session, await _price(db_session, product))
    membership = await db_session.get(Membership, payment.membership_id)
    assert membership.state == "ACTIVE"
    assert not await memberships.has_access(
        db_session, payment.customer_id, product.id, at=YEAR_LATER + timedelta(minutes=1)
    )


async def test_another_product_still_running_is_not_leaving(db_session):
    company, unit, daily = await _world(db_session)
    weekly = await _product(db_session, company, unit, key="weekly")
    await _buy(db_session, await _price(db_session, daily))
    later, _ = await _buy(
        db_session, await _price(db_session, weekly), at=DAY + timedelta(days=100)
    )

    await memberships.expire_due(
        db_session, company.id, now=YEAR_LATER + timedelta(hours=1), actor=PAYUNI
    )

    customer = await db_session.get(Customer, later.customer_id)
    assert customer.churned_at is None
    assert (
        await memberships.members(db_session, company.id, at=YEAR_LATER + timedelta(hours=1)) == 1
    )


async def test_coming_back_reopens_the_same_customer_and_membership(db_session):
    company, _, product = await _world(db_session)
    price = await _price(db_session, product)
    first, _ = await _buy(db_session, price)
    await memberships.expire_due(
        db_session, company.id, now=YEAR_LATER + timedelta(days=1), actor=PAYUNI
    )

    back_at = YEAR_LATER + timedelta(days=40)
    returned, _ = await _buy(db_session, price, at=back_at)

    assert returned.customer_id == first.customer_id
    assert returned.membership_id == first.membership_id
    assert returned.grants_from == back_at, "a lapsed year is not backdated"
    membership = await db_session.get(Membership, first.membership_id)
    assert (membership.state, membership.started_at) == ("ACTIVE", back_at)
    customer = await db_session.get(Customer, first.customer_id)
    assert customer.churned_at is None
    back = await _events(db_session, company, "CUSTOMER_RETURNED")
    assert len(back) == 1 and back[0].payload["days_away"] == 40
    assert len(await _events(db_session, company, "CUSTOMER_ACQUIRED")) == 1, "not a new customer"


async def test_each_customer_is_worth_what_they_paid(db_session):
    """T-612's question, answered by real payments instead of hand-made rows."""
    company, _, product = await _world(db_session)
    price = await _price(db_session, product)
    for year in range(3):
        await _buy(db_session, price, reader="reader:loyal", at=DAY + timedelta(days=365 * year))
    await _buy(db_session, price, reader="reader:brief")

    ranked = await customers.revenue_by_customer(db_session, company.id)
    assert [(c.external_ref, total) for c, total in ranked] == [
        ("reader:loyal", Decimal("3600")),
        ("reader:brief", Decimal("1200")),
    ]


async def test_a_ledger_row_with_no_payment_behind_it_is_refused_not_guessed(db_session):
    """If the key is taken but no payment points at it, something wrote the ledger by hand.
    Returning quietly would lose a payment; writing another row is impossible. Say so."""
    company, _, product = await _world(db_session)
    price = await _price(db_session, product)
    await Ledger().record(
        db_session, company_id=company.id, kind=TransactionKind.REVENUE, category="membership",
        amount=Decimal("1200"), idempotency_key=memberships.payment_key("payuni", "T-orphan"),
        actor=OPERATOR, source=TransactionSource.HUMAN,
    )  # fmt: skip
    with pytest.raises(memberships.MembershipError, match="refusing to guess"):
        await _buy(db_session, price, ref="T-orphan")
