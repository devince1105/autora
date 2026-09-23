"""T-707: what the memberships add up to — in the cycle's report, and so in the CEO's snapshot.

Each test builds a small history of real purchases (``memberships.purchase``, the path a PAYUNi
notification takes) and asks what a period looked like. The hard cases are about time: a renewal
must not rewrite who was a member last month, and somebody who lapsed and came back is a renewal,
not a new member.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from autora.company import memberships, revenue
from autora.company.ledger import Ledger
from autora.company.organization import add_business_unit, add_product
from autora.company.reporting import Reporting, Window
from autora.db.models import BusinessUnitState, KpiScope, PriceInterval, Product, ProductState
from autora.infra.money import Fx
from autora.runtime.actor import Actor
from tests.conftest import unique_company

OPERATOR = Actor.human("operator")
PAYUNI = Actor.system("payments:payuni")
DAY = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
TWD = Fx(base="TWD", rates={"USD": Decimal("32")})


async def _world(session, units=("ai_media",)):
    company = await unique_company(session, "revenue")
    prices = []
    for i, key in enumerate(units):
        unit = await add_business_unit(
            session, company_id=company.id, key=key, name=key,
            actor=OPERATOR, state=BusinessUnitState.ACTIVE,
        )  # fmt: skip
        product = await add_product(
            # the first business sells the company's membership — the product the site prices
            session, company_id=company.id,
            key=memberships.PRODUCT_KEY if i == 0 else f"{key}_membership", name="Membership",
            business_unit_id=unit.id, actor=OPERATOR, state=ProductState.LIVE,
        )  # fmt: skip
        prices.append(
            await memberships.add_price(
                session, product, amount=Decimal("360"), interval=PriceInterval.YEAR
            )
        )
    return company, prices


async def _pay(session, price, reader, at):
    payment, _ = await memberships.purchase(
        session, price=price, customer_ref=reader, provider="payuni",
        external_ref=f"T{uuid.uuid4().hex[:12]}", actor=PAYUNI, paid_at=at, ledger=Ledger(fx=TWD),
    )  # fmt: skip
    return payment


async def _numbers(session, company, since, until, unit=None):
    return await revenue.membership_numbers(
        session, company.id, since=since, until=until, business_unit_id=unit
    )


async def test_a_first_payment_is_a_new_member_and_every_later_one_a_renewal(db_session):
    company, [price] = await _world(db_session)
    await _pay(db_session, price, "reader:a", DAY)
    await _pay(db_session, price, "reader:b", DAY + timedelta(days=5))
    await _pay(db_session, price, "reader:a", DAY + timedelta(days=300))  # early: extends the year

    first_month = await _numbers(
        db_session, company, DAY - timedelta(days=1), DAY + timedelta(days=30)
    )
    assert (first_month.payments, first_month.new_members, first_month.renewals) == (2, 2, 0)

    the_renewal = await _numbers(
        db_session, company, DAY + timedelta(days=290), DAY + timedelta(days=310)
    )
    assert (the_renewal.payments, the_renewal.new_members, the_renewal.renewals) == (1, 0, 1)


async def test_somebody_who_lapsed_and_came_back_is_a_renewal_not_a_new_member(db_session):
    company, [price] = await _world(db_session)
    await _pay(db_session, price, "reader:c", DAY)
    back = DAY + timedelta(days=500)  # the year ran out at day 365
    await _pay(db_session, price, "reader:c", back)
    numbers = await _numbers(
        db_session, company, back - timedelta(days=1), back + timedelta(days=1)
    )
    assert (numbers.new_members, numbers.renewals, numbers.members) == (0, 1, 1)


async def test_members_are_whoever_holds_access_at_the_end_of_the_period(db_session):
    company, [price] = await _world(db_session)
    await _pay(db_session, price, "reader:a", DAY)
    await _pay(db_session, price, "reader:b", DAY + timedelta(days=200))
    halfway = await _numbers(db_session, company, DAY, DAY + timedelta(days=100))
    later = await _numbers(db_session, company, DAY, DAY + timedelta(days=250))
    assert (halfway.members, later.members) == (1, 2)


async def test_a_renewal_does_not_rewrite_who_was_a_member_last_month(db_session):
    """The reason the numbers come from payments: the membership row forgets the past."""
    company, [price] = await _world(db_session)
    await _pay(db_session, price, "reader:a", DAY)
    lapse = DAY + timedelta(days=400)  # a's year ended on day 365
    before = await _numbers(db_session, company, lapse - timedelta(days=10), lapse)
    assert before.members == 0

    await _pay(db_session, price, "reader:a", lapse + timedelta(days=20))  # comes back later
    again = await _numbers(db_session, company, lapse - timedelta(days=10), lapse)
    assert again.members == 0, "coming back later does not make them a member back then"


async def test_expiring_members_are_the_ones_a_renewal_reminder_is_for(db_session):
    company, [price] = await _world(db_session)
    await _pay(db_session, price, "reader:soon", DAY)  # ends day 365
    await _pay(db_session, price, "reader:later", DAY + timedelta(days=100))  # ends day 465
    end = DAY + timedelta(days=350)
    numbers = await _numbers(db_session, company, end - timedelta(days=30), end)
    assert (numbers.members, numbers.expiring_members) == (2, 1)


async def test_a_member_who_renewed_early_is_not_about_to_expire(db_session):
    company, [price] = await _world(db_session)
    await _pay(db_session, price, "reader:a", DAY)
    await _pay(db_session, price, "reader:a", DAY + timedelta(days=340))  # now covered to day 730
    end = DAY + timedelta(days=350)
    assert (
        await _numbers(db_session, company, end - timedelta(days=30), end)
    ).expiring_members == 0


async def test_lapsed_members_are_those_whose_access_ran_out_in_the_period_and_did_not_pay_again(
    db_session,
):
    company, [price] = await _world(db_session)
    await _pay(db_session, price, "reader:gone", DAY)  # runs out day 365
    await _pay(db_session, price, "reader:stayed", DAY)
    await _pay(db_session, price, "reader:stayed", DAY + timedelta(days=360))  # renewed in time
    period = await _numbers(
        db_session, company, DAY + timedelta(days=350), DAY + timedelta(days=380)
    )
    assert (period.lapsed_members, period.members) == (1, 1)
    # somebody already gone is not "about to expire": a renewal reminder is for current members
    assert period.expiring_members == 0


async def test_the_average_payment_is_membership_revenue_over_payments_and_none_for_no_payments(
    db_session,
):
    company, [price] = await _world(db_session)
    await _pay(db_session, price, "reader:a", DAY)
    await _pay(db_session, price, "reader:b", DAY)
    numbers = await _numbers(db_session, company, DAY - timedelta(days=1), DAY + timedelta(days=1))
    assert numbers.membership_revenue == Decimal("720")
    assert numbers.average_payment == Decimal("360")
    quiet = await _numbers(db_session, company, DAY + timedelta(days=10), DAY + timedelta(days=20))
    assert quiet.payments == 0 and quiet.average_payment is None


async def test_a_business_counts_its_own_members_only(db_session):
    company, [news, school] = await _world(db_session, units=("ai_media", "ai_education"))
    await _pay(db_session, news, "reader:a", DAY)
    await _pay(db_session, school, "reader:a", DAY)
    await _pay(db_session, school, "reader:b", DAY)
    school_unit = (await db_session.get(Product, school.product_id)).business_unit_id
    around = (DAY - timedelta(days=1), DAY + timedelta(days=1))
    whole = await _numbers(db_session, company, *around)
    only = await _numbers(db_session, company, *around, unit=school_unit)
    assert (whole.payments, whole.members) == (3, 2)
    assert (only.payments, only.members, only.new_members) == (2, 2, 2)


async def test_the_cycle_report_carries_the_memberships_for_the_company_and_each_business(
    db_session,
):
    company, [price] = await _world(db_session)
    await _pay(db_session, price, "reader:a", DAY)
    reporting = Reporting(fx=TWD)
    window = Window(
        company_id=company.id, since=DAY - timedelta(days=1), until=DAY + timedelta(days=1)
    )
    metrics = await reporting.metrics_for(db_session, window)
    assert metrics["payments"] == 1
    assert metrics["new_members"] == 1
    assert metrics["members"] == 1
    assert Decimal(metrics["membership_revenue"]) == Decimal("360")
    # bare names: the core's own numbers, not a domain's
    assert not any(key.startswith("company.") for key in metrics)


async def test_a_project_is_not_asked_about_members_it_cannot_have(db_session):
    company, [price] = await _world(db_session)
    await _pay(db_session, price, "reader:a", DAY)
    window = Window(
        company_id=company.id, since=DAY - timedelta(days=1), until=DAY + timedelta(days=1),
        scope=KpiScope.PROJECT, project_id=uuid.uuid4(),
    )  # fmt: skip
    assert "members" not in await Reporting(fx=TWD).metrics_for(db_session, window)


async def test_daily_revenue_has_every_day_and_zero_for_the_quiet_ones(db_session):
    company, [price] = await _world(db_session)
    await _pay(db_session, price, "reader:a", DAY)
    await _pay(db_session, price, "reader:b", DAY + timedelta(days=2))
    days = await revenue.daily_revenue(
        db_session, company.id, days=5, until=DAY + timedelta(days=2, hours=3)
    )
    assert [d.day.isoformat() for d in days] == [
        "2026-01-08",
        "2026-01-09",
        "2026-01-10",
        "2026-01-11",
        "2026-01-12",
    ]
    assert [d.amount for d in days] == [
        Decimal(0),
        Decimal(0),
        Decimal(360),
        Decimal(0),
        Decimal(360),
    ]


@pytest.mark.parametrize("hours", [0, 23])
async def test_a_day_is_a_utc_day(db_session, hours):
    company, [price] = await _world(db_session)
    await _pay(db_session, price, "reader:a", datetime(2026, 1, 10, hours, 30, tzinfo=UTC))
    days = await revenue.daily_revenue(
        db_session, company.id, days=1, until=datetime(2026, 1, 10, 23, 59, tzinfo=UTC)
    )
    assert days[0].amount == Decimal(360)


# --- the dashboard's view of the same numbers --------------------------------------------------


async def test_the_dashboard_shows_the_last_30_days_and_where_the_memberships_stand(db_session):
    from autora.company.reporting_min import load_kpis

    company, [price] = await _world(db_session)
    now = DAY + timedelta(days=340)
    await _pay(db_session, price, "reader:old", DAY)  # its year ends 25 days from now: expiring
    await _pay(db_session, price, "reader:new", now - timedelta(days=3))  # new this month
    await _pay(
        db_session, price, "reader:old", now - timedelta(days=1, hours=2)
    )  # an early renewal
    view = (await load_kpis(db_session, company.id, now=now)).revenue

    assert view is not None and view.days == 30
    assert len(view.daily) == 30 and view.daily[-1].day == now.date()
    assert view.total == Decimal("720")
    assert (view.payments, view.new_members, view.renewals) == (2, 1, 1)
    assert view.members == 2
    assert view.expiring_members == 0, "the one about to expire renewed in time"
    assert view.average_payment == Decimal("360")
    assert view.offer is not None and view.offer.amount == Decimal("360")


async def test_the_dashboard_and_the_cycle_report_agree(db_session):
    """Both ask ``company.revenue``; this holds them to it."""
    from autora.company.reporting_min import load_kpis

    company, [price] = await _world(db_session)
    now = DAY + timedelta(days=10)
    await _pay(db_session, price, "reader:a", now - timedelta(days=2))
    await _pay(db_session, price, "reader:b", now - timedelta(days=40))  # before the 30 days
    view = (await load_kpis(db_session, company.id, now=now)).revenue
    report = await Reporting(fx=TWD).metrics_for(
        db_session, Window(company_id=company.id, since=now - timedelta(days=30), until=now)
    )
    assert view is not None
    assert (view.payments, view.new_members, view.members) == (
        report["payments"], report["new_members"], report["members"]
    )  # fmt: skip


async def test_a_company_that_sells_nothing_says_so_rather_than_inventing_a_price(db_session):
    from autora.company.reporting_min import load_kpis

    company = await unique_company(db_session, "no-sales")
    view = (await load_kpis(db_session, company.id, now=DAY)).revenue
    assert view is not None
    assert view.offer is None and view.members == 0 and view.average_payment is None
    assert all(d.amount == 0 for d in view.daily)


async def test_the_kpis_endpoint_carries_the_revenue_block(api, db_session):
    company, [price] = await _world(db_session)
    await _pay(db_session, price, "reader:a", datetime.now(UTC) - timedelta(hours=1))
    body = (await api.get(f"/api/companies/{company.id}/kpis")).json()
    block = body["revenue"]
    assert isinstance(block["total"], str), "money stays a decimal string"
    assert Decimal(block["total"]) == Decimal("360")
    assert (block["members"], block["new_members"]) == (1, 1)
    assert len(block["daily"]) == 30
