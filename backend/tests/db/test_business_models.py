"""T-701: the tables the first revenue model needs, and what the database itself refuses.

The service (``company.subscriptions``) keeps these rules too, but a webhook handler written in
a hurry, a script, or a migration can go around a service. It cannot go around a constraint.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from autora.company import subscriptions
from autora.company.organization import add_business_unit, add_product
from autora.db.models import (
    BusinessUnitState,
    Payment,
    Price,
    PriceInterval,
    ProductState,
    Subscription,
)
from autora.runtime.actor import Actor
from tests.conftest import unique_company

OPERATOR = Actor.human("operator")
STRIPE = Actor.system("payments:stripe")
DAY = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
FORBIDDEN = ("name", "email", "phone", "address", "card", "ssn", "birth", "ip")


async def _priced(session):
    company = await unique_company(session, "business-models")
    unit = await add_business_unit(
        session, company_id=company.id, key="ai_media", name="AI Media",
        actor=OPERATOR, state=BusinessUnitState.ACTIVE,
    )  # fmt: skip
    product = await add_product(
        session, company_id=company.id, key="daily", name="Daily",
        business_unit_id=unit.id, actor=OPERATOR, state=ProductState.LIVE,
    )  # fmt: skip
    price = await subscriptions.add_price(
        session, product, amount=Decimal("5"), interval=PriceInterval.MONTH,
        provider="stripe", external_ref=f"price_{uuid.uuid4().hex[:8]}",
    )  # fmt: skip
    return company, price


async def _paid(session):
    company, price = await _priced(session)
    sub = await subscriptions.start(
        session, price=price, customer_ref=f"cus_{uuid.uuid4().hex[:8]}",
        external_ref=f"sub_{uuid.uuid4().hex[:8]}", actor=STRIPE, started_at=DAY,
    )  # fmt: skip
    payment, _ = await subscriptions.record_payment(
        session, sub, external_ref=f"in_{uuid.uuid4().hex[:8]}", amount=Decimal("5"),
        actor=STRIPE, paid_at=DAY,
    )  # fmt: skip
    return sub, payment


@pytest.mark.parametrize("model", [Price, Subscription, Payment])
def test_no_table_holds_anything_a_person_could_be_identified_by(model):
    """D-018's boundary, extended: the provider keeps the person, the company keeps references."""
    columns = {column.name for column in inspect(model).columns}
    assert [c for c in columns if any(word in c.split("_") for word in FORBIDDEN)] == []


async def test_a_payment_cannot_be_edited_or_deleted(db_session):
    """Like the ledger it feeds: a refund is a new row, never a changed one."""
    _, payment = await _paid(db_session)
    for statement in (
        "UPDATE payments SET amount = 1 WHERE id = :id",
        "DELETE FROM payments WHERE id = :id",
    ):
        with pytest.raises(DBAPIError, match="append-only"):
            async with db_session.begin_nested():
                await db_session.execute(text(statement), {"id": payment.id})


async def test_the_same_provider_charge_cannot_be_two_payments(db_session):
    sub, payment = await _paid(db_session)
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(
                Payment(
                    company_id=sub.company_id,
                    business_unit_id=sub.business_unit_id,
                    customer_id=sub.customer_id,
                    subscription_id=sub.id,
                    transaction_id=payment.transaction_id,
                    provider="stripe",
                    external_ref=payment.external_ref,
                    amount=Decimal("5"),
                    paid_at=DAY,
                )
            )
            await db_session.flush()


async def test_a_canceled_subscription_says_when(db_session):
    sub, _ = await _paid(db_session)
    with pytest.raises(IntegrityError, match="canceled_has_a_date"):
        async with db_session.begin_nested():
            await db_session.execute(
                text("UPDATE subscriptions SET state = 'CANCELED' WHERE id = :id"),
                {"id": sub.id},
            )


@pytest.mark.parametrize(
    ("statement", "constraint"),
    [
        ("UPDATE prices SET amount = 0 WHERE id = :id", "amount_positive"),
        ("UPDATE prices SET interval = 'week' WHERE id = :id", "interval_valid"),
        ("UPDATE prices SET provider = 'Stripe Inc' WHERE id = :id", "provider_format"),
        ("UPDATE prices SET currency = 'usd' WHERE id = :id", "currency_format"),
    ],
)
async def test_a_price_is_positive_per_month_or_year_from_a_named_provider(
    db_session, statement, constraint
):
    _, price = await _priced(db_session)
    with pytest.raises(IntegrityError, match=constraint):
        async with db_session.begin_nested():
            await db_session.execute(text(statement), {"id": price.id})
