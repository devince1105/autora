"""T-612: who pays the company, and what they are worth (ARCHITECTURE_V2_1 §7).

The table is small on purpose and the tests say why: it must answer "what does each business,
product and customer earn" on the day the first payment arrives, and it must hold nothing about
a person that anybody would have to protect.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError, StatementError

from autora.company import customers
from autora.company.ledger import Ledger
from autora.company.organization import add_business_unit
from autora.db.models import (
    BusinessUnitState,
    Customer,
    CustomerKind,
    EventRecord,
    TransactionKind,
    TransactionSource,
)
from autora.runtime.actor import Actor
from tests.conftest import unique_company

OPERATOR = Actor.human("operator")
DAY = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


async def _world(session):
    company = await unique_company(session, "customers")
    unit = await add_business_unit(
        session, company_id=company.id, key="ai_media", name="AI Media",
        actor=OPERATOR, state=BusinessUnitState.ACTIVE,
    )  # fmt: skip
    return company, unit


async def _paid(session, company, customer, amount, *, unit=None, at=None):
    return await Ledger().record(
        session,
        company_id=company.id,
        kind=TransactionKind.REVENUE,
        category="subscription",
        amount=Decimal(amount),
        customer_id=customer.id,
        business_unit_id=unit.id if unit else None,
        occurred_at=at or DAY,
        source=TransactionSource.INTEGRATION,
        idempotency_key=f"pay-{uuid.uuid4().hex[:12]}",
    )


# --- what the table is and is not --------------------------------------------------------------


def test_a_customer_is_a_reference_and_nothing_a_person_could_be_identified_by():
    """The boundary is the design: what would need protecting is not here to protect."""
    columns = {column.name for column in inspect(Customer).columns}
    assert columns == {
        "id",
        "company_id",
        "business_unit_id",
        "external_ref",
        "kind",
        "acquired_at",
        "churned_at",
        "created_at",
        "updated_at",
    }
    forbidden = ("name", "email", "phone", "address", "card", "ssn", "birth", "ip")
    assert [c for c in columns if any(word in c for word in forbidden)] == []


async def test_the_same_payment_reference_is_the_same_customer(db_session):
    """A webhook delivered twice must not make two customers, and the provider's id is the only
    thing both deliveries agree on."""
    company, unit = await _world(db_session)
    first = await customers.acquire(
        db_session, company_id=company.id, external_ref="cus_123",
        kind=CustomerKind.SUBSCRIBER, actor=OPERATOR, business_unit_id=unit.id,
    )  # fmt: skip
    again = await customers.acquire(
        db_session, company_id=company.id, external_ref="cus_123",
        kind=CustomerKind.SUBSCRIBER, actor=OPERATOR, business_unit_id=unit.id,
    )  # fmt: skip

    assert again.id == first.id
    rows = (
        await db_session.scalars(select(Customer).where(Customer.company_id == company.id))
    ).all()
    assert len(rows) == 1
    acquired = await db_session.scalars(
        select(EventRecord).where(
            EventRecord.company_id == company.id,
            EventRecord.event_type == "CUSTOMER_ACQUIRED",
        )
    )
    assert len(list(acquired)) == 1


async def test_two_companies_may_know_the_same_reference(db_session):
    one, _ = await _world(db_session)
    two, _ = await _world(db_session)
    a = await customers.acquire(
        db_session, company_id=one.id, external_ref="cus_123",
        kind=CustomerKind.SPONSOR, actor=OPERATOR,
    )  # fmt: skip
    b = await customers.acquire(
        db_session, company_id=two.id, external_ref="cus_123",
        kind=CustomerKind.SPONSOR, actor=OPERATOR,
    )  # fmt: skip
    assert a.id != b.id


async def test_nobody_is_not_a_customer(db_session):
    company, _ = await _world(db_session)
    with pytest.raises(customers.CustomerError):
        await customers.acquire(
            db_session, company_id=company.id, external_ref="  ",
            kind=CustomerKind.CLIENT, actor=OPERATOR,
        )  # fmt: skip


# --- leaving --------------------------------------------------------------------------------


async def test_churning_keeps_the_row_and_says_how_long_they_stayed(db_session):
    company, unit = await _world(db_session)
    customer = await customers.acquire(
        db_session, company_id=company.id, external_ref="cus_1", kind=CustomerKind.SUBSCRIBER,
        actor=OPERATOR, business_unit_id=unit.id, acquired_at=DAY - timedelta(days=40),
    )  # fmt: skip
    await _paid(db_session, company, customer, "9.99", unit=unit, at=DAY - timedelta(days=30))

    await customers.churn(
        db_session, customer, actor=OPERATOR, reason="stopped reading", churned_at=DAY
    )

    assert customer.churned_at == DAY
    [event] = (
        await db_session.scalars(
            select(EventRecord).where(
                EventRecord.company_id == company.id,
                EventRecord.event_type == "CUSTOMER_CHURNED",
            )
        )
    ).all()
    assert event.payload["days"] == 40 and event.payload["reason"] == "stopped reading"
    # what they paid still happened, and is still theirs
    [(who, total)] = await customers.revenue_by_customer(db_session, company.id)
    assert who.id == customer.id and total == Decimal("9.990000")
    # churning twice is not two departures
    await customers.churn(db_session, customer, actor=OPERATOR, churned_at=DAY + timedelta(days=1))
    assert customer.churned_at == DAY


async def test_the_database_refuses_somebody_who_left_before_they_arrived(db_session):
    company, _ = await _world(db_session)
    customer = await customers.acquire(
        db_session, company_id=company.id, external_ref="cus_1", kind=CustomerKind.CLIENT,
        actor=OPERATOR, acquired_at=DAY,
    )  # fmt: skip
    customer.churned_at = DAY - timedelta(days=1)
    with pytest.raises((IntegrityError, StatementError)):
        await db_session.flush()


# --- what they are worth ----------------------------------------------------------------------


async def test_revenue_by_customer_answers_the_last_question_of_section_seven(db_session):
    company, unit = await _world(db_session)
    big = await customers.acquire(
        db_session, company_id=company.id, external_ref="cus_big", kind=CustomerKind.SPONSOR,
        actor=OPERATOR, business_unit_id=unit.id, acquired_at=DAY - timedelta(days=10),
    )  # fmt: skip
    small = await customers.acquire(
        db_session, company_id=company.id, external_ref="cus_small",
        kind=CustomerKind.SUBSCRIBER, actor=OPERATOR, business_unit_id=unit.id,
        acquired_at=DAY - timedelta(days=10),
    )  # fmt: skip
    await _paid(db_session, company, big, "500", unit=unit)
    await _paid(db_session, company, small, "9.99", unit=unit)
    await _paid(db_session, company, small, "9.99", unit=unit, at=DAY + timedelta(days=30))
    # revenue from nobody in particular: it is the company's, but it is not anybody's
    await Ledger().record(
        db_session, company_id=company.id, kind=TransactionKind.REVENUE, category="grant",
        amount=Decimal("100"), occurred_at=DAY, idempotency_key=f"g-{uuid.uuid4().hex[:8]}",
    )  # fmt: skip

    ranked = await customers.revenue_by_customer(db_session, company.id)

    assert [(c.external_ref, str(total)) for c, total in ranked] == [
        ("cus_big", "500.000000"),
        ("cus_small", "19.980000"),
    ]
    # a window is a window: the second subscription is outside this one
    windowed = await customers.revenue_by_customer(
        db_session, company.id, until=DAY + timedelta(days=1)
    )
    assert dict((c.external_ref, str(t)) for c, t in windowed)["cus_small"] == "9.990000"


async def test_counting_who_was_paying_at_a_moment(db_session):
    company, unit = await _world(db_session)
    other = await add_business_unit(
        db_session, company_id=company.id, key="ai_edu", name="AI Education",
        actor=OPERATOR, state=BusinessUnitState.ACTIVE,
    )  # fmt: skip
    stayed = await customers.acquire(
        db_session, company_id=company.id, external_ref="cus_stayed",
        kind=CustomerKind.SUBSCRIBER, actor=OPERATOR, business_unit_id=unit.id,
        acquired_at=DAY - timedelta(days=30),
    )  # fmt: skip
    left = await customers.acquire(
        db_session, company_id=company.id, external_ref="cus_left",
        kind=CustomerKind.SUBSCRIBER, actor=OPERATOR, business_unit_id=unit.id,
        acquired_at=DAY - timedelta(days=30),
    )  # fmt: skip
    await customers.acquire(
        db_session, company_id=company.id, external_ref="cus_elsewhere",
        kind=CustomerKind.CLIENT, actor=OPERATOR, business_unit_id=other.id, acquired_at=DAY,
    )  # fmt: skip
    await customers.churn(db_session, left, actor=OPERATOR, churned_at=DAY - timedelta(days=1))

    assert await customers.paying(db_session, company.id, at=DAY) == 2
    assert await customers.paying(db_session, company.id, business_unit_id=unit.id, at=DAY) == 1
    # a month ago both were still paying and the third had not arrived
    assert await customers.paying(db_session, company.id, at=DAY - timedelta(days=5)) == 2
    assert await customers.paying(db_session, company.id, at=DAY - timedelta(days=60)) == 0
    assert stayed.churned_at is None


async def test_the_kpis_count_the_paying_ones_per_business(db_session):
    """Reporting already carried the money; customers are the other half of a business's P&L."""
    from autora.company.reporting import Reporting, Window
    from autora.db.models import KpiScope

    company, unit = await _world(db_session)
    await customers.acquire(
        db_session, company_id=company.id, external_ref="cus_1",
        kind=CustomerKind.SUBSCRIBER, actor=OPERATOR, business_unit_id=unit.id,
        acquired_at=DAY - timedelta(days=1),
    )  # fmt: skip
    await db_session.flush()

    window = Window(
        company_id=company.id,
        since=DAY - timedelta(days=7),
        until=DAY,
        scope=KpiScope.BUSINESS_UNIT,
        business_unit_id=unit.id,
    )
    metrics = await Reporting().metrics_for(db_session, window)
    assert metrics["customers"] == 1
