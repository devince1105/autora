"""T-314: minimal KPIs (cash, today's revenue and expenses, today's goal)."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from autora.company.reporting_min import load_kpis
from autora.db.models import CompanyGoal, ModelCall, Transaction
from tests.conftest import unique_company

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
YESTERDAY = NOW - timedelta(days=1)


def _tx(company_id, kind, amount, *, at=NOW - timedelta(hours=1), category="ops"):
    return Transaction(
        company_id=company_id, kind=kind, category=category, amount=Decimal(amount),
        occurred_at=at, source="system", idempotency_key=f"tx-{uuid.uuid4()}",
    )  # fmt: skip


def _call(company_id, cost, *, at=NOW - timedelta(hours=1)):
    return ModelCall(
        company_id=company_id, role="writer", capability="drafting", alias="frontier",
        provider="nvidia", model_id="m", status="ok", cost_usd=Decimal(cost), created_at=at,
    )  # fmt: skip


async def test_kpis_count_each_cost_once(db_session):
    company = await unique_company(db_session, "kpi")
    cid = company.id
    db_session.add_all([
        _tx(cid, "capital_in", "100", at=YESTERDAY),
        _tx(cid, "revenue", "12.5"),
        _tx(cid, "revenue", "3", at=YESTERDAY),         # not today
        _tx(cid, "expense", "2", category="hosting"),
        _tx(cid, "expense", "9", category="model_cost"),  # ledger copy of model calls: skipped
        _tx(cid, "capital_out", "10", at=YESTERDAY),
        _tx(cid, "transfer", "50"),                        # inside the company: ignored
        _tx(cid, "revenue", "7", at=NOW + timedelta(minutes=5)),  # not yet
        _call(cid, "0.40"),
        _call(cid, "0.10", at=YESTERDAY),
    ])  # fmt: skip
    await db_session.flush()

    kpis = await load_kpis(db_session, cid, now=NOW)
    # 100 + 12.5 + 3 - 2 - 10 - (0.40 + 0.10) = 103
    assert kpis.cash == Decimal("103.00")
    assert kpis.revenue_today == Decimal("12.5")
    assert kpis.model_cost_today == Decimal("0.40")
    assert kpis.expenses_today == Decimal("2.40")
    assert kpis.published_today == 0  # counted from ARTICLE_PUBLISHED events (T-512)
    assert kpis.goal is None and kpis.currency == "USD" and kpis.as_of == NOW


async def test_empty_company_and_isolation(db_session):
    a = await unique_company(db_session, "kpi-a")
    b = await unique_company(db_session, "kpi-b")
    db_session.add_all([_tx(b.id, "revenue", "5"), _call(b.id, "1")])
    await db_session.flush()
    kpis = await load_kpis(db_session, a.id, now=NOW)
    assert (kpis.cash, kpis.revenue_today, kpis.expenses_today) == (0, 0, 0)


async def test_goal_is_the_active_cycle_goal_due_soonest(db_session):
    company = await unique_company(db_session, "kpi-goal")
    cid = company.id

    def goal(title, **kw):
        return CompanyGoal(company_id=cid, title=title, metric="published_articles",
                           target=Decimal(3), **{"level": "cycle", **kw})  # fmt: skip

    db_session.add_all([
        goal("later", deadline=NOW + timedelta(days=2)),
        goal("today", deadline=NOW + timedelta(hours=6), current=Decimal(1)),
        goal("no deadline"),
        goal("done", deadline=NOW + timedelta(hours=1), status="achieved"),
        goal("quarter", deadline=NOW, level="quarter"),
    ])  # fmt: skip
    await db_session.flush()
    kpis = await load_kpis(db_session, cid, now=NOW)
    assert kpis.goal is not None
    assert (kpis.goal.title, kpis.goal.target, kpis.goal.current) == ("today", 3, 1)


async def test_kpis_endpoint(api, db_session):
    company = await unique_company(db_session, "kpi-api")
    db_session.add(_tx(company.id, "revenue", "4.5", at=datetime.now(UTC)))
    await db_session.flush()
    body = (await api.get(f"/api/companies/{company.id}/kpis")).json()
    assert isinstance(body["revenue_today"], str), "money stays a decimal string"
    assert Decimal(body["revenue_today"]) == Decimal("4.5") and body["currency"] == "USD"
    assert body["published_today"] == 0 and body["goal"] is None
    assert (await api.get(f"/api/companies/{uuid.uuid4()}/kpis")).status_code == 404
