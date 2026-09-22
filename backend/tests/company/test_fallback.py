"""T-607: what happens on the days the CEO does not answer, and the record of every day.

The hole this closes: a failed planning run used to be silence. The cycle went on with no plan,
nothing said so, and the next snapshot could not tell a day nobody planned from a day planned
to do nothing.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from autora.app import build_runtime
from autora.company.agents.roster import hire_agent
from autora.company.cycle import CycleRunner, work_is_finished
from autora.company.executive import FALLBACK_POLICY, PLAN_TEMPLATE, REVIEW_TEMPLATE, Executive
from autora.company.ledger import Ledger
from autora.company.organization import add_business_unit, bootstrap_executive
from autora.company.reporting import Reporting
from autora.company.snapshot import SnapshotBuilder
from autora.company.summary import compose, for_cycle, write_daily_summary
from autora.db.models import (
    BusinessUnitState,
    Cycle,
    CycleStage,
    Document,
    DocumentKind,
    EventRecord,
    ModelCall,
    Project,
    ProjectState,
    Task,
    WorkflowRun,
)
from autora.db.repositories.companies import upsert_policy
from autora.runtime.actor import Actor
from tests.conftest import unique_company

HUMAN = Actor.human("founder")
START = datetime(2026, 9, 21, 6, 0, tzinfo=UTC)


async def _company(db_session, *, with_ceo: bool = True):
    company = await unique_company(db_session, "fallback")
    _, ceo_role = await bootstrap_executive(db_session, company.id, actor=HUMAN)
    unit = await add_business_unit(
        db_session, company_id=company.id, key="ai_media", name="AI Media",
        actor=HUMAN, state=BusinessUnitState.ACTIVE,
    )  # fmt: skip
    project = Project(
        company_id=company.id, business_unit_id=unit.id, name="Lumen Daily",
        state=ProjectState.ACTIVE.value, kill_criteria={"note": "none"},
    )  # fmt: skip
    db_session.add(project)
    await db_session.flush()
    if with_ceo:
        await hire_agent(
            db_session, company_id=company.id, role="ceo", display_name="Cyra",
            actor=HUMAN, position=ceo_role,
        )  # fmt: skip
    return company, unit, project


def _runner() -> tuple[CycleRunner, Executive]:
    runtime = build_runtime()
    cycles = CycleRunner()
    cycles.finishes_when(CycleStage.EXECUTING, work_is_finished)
    executive = Executive(runtime.workflows)
    executive.install(cycles)
    return cycles, executive


async def _plan_task(db_session, cycle, template=PLAN_TEMPLATE) -> Task:
    run = await db_session.scalar(
        select(WorkflowRun).where(
            WorkflowRun.cycle_id == cycle.id, WorkflowRun.template_name == template
        )
    )
    return await db_session.scalar(select(Task).where(Task.workflow_run_id == run.id))


async def _answer(db_session, cycle, state, output=None, template=PLAN_TEMPLATE) -> None:
    """The CEO's task ends, and so does its workflow — EXECUTING waits for the run, not the
    task, so a test that finishes only the task leaves the cycle stuck exactly as production
    would."""
    task = await _plan_task(db_session, cycle, template)
    task.state, task.output = state, output
    run = await db_session.get(WorkflowRun, task.workflow_run_id)
    run.state = "SUCCEEDED" if state == "SUCCEEDED" else "FAILED"
    await db_session.flush()


# --- the plan is kept ---------------------------------------------------------------------------


async def test_the_ceo_s_plan_is_recorded_on_the_cycle(db_session):
    """The day's decision has to outlive the run that made it."""
    company, unit, project = await _company(db_session)
    cycles, _ = _runner()
    cycle = await cycles.start(db_session, company.id)
    await _answer(
        db_session, cycle, "SUCCEEDED",
        {"goals": [{"metric": "published", "target": 3}], "rationale": "because"},
    )  # fmt: skip

    await cycles.tick(db_session, company.id)

    assert cycle.plan["by"] == "ceo"
    assert cycle.plan["goals"][0]["metric"] == "published"


async def test_a_failed_plan_falls_back_to_what_the_company_was_doing(db_session):
    """A day nobody planned is a day to hold position, not to improvise."""
    company, unit, project = await _company(db_session)
    cycles, _ = _runner()
    yesterday = Cycle(
        company_id=company.id,
        seq=1,
        stage="DONE",
        started_at=START - timedelta(days=1),
        ended_at=START - timedelta(hours=12),
        plan={"by": "ceo", "allocations": [{"amount": "5", "business_unit_id": str(unit.id)}]},
    )
    db_session.add(yesterday)
    await db_session.flush()
    cycle = await cycles.start(db_session, company.id)
    await _answer(db_session, cycle, "FAILED")

    await cycles.tick(db_session, company.id)

    assert cycle.plan["by"] == "fallback"
    assert cycle.plan["source"] == "last_cycle"
    assert cycle.plan["allocations"] == [{"amount": "5", "business_unit_id": str(unit.id)}]
    assert cycle.plan["goals"] == []  # holding position sets no new targets
    event = await db_session.scalar(
        select(EventRecord).where(
            EventRecord.company_id == company.id,
            EventRecord.event_type == "CYCLE_PLAN_FALLBACK",
        )
    )
    assert "failed" in event.payload["reason"]


async def test_a_company_may_write_its_own_fallback(db_session):
    company, unit, project = await _company(db_session)
    await upsert_policy(
        db_session,
        company.id,
        FALLBACK_POLICY,
        {"goals": [], "allocations": [{"amount": "1", "business_unit_id": str(unit.id)}],
         "rationale": "a quiet day costs a dollar"},
        updated_by=HUMAN.as_json(),
    )  # fmt: skip
    await db_session.flush()
    cycles, _ = _runner()
    cycle = await cycles.start(db_session, company.id)
    await _answer(db_session, cycle, "FAILED")

    await cycles.tick(db_session, company.id)

    assert cycle.plan["source"] == "policy"
    assert cycle.plan["allocations"][0]["amount"] == "1"


async def test_the_first_cycle_with_no_plan_has_nothing_to_hold(db_session):
    company, unit, project = await _company(db_session)
    cycles, _ = _runner()
    cycle = await cycles.start(db_session, company.id)
    await _answer(db_session, cycle, "FAILED")

    await cycles.tick(db_session, company.id)

    assert cycle.plan["source"] == "nothing"
    assert cycle.plan["allocations"] == []


async def test_a_company_with_no_ceo_records_that_nobody_was_asked(db_session):
    company, unit, project = await _company(db_session, with_ceo=False)
    cycles, _ = _runner()
    cycle = await cycles.start(db_session, company.id)

    await cycles.tick(db_session, company.id)

    assert cycle.plan["by"] == "fallback"
    event = await db_session.scalar(
        select(EventRecord).where(
            EventRecord.company_id == company.id,
            EventRecord.event_type == "CYCLE_PLAN_FALLBACK",
        )
    )
    assert event.payload["reason"] == "no CEO was asked"


# --- the review, and its absence --------------------------------------------------------------


async def test_a_missing_review_is_recorded_as_missing(db_session):
    """The cycle still reaches DONE; the next snapshot is told the review is absent."""
    company, unit, project = await _company(db_session)
    cycles, _ = _runner()
    cycle = await cycles.start(db_session, company.id)
    await _answer(db_session, cycle, "SUCCEEDED", {"goals": [], "rationale": "fine"})
    await cycles.tick(db_session, company.id)  # into EXECUTING, then REVIEWING
    await _answer(db_session, cycle, "FAILED", template=REVIEW_TEMPLATE)

    await cycles.tick(db_session, company.id)

    assert cycle.stage == CycleStage.DONE  # the day still ends
    assert cycle.review["missing"] is True
    assert "failed" in cycle.review["reason"]

    snapshot = await SnapshotBuilder(Reporting()).build(db_session, company.id)
    assert snapshot.last_cycle.review_missing
    assert snapshot.last_cycle.planned_by == "ceo"


async def test_a_review_that_happened_is_carried_into_the_next_snapshot(db_session):
    company, unit, project = await _company(db_session)
    cycles, _ = _runner()
    cycle = await cycles.start(db_session, company.id)
    await _answer(db_session, cycle, "SUCCEEDED", {"goals": [], "rationale": "fine"})
    await cycles.tick(db_session, company.id)
    await _answer(
        db_session, cycle, "SUCCEEDED",
        {"projects": [], "summary": "A quiet day; nothing to change."},
        template=REVIEW_TEMPLATE,
    )  # fmt: skip

    await cycles.tick(db_session, company.id)

    assert cycle.review["by"] == "ceo"
    snapshot = await SnapshotBuilder(Reporting()).build(db_session, company.id)
    assert snapshot.last_cycle.review == "A quiet day; nothing to change."
    assert snapshot.last_cycle.review_missing is None


# --- the daily summary ---------------------------------------------------------------------------


async def _finished_cycle(db_session, company, **kw) -> Cycle:
    cycle = Cycle(
        company_id=company.id,
        seq=kw.pop("seq", 1),
        stage="DONE",
        started_at=START,
        ended_at=START + timedelta(hours=14),
        **kw,
    )
    db_session.add(cycle)
    await db_session.flush()
    return cycle


async def test_the_summary_says_what_the_day_did(db_session):
    company, unit, project = await _company(db_session)
    cycle = await _finished_cycle(
        db_session,
        company,
        plan={"by": "ceo", "goals": [{"metric": "published", "target": 3}],
              "allocations": [{"amount": "5"}]},
        review={"by": "ceo", "projects": [{"id": "x"}], "summary": "Steady."},
    )  # fmt: skip
    run = WorkflowRun(
        company_id=company.id, project_id=project.id, cycle_id=cycle.id,
        template_name="newsroom.story_to_article_v2", state="SUCCEEDED",
    )  # fmt: skip
    db_session.add(run)
    db_session.add(
        ModelCall(
            company_id=company.id,
            project_id=project.id,
            cycle_id=cycle.id,
            role="writer",
            capability="drafting",
            alias="frontier",
            provider="nvidia",
            model_id="m",
            status="ok",
            cost_usd=Decimal("0.40"),
            created_at=START + timedelta(hours=1),
        )  # fmt: skip
    )
    await db_session.flush()
    await Reporting(clock=lambda: START + timedelta(hours=14)).measure_cycle(db_session, cycle)

    summary = await compose(db_session, cycle)

    assert summary.title == "Cycle 1"
    assert "aimed at published 3" in summary.body
    assert "allocated NT$5" in summary.body
    assert "1 workflow(s) ran" in summary.body
    assert "cost NT$12.8" in summary.body  # the meter's $0.40, in TWD
    assert "Steady." in summary.body


async def test_the_summary_of_a_day_nobody_planned_says_so(db_session):
    company, unit, project = await _company(db_session)
    cycle = await _finished_cycle(
        db_session,
        company,
        plan={"by": "fallback", "source": "last_cycle", "allocations": []},
        review={"by": None, "missing": True, "reason": "the CEO's run failed after 3 attempt(s)"},
    )

    summary = await compose(db_session, cycle)

    assert "No plan was made" in summary.body
    assert "previous cycle's allocations" in summary.body
    assert "The review is missing" in summary.body
    assert "failed after 3" in summary.body
    assert "No work was started." in summary.body


async def test_the_summary_reports_what_the_rules_did(db_session):
    company, unit, project = await _company(db_session)
    cycle = await _finished_cycle(db_session, company, plan={"by": "ceo", "goals": []})
    db_session.add(
        EventRecord(
            id=uuid.uuid4(),
            event_type="PROJECT_PAUSED",
            schema_version=1,
            company_id=company.id,
            occurred_at=START,
            aggregate_type="project",
            aggregate_id=project.id,
            cycle_id=cycle.id,
            actor={"kind": "system", "id": "governance"},
            payload={"name": "Lumen Daily", "trigger": "kill_criteria", "reason": "too costly"},
        )  # fmt: skip
    )
    await db_session.flush()

    summary = await compose(db_session, cycle)

    assert "Rules fired: Lumen Daily paused" in summary.body


async def test_the_summary_is_written_once_and_rewritten_in_place(db_session):
    company, unit, project = await _company(db_session)
    cycle = await _finished_cycle(db_session, company, plan={"by": "ceo", "goals": []})

    first = await write_daily_summary(db_session, cycle)
    cycle.review = {"by": "ceo", "summary": "Added later."}
    await db_session.flush()
    again = await write_daily_summary(db_session, cycle)

    assert first.id == again.id
    assert "Added later." in again.body
    documents = (
        await db_session.scalars(select(Document).where(Document.company_id == company.id))
    ).all()
    assert len(documents) == 1
    assert documents[0].kind == DocumentKind.DAILY_SUMMARY.value


async def test_a_finished_cycle_leaves_its_summary_behind(db_session):
    company, unit, project = await _company(db_session)
    cycles, _ = _runner()
    from autora.company import summary as daily

    cycles.when_entering(CycleStage.DONE, daily.stage_hook())
    cycle = await cycles.start(db_session, company.id)
    await _answer(db_session, cycle, "SUCCEEDED", {"goals": [], "rationale": "fine"})
    await cycles.tick(db_session, company.id)
    await _answer(
        db_session, cycle, "SUCCEEDED", {"projects": [], "summary": "Nothing to change."},
        template=REVIEW_TEMPLATE,
    )  # fmt: skip

    await cycles.tick(db_session, company.id)

    document = await for_cycle(db_session, company.id, cycle.id)
    assert document is not None
    assert document.title == f"Cycle {cycle.seq}"
    assert "Nothing to change." in document.body


async def test_the_summary_costs_nothing(db_session):
    """Written by arithmetic: a model-written summary of a day the model could not plan would
    be the least trustworthy sentence in the company."""
    company, unit, project = await _company(db_session)
    cycle = await _finished_cycle(db_session, company, plan={"by": "fallback", "source": "nothing"})

    await write_daily_summary(db_session, cycle)

    assert (
        await db_session.scalar(select(ModelCall.id).where(ModelCall.company_id == company.id))
        is None
    )
    assert await Ledger().metered(db_session, cycle_id=cycle.id) == 0
