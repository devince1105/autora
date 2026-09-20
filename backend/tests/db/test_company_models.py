"""T-103: company, agent, project, budget and ledger models against the migrated schema."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from autora.db.models import (
    Agent,
    Budget,
    Company,
    CompanyGoal,
    Project,
    ProjectState,
    Transaction,
)
from autora.db.repositories import agents, companies, projects, transactions

HUMAN = {"kind": "human", "id": "operator"}


async def _company(session, slug="ai-newsroom") -> Company:
    return await companies.add_company(
        session, Company(slug=slug, name="AI Bilingual Newsroom", type="newsroom")
    )


async def _expect_integrity_error(session, coro, constraint: str):
    with pytest.raises(IntegrityError) as exc:
        await coro
    assert constraint in str(exc.value)
    await session.rollback()


# --- companies ---------------------------------------------------------------------------


async def test_company_defaults_and_lookup(db_session):
    company = await _company(db_session)
    assert company.id.version == 7
    await db_session.refresh(company)
    assert company.status == "active"
    assert company.strategy_doc == {}
    assert company.created_at.tzinfo is not None

    assert (await companies.get_company_by_slug(db_session, "ai-newsroom")).id == company.id


@pytest.mark.parametrize(
    ("field", "value", "constraint"),
    [
        ("slug", "Bad Slug", "ck_companies_slug_format"),
        ("type", "bank", "ck_companies_type_valid"),
        ("status", "deleted", "ck_companies_status_valid"),
    ],
)
async def test_company_checks(db_session, field, value, constraint):
    data = {"slug": "ok-slug", "name": "x", "type": "newsroom", field: value}
    await _expect_integrity_error(
        db_session, companies.add_company(db_session, Company(**data)), constraint
    )


async def test_company_slug_unique(db_session):
    await _company(db_session, "dup")
    await _expect_integrity_error(db_session, _company(db_session, "dup"), "uq_companies_slug")


# --- goals & policies --------------------------------------------------------------------


async def test_goals_hierarchy_and_filters(db_session):
    company = await _company(db_session)
    annual = await companies.add_goal(
        db_session,
        CompanyGoal(
            company_id=company.id,
            level="annual",
            title="Reach 100k monthly readers",
            metric="monthly_readers",
            target=Decimal(100_000),
        ),
    )
    await companies.add_goal(
        db_session,
        CompanyGoal(
            company_id=company.id,
            parent_goal_id=annual.id,
            level="cycle",
            title="Publish 3 high-quality bilingual articles",
            metric="published_articles",
            target=Decimal(3),
        ),
    )
    cycle_goals = await companies.list_goals(db_session, company.id, level="cycle")
    assert [g.title for g in cycle_goals] == ["Publish 3 high-quality bilingual articles"]
    assert cycle_goals[0].parent_goal_id == annual.id


async def test_policies_upsert_and_read(db_session):
    company = await _company(db_session)
    await companies.upsert_policy(
        db_session, company.id, "newsroom.primary_lang", "zh-TW", updated_by=HUMAN
    )
    await companies.upsert_policy(
        db_session,
        company.id,
        "newsroom.auto_approve_if_fact_check_passed",
        False,
        updated_by=HUMAN,
    )
    await companies.upsert_policy(
        db_session, company.id, "newsroom.langs", ["zh-TW", "en"], updated_by=HUMAN
    )
    await companies.upsert_policy(
        db_session, company.id, "newsroom.langs", ["zh-TW", "en", "ja"], updated_by=HUMAN
    )

    policies = await companies.get_policies(db_session, company.id)
    assert policies == {
        "newsroom.primary_lang": "zh-TW",
        "newsroom.auto_approve_if_fact_check_passed": False,
        "newsroom.langs": ["zh-TW", "en", "ja"],
    }
    assert await companies.get_policy(db_session, company.id, "missing.key", 42) == 42


async def test_policy_key_format(db_session):
    company = await _company(db_session)
    await _expect_integrity_error(
        db_session,
        companies.upsert_policy(db_session, company.id, "Bad Key", 1, updated_by=HUMAN),
        "ck_company_policies_key_format",
    )


# --- agents ------------------------------------------------------------------------------


async def test_agents_defaults_and_listing(db_session):
    company = await _company(db_session)
    for role, name in [("ceo", "CEO"), ("researcher", "Researcher"), ("writer", "Writer")]:
        await agents.add_agent(
            db_session,
            Agent(
                company_id=company.id,
                role=role,
                display_name=name,
                model_policy={"reasoning": "frontier"},
            ),
        )
    retired = await agents.add_agent(
        db_session,
        Agent(company_id=company.id, role="writer", display_name="Old Writer", status="retired"),
    )

    listed = await agents.list_agents(db_session, company.id)
    assert [a.display_name for a in listed] == ["CEO", "Researcher", "Writer"]
    writers = await agents.list_agents(db_session, company.id, role="writer", include_retired=True)
    assert {a.id for a in writers} >= {retired.id}

    await db_session.refresh(listed[0])
    assert listed[0].capabilities == []
    assert listed[0].avatar_key == "default"
    assert listed[0].model_policy == {"reasoning": "frontier"}


async def test_agent_display_name_unique_per_company(db_session):
    a = await _company(db_session, "company-a")
    b = await _company(db_session, "company-b")
    await agents.add_agent(db_session, Agent(company_id=a.id, role="ceo", display_name="CEO"))
    await agents.add_agent(db_session, Agent(company_id=b.id, role="ceo", display_name="CEO"))
    await _expect_integrity_error(
        db_session,
        agents.add_agent(db_session, Agent(company_id=a.id, role="ceo", display_name="CEO")),
        "uq_agents_company_id_display_name",
    )


async def test_agent_role_format(db_session):
    company = await _company(db_session)
    await _expect_integrity_error(
        db_session,
        agents.add_agent(
            db_session, Agent(company_id=company.id, role="Chief Editor", display_name="x")
        ),
        "ck_agents_role_format",
    )


# --- projects & budgets ------------------------------------------------------------------


async def test_project_requires_kill_criteria_once_approved(db_session):
    company = await _company(db_session)
    project = await projects.add_project(
        db_session, Project(company_id=company.id, name="Daily AI news")
    )
    await db_session.refresh(project)
    assert project.state == ProjectState.PROPOSED

    project.state = ProjectState.APPROVED
    await _expect_integrity_error(
        db_session, db_session.flush(), "ck_projects_kill_criteria_required_once_approved"
    )


async def test_project_with_kill_criteria_can_be_active(db_session):
    company = await _company(db_session)
    project = await projects.add_project(
        db_session,
        Project(
            company_id=company.id,
            name="Daily AI news",
            state=ProjectState.ACTIVE,
            kill_criteria={"evaluate_after_cycles": 7},
        ),
    )
    assert await projects.list_projects(db_session, company.id, state="ACTIVE") == [project]


async def test_budget_unique_per_scope_including_company_wide(db_session):
    company = await _company(db_session)
    await projects.add_budget(
        db_session, Budget(company_id=company.id, period="day", amount=Decimal("10"))
    )
    # NULL business unit and NULL project mean company-wide; a second one must collide.
    await _expect_integrity_error(
        db_session,
        projects.add_budget(
            db_session, Budget(company_id=company.id, period="day", amount=Decimal("20"))
        ),
        "uq_budgets_company_id_business_unit_id_project_id_period",
    )


# --- ledger ------------------------------------------------------------------------------


def _tx(company_id, key, kind="expense", amount="1.25", category="model_cost", **kw):
    return Transaction(
        company_id=company_id,
        kind=kind,
        category=category,
        amount=Decimal(amount),
        occurred_at=datetime.now(UTC),
        source="system",
        idempotency_key=key,
        **kw,
    )


async def test_transaction_record_is_idempotent(db_session):
    company = await _company(db_session)
    first, created = await transactions.record(db_session, _tx(company.id, "cycle:1:model_cost"))
    assert created
    again, created_again = await transactions.record(
        db_session, _tx(company.id, "cycle:1:model_cost", amount="999")
    )
    assert not created_again
    assert again.id == first.id
    assert again.amount == Decimal("1.250000")


async def test_balance_signs_by_kind(db_session):
    company = await _company(db_session)
    for key, kind, amount in [
        ("c1", "capital_in", "100"),
        ("e1", "expense", "12.5"),
        ("r1", "revenue", "3.25"),
        ("t1", "transfer", "50"),
        ("o1", "capital_out", "10"),
    ]:
        category = "funding" if kind.startswith("capital") else "misc"
        await transactions.record(db_session, _tx(company.id, key, kind, amount, category))
    assert await transactions.balance(db_session, company.id) == Decimal("80.75")


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"amount": "0"}, "ck_transactions_amount_positive"),
        ({"kind": "refund"}, "ck_transactions_kind_valid"),
        ({"category": "Model Cost"}, "ck_transactions_category_format"),
    ],
)
async def test_transaction_checks(db_session, overrides, constraint):
    company = await _company(db_session)
    await _expect_integrity_error(
        db_session, transactions.record(db_session, _tx(company.id, "k", **overrides)), constraint
    )


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE transactions SET amount = 0.01 WHERE id = :id",
        "DELETE FROM transactions WHERE id = :id",
    ],
)
async def test_transactions_are_append_only(db_session, statement):
    company = await _company(db_session)
    row, _ = await transactions.record(db_session, _tx(company.id, "immutable"))

    with pytest.raises(DBAPIError, match="append-only"):
        await db_session.execute(text(statement), {"id": row.id})
    await db_session.rollback()
