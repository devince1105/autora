"""T-603: the CompanySnapshot — everything the CEO is given, and nothing else.

Two things these tests are really about: the document is organised around the businesses the
company runs, and when it does not fit it says so instead of quietly losing the interesting
parts.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from autora.company.ledger import Ledger
from autora.company.organization import add_business_unit, add_product
from autora.company.reporting import Reporting
from autora.company.snapshot import (
    DEFAULT_TOKEN_BUDGET,
    HUMAN_NOTES_POLICY,
    STRATEGY_POLICY,
    TOKENS_POLICY,
    SnapshotBuilder,
)
from autora.db.models import (
    Budget,
    BusinessUnitState,
    CompanyGoal,
    Cycle,
    ProductState,
    Project,
    ProjectState,
    TransactionKind,
)
from autora.db.repositories.companies import upsert_policy
from autora.runtime.actor import Actor
from tests.conftest import unique_company

ACTOR = Actor.human("founder")
START = datetime(2026, 9, 21, 6, 0, tzinfo=UTC)
NOW = START + timedelta(hours=8)


def _builder(hooks: dict | None = None) -> SnapshotBuilder:
    reporting = Reporting(clock=lambda: NOW)
    builder = SnapshotBuilder(reporting, Ledger(clock=lambda: NOW))
    for name, hook in (hooks or {}).items():
        builder.register(name, hook)
    return builder


async def _company(session, *, businesses=("ai_media",)):
    company = await unique_company(session, "snap")
    units = []
    for key in businesses:
        units.append(
            await add_business_unit(
                session,
                company_id=company.id,
                key=key,
                name=key.replace("_", " ").title(),
                actor=ACTOR,
                state=BusinessUnitState.ACTIVE,
                kill_criteria={"auto_pause_if": {"metric": "cost_usd", "op": ">", "value": 9}},
            )
        )
    return company, units


async def _project(session, company, unit, name="p", *, state=ProjectState.ACTIVE):
    project = Project(
        company_id=company.id,
        business_unit_id=unit.id if unit else None,
        name=name,
        state=state.value,
        kill_criteria={"max_cost_usd": 5},
    )
    session.add(project)
    await session.flush()
    return project


# --- the shape ---------------------------------------------------------------------------------


async def test_the_portfolio_is_the_spine(db_session):
    company, (media,) = await _company(db_session)
    project = await _project(db_session, company, media, "Launch bilingual news MVP")
    platform = await _project(db_session, company, None, "Cost attribution")
    await add_product(
        db_session,
        company_id=company.id,
        key="daily_english_world",
        name="Daily English World",
        business_unit_id=media.id,
        actor=ACTOR,
        state=ProductState.LIVE,
    )
    await db_session.flush()

    snapshot = await _builder().build(db_session, company.id, now=NOW)

    assert snapshot.company == company.name
    (line,) = snapshot.portfolio
    assert (line.key, line.state) == ("ai_media", "ACTIVE")
    assert [p.name for p in line.projects] == [project.name]
    assert [p.key for p in line.products] == ["daily_english_world"]
    assert line.kill_criteria["auto_pause_if"]["metric"] == "cost_usd"
    # work that belongs to no business sits beside the portfolio, not inside it
    assert [p.name for p in snapshot.company_work] == [platform.name]


async def test_capital_is_what_is_left_and_what_today_took(db_session):
    company, (media,) = await _company(db_session)
    ledger = Ledger(clock=lambda: NOW)
    await ledger.record(
        db_session, company_id=company.id, kind=TransactionKind.CAPITAL_IN,
        category="ops", amount=Decimal("1000"), idempotency_key="in",
        occurred_at=START - timedelta(days=3),
    )  # fmt: skip
    await ledger.record(
        db_session, company_id=company.id, kind=TransactionKind.EXPENSE, category="ads",
        amount=Decimal("12"), idempotency_key="spend", occurred_at=NOW - timedelta(hours=1),
    )  # fmt: skip
    db_session.add(Budget(company_id=company.id, period="day", amount=Decimal("10")))
    await db_session.flush()

    snapshot = await _builder().build(db_session, company.id, now=NOW)

    assert snapshot.capital.balance == Decimal("988.000000")
    assert snapshot.capital.daily_cap == Decimal("10")
    assert snapshot.capital.daily_spent == Decimal("12.000000")


async def test_a_goal_carries_what_measured_it(db_session):
    company, (media,) = await _company(db_session)
    await _project(db_session, company, media)
    db_session.add(
        CompanyGoal(
            company_id=company.id,
            level="cycle",
            title="Publish 3 bilingual articles",
            metric="published_articles",
            target=Decimal(3),
            current=Decimal(1),
        )  # fmt: skip
    )
    await db_session.flush()
    reporting = Reporting(clock=lambda: NOW)

    async def counted(session, window, core):
        return {"published_articles": 2}

    reporting.register("newsroom", counted)
    for seq in (1, 2):
        cycle = Cycle(
            company_id=company.id, seq=seq, stage="DONE",
            started_at=START - timedelta(days=3 - seq), ended_at=START - timedelta(days=2 - seq),
        )  # fmt: skip
        db_session.add(cycle)
        await db_session.flush()
        await reporting.measure_cycle(db_session, cycle)

    builder = SnapshotBuilder(reporting, Ledger(clock=lambda: NOW))
    snapshot = await builder.build(db_session, company.id, now=NOW)

    (goal,) = snapshot.goals
    assert goal.title == "Publish 3 bilingual articles"
    # the goal names a bare metric; the measurement carries the domain's name. It still matches.
    assert goal.trend_7d == [2.0, 2.0]


async def test_a_goal_nobody_measures_says_so(db_session):
    company, (media,) = await _company(db_session)
    db_session.add(
        CompanyGoal(
            company_id=company.id,
            level="cycle",
            title="Be loved",
            metric="affection",
            target=Decimal(1),
        )  # fmt: skip
    )
    await db_session.flush()

    snapshot = await _builder().build(db_session, company.id, now=NOW)

    assert snapshot.goals[0].trend_7d is None


async def test_the_last_finished_cycle_is_reported_with_what_broke(db_session):
    company, (media,) = await _company(db_session)
    project = await _project(db_session, company, media)
    cycle = Cycle(
        company_id=company.id, seq=1, stage="DONE", started_at=START,
        ended_at=START + timedelta(hours=6),
    )  # fmt: skip
    db_session.add(cycle)
    await db_session.flush()
    from autora.db.models import Task

    db_session.add_all([
        Task(company_id=company.id, project_id=project.id, cycle_id=cycle.id, name="review",
             display_name="Review: microgrid", required_role="editor", state="FAILED"),
        Task(company_id=company.id, project_id=project.id, cycle_id=cycle.id, name="draft",
             display_name="Draft: microgrid", required_role="writer", state="SUCCEEDED"),
    ])  # fmt: skip
    await db_session.flush()

    snapshot = await _builder().build(db_session, company.id, now=NOW)

    assert snapshot.last_cycle.seq == 1
    assert snapshot.last_cycle.failed_tasks == ["Review: microgrid"]
    assert snapshot.period.cycle_id == cycle.id


async def test_a_project_shows_what_is_left_of_its_budget(db_session):
    company, (media,) = await _company(db_session)
    project = await _project(db_session, company, media)
    db_session.add(
        Budget(company_id=company.id, project_id=project.id, period="cycle", amount=Decimal("5"))
    )
    await db_session.flush()
    await Ledger(clock=lambda: NOW).record(
        db_session, company_id=company.id, kind=TransactionKind.EXPENSE, category="ads",
        amount=Decimal("2"), idempotency_key="k", project_id=project.id,
    )  # fmt: skip

    snapshot = await _builder().build(db_session, company.id, now=NOW)

    (line,) = snapshot.portfolio[0].projects
    assert line.budget_remaining == Decimal("3.000000")


async def test_what_a_person_wrote_is_carried_never_invented(db_session):
    company, (media,) = await _company(db_session)
    await upsert_policy(
        db_session, company.id, HUMAN_NOTES_POLICY, "Slow down; quality over volume.",
        updated_by=ACTOR.as_json(),
    )  # fmt: skip
    await upsert_policy(
        db_session, company.id, STRATEGY_POLICY, "Own bilingual local news.",
        updated_by=ACTOR.as_json(),
    )  # fmt: skip
    await db_session.flush()

    snapshot = await _builder().build(db_session, company.id, now=NOW)

    assert snapshot.human_notes == "Slow down; quality over volume."
    assert snapshot.strategy_summary == "Own bilingual local news."


async def test_domains_put_things_in_front_of_the_decision(db_session):
    company, (media,) = await _company(db_session)

    async def candidates(session, company_id):
        return {"candidates": [{"title": "A microgrid for Lumen City", "score": 0.9}]}

    snapshot = await _builder({"newsroom": candidates}).build(db_session, company.id, now=NOW)

    assert snapshot.domains["newsroom"]["candidates"][0]["score"] == 0.9


async def test_a_broken_domain_hook_costs_only_its_own_section(db_session):
    company, (media,) = await _company(db_session)

    async def explodes(session, company_id):
        raise RuntimeError("the desk is on fire")

    snapshot = await _builder({"newsroom": explodes}).build(db_session, company.id, now=NOW)

    assert snapshot.domains == {}
    assert snapshot.portfolio  # the rest of the document survived


# --- the size limit ----------------------------------------------------------------------------


async def test_a_small_company_fits_and_says_nothing_was_left_out(db_session):
    company, (media,) = await _company(db_session)
    await _project(db_session, company, media)

    snapshot = await _builder().build(db_session, company.id, now=NOW)

    assert snapshot.trimmed == []
    assert 0 < snapshot.tokens < DEFAULT_TOKEN_BUDGET


async def test_when_it_does_not_fit_it_says_what_it_dropped(db_session):
    """Silently pushing the interesting parts out of the context window is the worst way to
    lose them, so the document reports its own incompleteness."""
    company, (media,) = await _company(db_session)
    for n in range(12):
        await _project(db_session, company, media, f"project {n}")

    async def candidates(session, company_id):
        return {"candidates": [{"title": f"story {n}" * 20} for n in range(30)]}

    builder = _builder({"newsroom": candidates})
    whole = await builder.build(db_session, company.id, now=NOW, token_budget=1_000_000)
    snapshot = await builder.build(db_session, company.id, now=NOW, token_budget=400)

    assert snapshot.trimmed[0] == "domain extras"
    assert snapshot.domains == {}
    assert snapshot.tokens <= 400 < whole.tokens
    assert whole.trimmed == []


async def test_there_is_a_floor_and_a_budget_below_it_is_simply_too_small(db_session):
    """Trimming stops when everything trimmable is gone. A budget under that floor does not
    make the document smaller — it makes the budget wrong, and the trims say so."""
    company, (media,) = await _company(db_session)
    for n in range(10):
        await _project(db_session, company, media, f"project {n}")

    floor = await _builder().build(db_session, company.id, now=NOW, token_budget=1)
    also_floor = await _builder().build(db_session, company.id, now=NOW, token_budget=10)

    assert floor.tokens == also_floor.tokens > 1
    assert floor.trimmed == also_floor.trimmed


async def test_what_a_decision_cannot_lose_is_never_dropped(db_session):
    company, units = await _company(db_session, businesses=("ai_media", "ai_edu", "ai_saas"))
    for unit in units:
        for n in range(6):
            await _project(db_session, company, unit, f"{unit.key} project {n}")

    snapshot = await _builder().build(db_session, company.id, now=NOW, token_budget=1)

    assert snapshot.trimmed  # everything trimmable went
    assert snapshot.period.date == NOW
    assert snapshot.capital.balance is not None
    assert sorted(line.key for line in snapshot.portfolio) == ["ai_edu", "ai_media", "ai_saas"]
    assert all(line.state == "ACTIVE" for line in snapshot.portfolio)


async def test_trimming_follows_a_fixed_order(db_session):
    company, (media,) = await _company(db_session)
    for n in range(10):
        await _project(db_session, company, media, f"project {n}")

    async def candidates(session, company_id):
        return {"candidates": ["x" * 400]}

    tight = await _builder({"newsroom": candidates}).build(
        db_session, company.id, now=NOW, token_budget=120
    )

    assert tight.trimmed[0] == "domain extras"
    assert "the projects of every business" in tight.trimmed
    assert tight.trimmed.index("domain extras") < tight.trimmed.index(
        "the projects of every business"
    )


async def test_a_company_may_set_its_own_budget(db_session):
    company, (media,) = await _company(db_session)
    for n in range(8):
        await _project(db_session, company, media, f"project {n}")
    await upsert_policy(db_session, company.id, TOKENS_POLICY, 400, updated_by=ACTOR.as_json())
    await db_session.flush()
    whole = await _builder().build(db_session, company.id, now=NOW, token_budget=1_000_000)

    snapshot = await _builder().build(db_session, company.id, now=NOW)

    assert whole.tokens > 400 and whole.trimmed == []
    assert snapshot.tokens <= 400 and snapshot.trimmed


# --- edges -------------------------------------------------------------------------------------


async def test_a_company_with_nothing_yet_still_produces_a_document(db_session):
    company = await unique_company(db_session, "snap-bare")

    snapshot = await _builder().build(db_session, company.id, now=NOW)

    assert snapshot.portfolio == [] and snapshot.goals == []
    assert snapshot.capital.balance == 0
    assert snapshot.period.cycle_id is None
    assert snapshot.last_cycle.seq is None


async def test_an_unknown_company_has_no_snapshot(db_session):
    with pytest.raises(ValueError, match="no company"):
        await _builder().build(db_session, uuid.uuid4(), now=NOW)


async def test_killed_projects_are_not_in_front_of_the_decision(db_session):
    company, (media,) = await _company(db_session)
    await _project(db_session, company, media, "live one")
    await _project(db_session, company, media, "dead one", state=ProjectState.KILLED)

    snapshot = await _builder().build(db_session, company.id, now=NOW)

    assert [p.name for p in snapshot.portfolio[0].projects] == ["live one"]
