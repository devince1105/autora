"""T-201: workflow runs, tasks, agent runs, agent steps."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from autora.db.models import (
    Agent,
    AgentRun,
    AgentStep,
    Company,
    Project,
    Task,
    TaskState,
    WorkflowRun,
)


@pytest.fixture
async def world(db_session):
    company = Company(slug=f"t201-{uuid.uuid4().hex[:8]}", name="Newsroom", type="newsroom")
    db_session.add(company)
    await db_session.flush()
    project = Project(company_id=company.id, name="Daily AI news")
    agent = Agent(company_id=company.id, role="researcher", display_name="Researcher")
    db_session.add_all([project, agent])
    await db_session.flush()
    workflow = WorkflowRun(
        company_id=company.id,
        project_id=project.id,
        template_name="newsroom.story_to_article_v2",
        params={"story_seed": "EU AI Act"},
    )
    db_session.add(workflow)
    await db_session.flush()
    return {"company": company, "project": project, "agent": agent, "workflow": workflow}


def _task(world, **kw) -> Task:
    values = {
        "company_id": world["company"].id,
        "project_id": world["project"].id,
        "workflow_run_id": world["workflow"].id,
        "name": "research",
        "display_name": "Find today's AI stories",
        "required_role": "researcher",
    }
    values.update(kw)
    return Task(**values)


async def _flush_expect(session, constraint):
    with pytest.raises(IntegrityError) as exc:
        await session.flush()
    assert constraint in str(exc.value)
    await session.rollback()


# --- workflow runs & tasks ---------------------------------------------------------------


async def test_defaults(db_session, world):
    research = _task(world)
    db_session.add(research)
    await db_session.flush()
    analysis = _task(
        world,
        name="analysis",
        display_name="Extract claims",
        required_role="analyst",
        depends_on=[research.id],
        budget_usd=Decimal("0.40"),
    )
    db_session.add(analysis)
    await db_session.flush()
    await db_session.refresh(research)
    await db_session.refresh(analysis)
    await db_session.refresh(world["workflow"])

    assert world["workflow"].state == "RUNNING"
    assert research.state == TaskState.PENDING
    assert (research.attempt, research.max_attempts, research.priority) == (0, 3, 100)
    assert research.depends_on == [] and research.input == {}
    assert research.progress is None and research.lease_owner is None
    assert analysis.depends_on == [research.id]


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"state": "DONE"}, "ck_tasks_state_valid"),
        ({"required_role": "Research Agent"}, "ck_tasks_required_role_format"),
        ({"name": "Research"}, "ck_tasks_name_format"),
        ({"max_attempts": 0, "attempt": 0}, "ck_tasks_max_attempts_positive"),
        ({"attempt": 4, "max_attempts": 3}, "ck_tasks_attempt_within_max"),
        ({"budget_usd": Decimal("-1")}, "ck_tasks_budget_non_negative"),
    ],
)
async def test_task_checks(db_session, world, overrides, constraint):
    db_session.add(_task(world, **overrides))
    await _flush_expect(db_session, constraint)


@pytest.mark.parametrize(
    ("state", "leased", "ok"),
    [
        ("RUNNING", True, True),
        ("RUNNING", False, False),  # a running task must be leased
        ("RUNNING", "no-token", False),  # a lease without its claim token is incomplete
        ("READY", True, False),  # a lease outside RUNNING is a leaked claim
        ("WAITING_APPROVAL", False, True),  # approval releases the worker
    ],
)
async def test_lease_exactly_when_running(db_session, world, state, leased, ok):
    lease = {}
    if leased:
        lease = {"lease_owner": "worker-1", "lease_until": datetime.now(UTC) + timedelta(minutes=5)}
        if leased != "no-token":
            lease["lease_token"] = uuid.uuid4()
    db_session.add(_task(world, state=state, **lease))
    if ok:
        await db_session.flush()
    else:
        await _flush_expect(db_session, "ck_tasks_lease_iff_running")


async def test_template_name_format(db_session, world):
    db_session.add(
        WorkflowRun(
            company_id=world["company"].id,
            project_id=world["project"].id,
            template_name="Story To Article",
        )
    )
    await _flush_expect(db_session, "ck_workflow_runs_template_name_format")


# --- agent runs --------------------------------------------------------------------------


async def _run(db_session, world, **kw) -> AgentRun:
    task = _task(world)
    db_session.add(task)
    await db_session.flush()
    values = {
        "company_id": world["company"].id,
        "task_id": task.id,
        "agent_id": world["agent"].id,
        "attempt": 1,
    }
    values.update(kw)
    run = AgentRun(**values)
    db_session.add(run)
    await db_session.flush()
    return run


async def test_agent_run_defaults_and_usage(db_session, world):
    run = await _run(db_session, world)
    await db_session.refresh(run)
    assert run.state == "CREATED"
    assert (run.cost_usd, run.tokens_in, run.tokens_out, run.steps_count) == (0, 0, 0, 0)
    assert run.started_at is None and run.finished_at is None

    run.handoff = [{"to_role": "analyst", "task_id": str(uuid.uuid4())}]
    run.state = "COMPLETED"
    run.finished_at = datetime.now(UTC)
    await db_session.flush()
    await db_session.refresh(run)
    assert run.handoff[0]["to_role"] == "analyst"


@pytest.mark.parametrize(
    ("state", "finished", "ok"),
    [
        ("COMPLETED", True, True),
        ("ABORTED", True, True),
        ("FAILED", False, False),  # terminal without finished_at
        ("RUNNING", True, False),  # finished_at on a live run
        ("WAITING_APPROVAL", False, True),
    ],
)
async def test_finished_at_exactly_when_terminal(db_session, world, state, finished, ok):
    kw = {"state": state, "finished_at": datetime.now(UTC) if finished else None}
    if ok:
        await _run(db_session, world, **kw)
    else:
        with pytest.raises(IntegrityError, match="ck_agent_runs_finished_iff_terminal"):
            await _run(db_session, world, **kw)
        await db_session.rollback()


async def test_one_run_per_task_attempt(db_session, world):
    run = await _run(db_session, world)
    db_session.add(
        AgentRun(
            company_id=world["company"].id,
            task_id=run.task_id,
            agent_id=world["agent"].id,
            attempt=1,
        )
    )
    await _flush_expect(db_session, "uq_agent_runs_task_id_attempt")


async def test_agent_run_usage_non_negative(db_session, world):
    with pytest.raises(IntegrityError, match="ck_agent_runs_usage_non_negative"):
        await _run(db_session, world, tokens_in=-1)
    await db_session.rollback()


async def test_project_references_proposing_run(db_session, world):
    run = await _run(db_session, world)
    project = Project(company_id=world["company"].id, name="Proposed", created_by_run_id=run.id)
    db_session.add(project)
    await db_session.flush()

    db_session.add(
        Project(company_id=world["company"].id, name="Ghost", created_by_run_id=uuid.uuid4())
    )
    await _flush_expect(db_session, "fk_projects_created_by_run_id_agent_runs")


# --- agent steps -------------------------------------------------------------------------


async def _steps(db_session, world) -> uuid.UUID:
    run = await _run(db_session, world)
    for seq, kind in enumerate(["think", "act", "observe", "evaluate"]):
        db_session.add(
            AgentStep(
                company_id=world["company"].id,
                run_id=run.id,
                seq=seq,
                kind=kind,
                tool_calls=[{"tool": "web_search", "args_hash": "abc"}] if kind == "act" else None,
                cost_usd=Decimal("0.01"),
            )
        )
    await db_session.flush()
    return run.id


async def test_step_seq_unique_per_run(db_session, world):
    run_id = await _steps(db_session, world)
    db_session.add(AgentStep(company_id=world["company"].id, run_id=run_id, seq=1, kind="act"))
    await _flush_expect(db_session, "uq_agent_steps_run_id_seq")


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE agent_steps SET summary = 'rewritten' WHERE run_id = :id",
        "DELETE FROM agent_steps WHERE run_id = :id",
    ],
)
async def test_steps_are_append_only(db_session, world, statement):
    run_id = await _steps(db_session, world)
    with pytest.raises(DBAPIError, match="append-only"):
        await db_session.execute(text(statement), {"id": run_id})
    await db_session.rollback()


async def test_step_kind_valid(db_session, world):
    run = await _run(db_session, world)
    db_session.add(AgentStep(company_id=world["company"].id, run_id=run.id, seq=0, kind="dance"))
    await _flush_expect(db_session, "ck_agent_steps_kind_valid")
