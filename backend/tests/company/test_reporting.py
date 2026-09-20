"""T-603: Reporting. The core counts money; domains count their own work.

The rule these tests exist for: nothing in the company layer knows what a published article is,
and a company with no domains still gets a complete report.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from autora.company.organization import add_business_unit
from autora.company.reporting import Reporting, ReportingError, Window, history, latest
from autora.db.models import (
    BusinessUnitState,
    Cycle,
    EventRecord,
    KpiScope,
    KpiSnapshot,
    ModelCall,
    Project,
    ProjectState,
    Transaction,
    TransactionKind,
)
from autora.runtime.actor import Actor
from tests.conftest import unique_company

ACTOR = Actor.human("founder")
START = datetime(2026, 9, 21, 6, 0, tzinfo=UTC)
END = START + timedelta(hours=14)


def _reporting() -> Reporting:
    return Reporting(clock=lambda: END)


async def _world(db_session, *, with_unit: bool = True):
    company = await unique_company(db_session, "report")
    unit = None
    if with_unit:
        unit = await add_business_unit(
            db_session,
            company_id=company.id,
            key="ai_media",
            name="AI Media",
            actor=ACTOR,
            state=BusinessUnitState.ACTIVE,
        )
    project = Project(
        company_id=company.id,
        business_unit_id=unit.id if unit else None,
        name="p",
        state=ProjectState.ACTIVE.value,
        kill_criteria={"max_cost_usd": 5},
    )
    cycle = Cycle(company_id=company.id, seq=1, stage="MEASURING", started_at=START)
    db_session.add_all([project, cycle])
    await db_session.flush()
    return company, unit, project, cycle


def _call(company, project, cost, *, at=START + timedelta(hours=1)):
    return ModelCall(
        company_id=company.id, project_id=project.id if project else None,
        role="writer", capability="drafting", alias="frontier", provider="nvidia",
        model_id="m", status="ok", cost_usd=Decimal(cost), created_at=at,
    )  # fmt: skip


def _tx(company, kind, amount, *, unit=None, project=None, category="ops", at=None):
    return Transaction(
        company_id=company.id, business_unit_id=unit.id if unit else None,
        project_id=project.id if project else None, kind=kind, category=category,
        amount=Decimal(amount), occurred_at=at or START + timedelta(hours=2),
        source="system", idempotency_key=f"tx-{uuid.uuid4()}",
    )  # fmt: skip


# --- the core's own numbers ------------------------------------------------------------------


async def test_the_core_measures_money_and_nothing_else(db_session):
    company, unit, project, cycle = await _world(db_session)
    db_session.add_all([
        _call(company, project, "0.30"),
        _call(company, project, "0.20"),
        _tx(company, TransactionKind.EXPENSE, "1.50", unit=unit, project=project, category="ads"),
        _tx(company, TransactionKind.REVENUE, "10", unit=unit),
    ])  # fmt: skip
    await db_session.flush()

    metrics = await _reporting().metrics_for(
        db_session, Window(company_id=company.id, since=START, until=END)
    )

    assert metrics == {
        "cost_usd": "2.000000",
        "model_cost_usd": "0.500000",
        "revenue_usd": "10.000000",
        "profit_usd": "8.000000",
        "model_calls": 2,
    }


async def test_settled_model_costs_are_not_counted_twice(db_session):
    """The ledger posts model costs as transactions at MEASURING; the meter already counted
    them, so the settled copy is skipped — the same rule the dashboard follows."""
    company, unit, project, cycle = await _world(db_session)
    db_session.add_all([
        _call(company, project, "0.50"),
        _tx(company, TransactionKind.EXPENSE, "0.50", unit=unit, project=project,
            category="model_cost"),
    ])  # fmt: skip
    await db_session.flush()

    metrics = await _reporting().metrics_for(
        db_session, Window(company_id=company.id, since=START, until=END)
    )

    assert metrics["cost_usd"] == "0.500000"


async def test_work_outside_the_window_is_not_this_cycle_s(db_session):
    company, unit, project, cycle = await _world(db_session)
    db_session.add_all([
        _call(company, project, "0.10", at=START - timedelta(hours=1)),
        _call(company, project, "0.20"),
        _call(company, project, "0.40", at=END + timedelta(hours=1)),
    ])  # fmt: skip
    await db_session.flush()

    metrics = await _reporting().metrics_for(
        db_session, Window(company_id=company.id, since=START, until=END)
    )

    assert metrics["cost_usd"] == "0.200000"


async def test_a_business_is_measured_through_its_projects(db_session):
    company, unit, project, cycle = await _world(db_session)
    other = Project(company_id=company.id, name="elsewhere", state=ProjectState.ACTIVE.value,
                    kill_criteria={})  # fmt: skip
    db_session.add(other)
    await db_session.flush()
    db_session.add_all([
        _call(company, project, "0.30"),   # this business
        _call(company, other, "5.00"),     # company-level work
        _tx(company, TransactionKind.REVENUE, "12", unit=unit),
    ])  # fmt: skip
    await db_session.flush()
    reporting = _reporting()

    unit_metrics = await reporting.metrics_for(
        db_session,
        Window(
            company_id=company.id,
            since=START,
            until=END,
            scope=KpiScope.BUSINESS_UNIT,
            business_unit_id=unit.id,
        ),  # fmt: skip
    )
    company_metrics = await reporting.metrics_for(
        db_session, Window(company_id=company.id, since=START, until=END)
    )

    assert unit_metrics["cost_usd"] == "0.300000"
    assert unit_metrics["revenue_usd"] == "12.000000"
    assert company_metrics["cost_usd"] == "5.300000"  # the whole company, both projects


# --- domain hooks ------------------------------------------------------------------------------


async def test_a_domain_s_numbers_carry_its_name(db_session):
    company, unit, project, cycle = await _world(db_session)
    reporting = _reporting()

    async def counts(session, window, core):
        return {"widgets": 7}

    reporting.register("factory", counts)

    metrics = await reporting.metrics_for(
        db_session, Window(company_id=company.id, since=START, until=END)
    )

    assert metrics["factory.widgets"] == 7
    assert "widgets" not in metrics  # never bare: a metric says who computed it


async def test_a_hook_is_handed_what_the_core_measured(db_session):
    """So a ratio mixing a domain's unit with money has both sides from one measurement."""
    company, unit, project, cycle = await _world(db_session)
    db_session.add(_call(company, project, "2.00"))
    await db_session.flush()
    reporting = _reporting()
    seen = {}

    async def ratio(session, window, core):
        seen.update(core)
        return {"per_unit": str(Decimal(core["cost_usd"]) / 4)}

    reporting.register("factory", ratio)

    metrics = await reporting.metrics_for(
        db_session, Window(company_id=company.id, since=START, until=END)
    )

    assert seen["cost_usd"] == "2.000000"
    assert metrics["factory.per_unit"] == "0.500000"  # Decimal keeps the core's scale


async def test_a_broken_hook_loses_its_own_numbers_and_nothing_else(db_session):
    company, unit, project, cycle = await _world(db_session)
    db_session.add(_call(company, project, "1.00"))
    await db_session.flush()
    reporting = _reporting()

    async def explodes(session, window, core):
        raise RuntimeError("the counter is down")

    async def fine(session, window, core):
        return {"ok": 1}

    reporting.register("broken", explodes)
    reporting.register("working", fine)

    metrics = await reporting.metrics_for(
        db_session, Window(company_id=company.id, since=START, until=END)
    )

    assert metrics["cost_usd"] == "1.000000"
    assert metrics["working.ok"] == 1
    assert not any(key.startswith("broken.") for key in metrics)


async def test_a_domain_registers_once_and_needs_a_usable_name(db_session):
    reporting = _reporting()

    async def hook(session, window, core):
        return {}

    reporting.register("newsroom", hook)
    with pytest.raises(ReportingError, match="already contributes"):
        reporting.register("newsroom", hook)
    with pytest.raises(ReportingError, match="not usable"):
        reporting.register("news.room", hook)


async def test_a_company_with_no_domains_still_gets_a_report(db_session):
    """The industry-agnostic check, in miniature: delete every domain and this still works."""
    company, unit, project, cycle = await _world(db_session)
    db_session.add(_call(company, project, "0.75"))
    await db_session.flush()

    written = await Reporting(clock=lambda: END).measure_cycle(db_session, cycle)

    assert [s.scope for s in written] == ["company", "business_unit", "project"]
    assert all(set(s.metrics) == {
        "cost_usd", "model_cost_usd", "revenue_usd", "profit_usd", "model_calls",
    } for s in written)  # fmt: skip


# --- measuring a cycle -------------------------------------------------------------------------


async def test_a_cycle_is_measured_at_three_scopes(db_session):
    company, unit, project, cycle = await _world(db_session)
    db_session.add_all([
        _call(company, project, "0.60"),
        _tx(company, TransactionKind.REVENUE, "3", unit=unit, project=project),
    ])  # fmt: skip
    await db_session.flush()

    written = await _reporting().measure_cycle(db_session, cycle)

    by_scope = {s.scope: s for s in written}
    assert set(by_scope) == {"company", "business_unit", "project"}
    assert by_scope["company"].business_unit_id is None
    assert by_scope["business_unit"].business_unit_id == unit.id
    assert by_scope["project"].project_id == project.id
    assert all(s.cycle_id == cycle.id for s in written)
    assert all(s.period_start == START for s in written)
    assert by_scope["project"].metrics["revenue_usd"] == "3.000000"


async def test_measuring_twice_updates_in_place(db_session):
    """MEASURING can be entered again after a restart; it must not double the record."""
    company, unit, project, cycle = await _world(db_session)
    db_session.add(_call(company, project, "0.10"))
    await db_session.flush()
    reporting = _reporting()
    await reporting.measure_cycle(db_session, cycle)

    db_session.add(_call(company, project, "0.05"))
    await db_session.flush()
    await reporting.measure_cycle(db_session, cycle)

    rows = (
        await db_session.scalars(select(KpiSnapshot).where(KpiSnapshot.company_id == company.id))
    ).all()
    assert len(rows) == 3  # one per scope, not six
    company_row = next(r for r in rows if r.scope == "company")
    assert company_row.metrics["cost_usd"] == "0.150000"  # recomputed, not added


async def test_each_new_measurement_says_so_once(db_session):
    company, unit, project, cycle = await _world(db_session)
    reporting = _reporting()

    await reporting.measure_cycle(db_session, cycle)
    await reporting.measure_cycle(db_session, cycle)

    events = (
        await db_session.scalars(
            select(EventRecord).where(
                EventRecord.company_id == company.id,
                EventRecord.event_type == "KPI_SNAPSHOT_CREATED",
            )
        )
    ).all()
    assert len(events) == 3  # the second measurement updated rows; it created none
    assert all(e.cycle_id == cycle.id for e in events)


async def test_a_finished_cycle_is_measured_to_its_end(db_session):
    company, unit, project, cycle = await _world(db_session)
    cycle.stage, cycle.ended_at = "DONE", START + timedelta(hours=10)
    await db_session.flush()

    (row, *_) = await _reporting().measure_cycle(db_session, cycle)

    assert row.period_end == START + timedelta(hours=10)


async def test_a_killed_project_is_not_measured_any_more(db_session):
    company, unit, project, cycle = await _world(db_session)
    project.state = ProjectState.KILLED.value
    await db_session.flush()

    written = await _reporting().measure_cycle(db_session, cycle)

    assert [s.scope for s in written] == ["company", "business_unit"]


async def test_the_stage_hook_measures(db_session):
    company, unit, project, cycle = await _world(db_session)

    await _reporting().stage_hook()(db_session, cycle)

    assert await latest(db_session, company.id) is not None


# --- reading -----------------------------------------------------------------------------------


async def test_the_latest_and_the_history_of_a_scope(db_session):
    company, unit, project, cycle = await _world(db_session)
    reporting = _reporting()
    for seq in range(1, 4):
        this_cycle = Cycle(
            company_id=company.id,
            seq=seq + 1,
            stage="DONE",
            started_at=START + timedelta(days=seq),
            ended_at=END + timedelta(days=seq),
        )
        db_session.add(this_cycle)
        await db_session.flush()
        db_session.add(
            _call(company, project, f"0.{seq}0", at=START + timedelta(days=seq, hours=1))
        )
        await db_session.flush()
        await reporting.measure_cycle(db_session, this_cycle)

    newest = await latest(db_session, company.id)
    trend = await history(db_session, company.id, limit=2)

    assert newest.metrics["cost_usd"] == "0.300000"
    assert [row.metrics["cost_usd"] for row in trend] == ["0.300000", "0.200000"]


async def test_an_unmeasured_company_has_nothing_to_read(db_session):
    assert await latest(db_session, uuid.uuid4()) is None
    assert await history(db_session, uuid.uuid4()) == []
