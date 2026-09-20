"""T-608: the company's days, over the API.

AC-12 is the one that matters here: the target comes from the plan the CEO recorded, and the
progress comes from what was actually counted — never from the plan's own idea of how it went.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from autora.company.organization import bootstrap_executive
from autora.company.summary import write_daily_summary
from autora.db.models import (
    Cycle,
    KpiScope,
    KpiSnapshot,
    ModelCall,
    Project,
    ProjectState,
    Task,
    WorkflowRun,
)
from autora.runtime.actor import Actor
from tests.conftest import unique_company

HUMAN = Actor.human("founder")
START = datetime(2026, 9, 21, 6, 0, tzinfo=UTC)


@pytest.fixture
async def week(committed):
    """A company with three finished days: one planned and met, one planned and missed, one
    nobody planned."""
    async with committed() as session:
        company = await unique_company(session, "cycles-api")
        await bootstrap_executive(session, company.id, actor=HUMAN)
        project = Project(
            company_id=company.id, name="Lumen Daily", state=ProjectState.ACTIVE.value,
            kill_criteria={"note": "none"},
        )  # fmt: skip
        session.add(project)
        await session.flush()

        cycles = []
        for seq, plan, published, review in (
            (
                1,
                {
                    "by": "ceo",
                    "goals": [
                        {
                            "metric": "published_articles",
                            "target": 3,
                            "title": "Publish 3 bilingual articles",
                        }
                    ],
                },
                3,
                {"by": "ceo", "projects": [], "summary": "A good day."},
            ),
            (
                2,
                {"by": "ceo", "goals": [{"metric": "published_articles", "target": 5}]},
                1,
                {"by": None, "missing": True, "reason": "the CEO's run failed after 3 attempt(s)"},
            ),
            (3, {"by": "fallback", "source": "last_cycle", "goals": []}, 0, None),
        ):
            cycle = Cycle(
                company_id=company.id,
                seq=seq,
                stage="DONE",
                started_at=START + timedelta(days=seq),
                ended_at=START + timedelta(days=seq, hours=14),
                plan=plan,
                review=review,
            )  # fmt: skip
            session.add(cycle)
            await session.flush()
            session.add(
                KpiSnapshot(
                    company_id=company.id,
                    cycle_id=cycle.id,
                    scope=KpiScope.COMPANY.value,
                    metrics={
                        "cost_usd": f"{seq}.000000",
                        "model_calls": seq,
                        "newsroom.published_articles": published,
                    },
                    period_start=cycle.started_at,
                    period_end=cycle.ended_at,
                )  # fmt: skip
            )
            run = WorkflowRun(
                company_id=company.id, project_id=project.id, cycle_id=cycle.id,
                template_name="newsroom.story_to_article_v2", state="SUCCEEDED",
            )  # fmt: skip
            session.add(run)
            await session.flush()
            if seq == 2:
                session.add(
                    Task(
                        company_id=company.id,
                        project_id=project.id,
                        cycle_id=cycle.id,
                        workflow_run_id=run.id,
                        name="review",
                        display_name="Review: microgrid",
                        required_role="editor",
                        state="FAILED",
                    )  # fmt: skip
                )
            cycles.append(cycle)
            await write_daily_summary(session, cycle)
        await session.commit()
        ids = [c.id for c in cycles]
    return company, ids


async def test_the_list_shows_the_last_few_days_newest_first(api, week):
    company, _ = week

    response = await api.get(f"/api/companies/{company.id}/cycles")
    assert response.status_code == 200
    days = response.json()

    assert [d["seq"] for d in days] == [3, 2, 1]
    assert all(d["stage"] == "DONE" for d in days)
    assert [d["planned_by"] for d in days] == ["fallback", "ceo", "ceo"]


async def test_a_goal_s_target_comes_from_the_plan_and_its_progress_from_the_count(api, week):
    """AC-12, stated as a test: the plan says 3, the count says 3 — and on the day it was
    missed, the plan still says 5 and the count still says 1."""
    company, _ = week
    days = (await api.get(f"/api/companies/{company.id}/cycles")).json()
    by_seq = {d["seq"]: d for d in days}

    met = by_seq[1]["goals"][0]
    assert (met["metric"], met["target"], met["current"]) == ("published_articles", 3.0, 3.0)
    assert met["title"] == "Publish 3 bilingual articles"

    missed = by_seq[2]["goals"][0]
    assert (missed["target"], missed["current"]) == (5.0, 1.0)


async def test_a_day_nobody_planned_says_so_rather_than_showing_nothing(api, week):
    company, _ = week
    days = (await api.get(f"/api/companies/{company.id}/cycles")).json()
    fallback = next(d for d in days if d["seq"] == 3)

    assert fallback["planned_by"] == "fallback"
    assert fallback["goals"] == []


async def test_a_missing_review_is_visible_in_the_list(api, week):
    company, _ = week
    days = (await api.get(f"/api/companies/{company.id}/cycles")).json()
    by_seq = {d["seq"]: d for d in days}

    assert by_seq[1]["review"] == "A good day."
    assert by_seq[2]["review"] is None
    assert "failed after 3" in by_seq[2]["review_missing"]


async def test_the_list_counts_the_work_and_what_broke(api, week):
    company, _ = week
    days = (await api.get(f"/api/companies/{company.id}/cycles")).json()
    by_seq = {d["seq"]: d for d in days}

    assert by_seq[1]["workflows"] == 1 and by_seq[1]["failed_tasks"] == 0
    assert by_seq[2]["failed_tasks"] == 1
    assert by_seq[1]["cost_usd"] == "1.000000"


async def test_one_day_in_full(api, week):
    company, ids = week

    detail = (await api.get(f"/api/cycles/{ids[0]}")).json()

    assert detail["seq"] == 1
    assert detail["plan"]["by"] == "ceo"
    assert detail["review_detail"]["summary"] == "A good day."
    assert detail["kpis"]["newsroom.published_articles"] == 3
    assert "A good day." in detail["summary"]  # the document written at the end
    assert isinstance(detail["timeline"], list)


async def test_the_timeline_of_a_day_is_its_own_events_in_order(api, committed, week):
    company, ids = week
    async with committed() as session:
        from autora.db.models import EventRecord

        payloads = [
            ("CYCLE_STARTED", {"seq": 1, "stage": "PLANNING"}),
            ("CYCLE_STAGE_CHANGED", {"from_stage": "PLANNING", "to_stage": "EXECUTING"}),
            ("CYCLE_COMPLETED", {"seq": 1}),
        ]
        for n, (kind, payload) in enumerate(payloads):
            session.add(
                EventRecord(
                    id=uuid.uuid4(),
                    event_type=kind,
                    schema_version=1,
                    company_id=company.id,
                    occurred_at=START + timedelta(minutes=n),
                    aggregate_type="cycle",
                    aggregate_id=ids[0],
                    cycle_id=ids[0],
                    actor={"kind": "system", "id": "t"},
                    payload=payload,
                )
            )
        await session.commit()

    detail = (await api.get(f"/api/cycles/{ids[0]}")).json()

    assert [e["event_type"] for e in detail["timeline"]] == [
        "CYCLE_STARTED",
        "CYCLE_STAGE_CHANGED",
        "CYCLE_COMPLETED",
    ]


async def test_an_unknown_cycle_and_an_unknown_company(api):
    assert (await api.get(f"/api/cycles/{uuid.uuid4()}")).status_code == 404
    assert (await api.get(f"/api/companies/{uuid.uuid4()}/cycles")).status_code == 404


async def test_a_company_that_has_not_run_a_day_yet(api, committed):
    async with committed() as session:
        company = await unique_company(session, "cycles-none")
        await session.commit()

    assert (await api.get(f"/api/companies/{company.id}/cycles")).json() == []


async def test_the_realtime_snapshot_carries_the_current_cycle(api, db_session):
    """The office's own view of what day it is (T-601's columns, finally reaching the page)."""
    company = await unique_company(db_session, "cycles-live")
    cycle = Cycle(
        company_id=company.id, seq=4, stage="EXECUTING", started_at=START,
        stage_deadline=START + timedelta(hours=13),
    )  # fmt: skip
    db_session.add(cycle)
    await db_session.flush()
    cycle_id = cycle.id

    snapshot = (await api.get(f"/api/companies/{company.id}/realtime/snapshot")).json()

    assert snapshot["cycle"]["seq"] == 4
    assert snapshot["cycle"]["stage"] == "EXECUTING"
    assert snapshot["cycle"]["id"] == str(cycle_id)


async def test_cost_is_reported_as_measured_not_recomputed(api, committed, week):
    """The list reads Reporting's number rather than adding up model calls itself: one
    measurement, one answer."""
    company, ids = week
    async with committed() as session:
        session.add(
            ModelCall(
                company_id=company.id,
                cycle_id=ids[0],
                role="writer",
                capability="drafting",
                alias="frontier",
                provider="nvidia",
                model_id="m",
                status="ok",
                cost_usd=Decimal("99"),
                created_at=START,
            )  # fmt: skip
        )
        await session.commit()

    days = (await api.get(f"/api/companies/{company.id}/cycles")).json()

    assert next(d for d in days if d["seq"] == 1)["cost_usd"] == "1.000000"
