"""T-701 / D-024: the tables the first revenue model needs, and what the database itself refuses.

The service (``company.memberships``) keeps these rules too, but a notification handler written
in a hurry, a script, or a migration can go around a service. It cannot go around a constraint.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from autora.company import memberships
from autora.company.organization import add_business_unit, add_product
from autora.db.models import BusinessUnitState, Membership, Payment, Price, ProductState
from autora.runtime.actor import Actor
from tests.conftest import unique_company

OPERATOR = Actor.human("operator")
PAYUNI = Actor.system("payments:payuni")
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
    price = await memberships.add_price(session, product, amount=Decimal("1200"))
    return company, price


async def _paid(session):
    company, price = await _priced(session)
    payment, _ = await memberships.purchase(
        session, price=price, customer_ref=f"reader:{uuid.uuid4()}", provider="payuni",
        external_ref=f"T{uuid.uuid4().hex[:12]}", actor=PAYUNI, paid_at=DAY,
    )  # fmt: skip
    return payment


@pytest.mark.parametrize("model", [Price, Membership, Payment])
def test_no_table_holds_anything_a_person_could_be_identified_by(model):
    """D-018's boundary: the reader table keeps the person (D-024), these keep references."""
    columns = {column.name for column in inspect(model).columns}
    assert [c for c in columns if any(word in c.split("_") for word in FORBIDDEN)] == []


async def test_a_payment_cannot_be_edited_or_deleted(db_session):
    """Like the ledger it feeds: a refund is a new row, never a changed one."""
    payment = await _paid(db_session)
    for statement in (
        "UPDATE payments SET amount = 1 WHERE id = :id",
        "DELETE FROM payments WHERE id = :id",
    ):
        with pytest.raises(DBAPIError, match="append-only"):
            async with db_session.begin_nested():
                await db_session.execute(text(statement), {"id": payment.id})


async def test_the_same_provider_charge_cannot_be_two_payments(db_session):
    payment = await _paid(db_session)
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(
                Payment(
                    company_id=payment.company_id,
                    business_unit_id=payment.business_unit_id,
                    customer_id=payment.customer_id,
                    transaction_id=payment.transaction_id,
                    provider="payuni",
                    external_ref=payment.external_ref,
                    amount=Decimal("1200"),
                    paid_at=DAY,
                )
            )
            await db_session.flush()


@pytest.mark.parametrize(
    ("membership", "grants_from", "grants_until"),
    [
        (True, None, None),  # bought access but does not say which stretch
        (False, 0, 365),  # a stretch of nothing
        (True, 365, 0),  # backwards
    ],
)
async def test_a_payment_that_bought_access_says_exactly_what(
    db_session, membership, grants_from, grants_until
):
    """Checked on insert, the only write a payment ever gets."""
    payment = await _paid(db_session)
    at = {None: None, 0: DAY, 365: DAY + timedelta(days=365)}
    with pytest.raises(IntegrityError, match="grant_complete"):
        async with db_session.begin_nested():
            db_session.add(
                Payment(
                    company_id=payment.company_id,
                    business_unit_id=payment.business_unit_id,
                    customer_id=payment.customer_id,
                    transaction_id=payment.transaction_id,
                    provider="payuni",
                    external_ref="other",
                    amount=Decimal("1200"),
                    paid_at=DAY,
                    membership_id=payment.membership_id if membership else None,
                    grants_from=at[grants_from],
                    grants_until=at[grants_until],
                )
            )
            await db_session.flush()


async def test_a_membership_ends_after_it_starts(db_session):
    payment = await _paid(db_session)
    with pytest.raises(IntegrityError, match="expires_after_start"):
        async with db_session.begin_nested():
            await db_session.execute(
                text("UPDATE memberships SET expires_at = started_at WHERE id = :id"),
                {"id": payment.membership_id},
            )


async def test_one_membership_per_customer_and_product(db_session):
    payment = await _paid(db_session)
    membership = await db_session.get(Membership, payment.membership_id)
    with pytest.raises(IntegrityError, match="uq_memberships_customer_id_product_id"):
        async with db_session.begin_nested():
            db_session.add(
                Membership(
                    company_id=membership.company_id,
                    business_unit_id=membership.business_unit_id,
                    customer_id=membership.customer_id,
                    product_id=membership.product_id,
                    state="ACTIVE",
                    started_at=DAY,
                    expires_at=DAY + timedelta(days=1),
                )
            )
            await db_session.flush()


@pytest.mark.parametrize(
    ("statement", "constraint"),
    [
        ("UPDATE prices SET amount = 0 WHERE id = :id", "amount_positive"),
        ("UPDATE prices SET interval = 'week' WHERE id = :id", "interval_valid"),
        ("UPDATE prices SET currency = 'twd' WHERE id = :id", "currency_format"),
    ],
)
async def test_a_price_is_positive_per_month_or_year(db_session, statement, constraint):
    _, price = await _priced(db_session)
    with pytest.raises(IntegrityError, match=constraint):
        async with db_session.begin_nested():
            await db_session.execute(text(statement), {"id": price.id})
