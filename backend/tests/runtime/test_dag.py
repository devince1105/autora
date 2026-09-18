"""T-203: workflow templates and the workflow engine."""

import pytest
from sqlalchemy import select

from autora.db.models import (
    ActivityState,
    Agent,
    AgentActivity,
    EventRecord,
    Project,
    Task,
    WorkflowRun,
)
from autora.runtime.activity import initialize_activity
from autora.runtime.actor import Actor
from autora.runtime.dag import (
    InvalidTemplate,
    NodeSpec,
    TemplateRegistry,
    WorkflowEngine,
    WorkflowError,
    WorkflowTemplate,
)
from autora.runtime.task_manager import TaskManager
from tests.conftest import unique_company

LINEAR = WorkflowTemplate(
    name="test.linear",
    nodes=(
        NodeSpec("research", "Research {topic}", "researcher"),
        NodeSpec("analysis", "Analyse {topic}", "analyst", depends_on=("research",)),
        NodeSpec("draft", "Draft {topic}", "writer", depends_on=("analysis",)),
    ),
)
DIAMOND = WorkflowTemplate(
    name="test.diamond",
    nodes=(
        NodeSpec("research", "Research", "researcher"),
        NodeSpec("analysis", "Analysis", "analyst", depends_on=("research",)),
        NodeSpec("draft", "Draft", "writer", depends_on=("research",)),
        NodeSpec("review", "Review", "editor", depends_on=("analysis", "draft")),
    ),
)


# --- templates ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("nodes", "message"),
    [
        ((), "at least one node"),
        ((NodeSpec("a", "A", "r"), NodeSpec("a", "A2", "r")), "duplicate"),
        ((NodeSpec("a", "A", "r", depends_on=("ghost",)),), "unknown dependencies"),
        (
            (
                NodeSpec("a", "A", "r", depends_on=("b",)),
                NodeSpec("b", "B", "r", depends_on=("a",)),
            ),
            "cycle",
        ),
        ((NodeSpec("Bad Name", "A", "r"),), "invalid node name"),
    ],
)
def test_invalid_templates(nodes, message):
    with pytest.raises(InvalidTemplate, match=message):
        WorkflowTemplate(name="test.bad", nodes=nodes)


def test_template_name_format():
    with pytest.raises(InvalidTemplate, match="dotted"):
        WorkflowTemplate(name="Story To Article", nodes=(NodeSpec("a", "A", "r"),))


def test_topological_order_is_stable():
    shuffled = WorkflowTemplate(
        name="test.shuffled",
        nodes=(
            NodeSpec("review", "R", "editor", depends_on=("analysis", "draft")),
            NodeSpec("draft", "D", "writer", depends_on=("research",)),
            NodeSpec("research", "S", "researcher"),
            NodeSpec("analysis", "A", "analyst", depends_on=("research",)),
        ),
    )
    assert [n.name for n in shuffled.topological_order()] == [
        "research",
        "draft",
        "analysis",
        "review",
    ]


def test_registry():
    registry = TemplateRegistry()
    registry.register(LINEAR)
    assert registry.get("test.linear") is LINEAR
    with pytest.raises(InvalidTemplate, match="already registered"):
        registry.register(LINEAR)
    with pytest.raises(WorkflowError, match="unknown workflow template"):
        registry.get("test.missing")


# --- engine ------------------------------------------------------------------------------


@pytest.fixture
async def world(db_session):
    company = await unique_company(db_session, "dag")
    project = Project(company_id=company.id, name="p")
    db_session.add(project)
    await db_session.flush()
    agents = {}
    for role in ("researcher", "analyst", "writer", "editor"):
        agent = Agent(company_id=company.id, role=role, display_name=role.title())
        db_session.add(agent)
        await db_session.flush()
        await initialize_activity(db_session, agent, actor=Actor.system("setup"))
        agents[role] = agent
    templates = TemplateRegistry()
    templates.register(LINEAR)
    templates.register(DIAMOND)
    tm = TaskManager()
    engine = WorkflowEngine(tm, templates)
    return {"company": company, "project": project, "agents": agents, "tm": tm, "engine": engine}


async def _start(world, session, template, **params):
    return await world["engine"].instantiate(
        session,
        template,
        company_id=world["company"].id,
        project_id=world["project"].id,
        params=params,
    )


async def _run_task(world, session, role, *, ok=True, retryable=False):
    claim = await world["tm"].claim_next(session, world["agents"][role], "w1")
    assert claim is not None, f"no READY task for {role}"
    if ok:
        return claim, await world["tm"].succeed(session, claim, {"done_by": role})
    await world["tm"].fail(session, claim, error_class="E", message="m", retryable=retryable)
    return claim, []


async def _state(session, task):
    return (await session.get(Task, task.id, populate_existing=True)).state


async def _run_events(session, run_id):
    rows = await session.scalars(
        select(EventRecord)
        .where(EventRecord.aggregate_id == run_id, EventRecord.aggregate_type == "workflow_run")
        .order_by(EventRecord.seq)
    )
    return [(r.event_type, r.payload) for r in rows]


def test_engine_binds_once(world):
    with pytest.raises(WorkflowError, match="already bound"):
        WorkflowEngine(world["tm"], TemplateRegistry())


async def test_instantiate_creates_run_and_tasks(db_session, world):
    run, tasks = await _start(world, db_session, "test.linear", topic="EU AI Act")

    assert run.state == "RUNNING" and run.params == {"topic": "EU AI Act"}
    assert [t.state for t in tasks.values()] == ["READY", "PENDING", "PENDING"]
    assert tasks["analysis"].depends_on == [tasks["research"].id]
    assert tasks["draft"].depends_on == [tasks["analysis"].id]
    assert tasks["research"].display_name == "Research EU AI Act"
    assert tasks["research"].input == {"params": {"topic": "EU AI Act"}}
    assert all(t.workflow_run_id == run.id for t in tasks.values())

    [(kind, created)] = await _run_events(db_session, run.id)
    assert kind == "WORKFLOW_RUN_CREATED"
    assert created["task_ids"] == [str(tasks[n].id) for n in ("research", "analysis", "draft")]
    first_task_event = await db_session.scalar(
        select(EventRecord.seq).where(EventRecord.task_id == tasks["research"].id).limit(1)
    )
    run_created = await db_session.scalar(
        select(EventRecord.seq).where(EventRecord.event_type == "WORKFLOW_RUN_CREATED",
                                      EventRecord.aggregate_id == run.id)
    )  # fmt: skip
    assert run_created < first_task_event, "the run is announced before its tasks"

    analyst = await db_session.get(AgentActivity, world["agents"]["analyst"].id)
    assert analyst.state == ActivityState.WAITING


async def test_missing_display_param_is_an_error(db_session, world):
    with pytest.raises(WorkflowError, match="needs parameter 'topic'"):
        await _start(world, db_session, "test.linear")


async def test_linear_workflow_hands_off_and_completes(db_session, world):
    run, tasks = await _start(world, db_session, "test.linear", topic="x")

    claim, unlocked = await _run_task(world, db_session, "researcher")
    assert [(u.task_id, u.required_role) for u in unlocked] == [(tasks["analysis"].id, "analyst")]
    assert await _state(db_session, tasks["analysis"]) == "READY"
    assert await _state(db_session, tasks["draft"]) == "PENDING"
    succeeded = await db_session.scalar(
        select(EventRecord.payload).where(
            EventRecord.task_id == tasks["research"].id, EventRecord.event_type == "TASK_SUCCEEDED"
        )
    )
    assert succeeded["unlocks"] == [
        {"task_id": str(tasks["analysis"].id), "required_role": "analyst"}
    ]

    await _run_task(world, db_session, "analyst")
    _, last = await _run_task(world, db_session, "writer")
    assert last == []

    run = await db_session.get(WorkflowRun, run.id, populate_existing=True)
    assert run.state == "SUCCEEDED" and run.finished_at is not None
    assert [k for k, _ in await _run_events(db_session, run.id)] == [
        "WORKFLOW_RUN_CREATED",
        "WORKFLOW_RUN_COMPLETED",
    ]


async def test_diamond_join_waits_for_all_parents(db_session, world):
    _, tasks = await _start(world, db_session, "test.diamond")
    _, unlocked = await _run_task(world, db_session, "researcher")
    assert {u.required_role for u in unlocked} == {"analyst", "writer"}

    _, unlocked = await _run_task(world, db_session, "analyst")
    assert unlocked == [], "review still waits for draft"
    assert await _state(db_session, tasks["review"]) == "PENDING"

    _, unlocked = await _run_task(world, db_session, "writer")
    assert [u.required_role for u in unlocked] == ["editor"]
    assert await _state(db_session, tasks["review"]) == "READY"


async def test_final_failure_cancels_only_dependents(db_session, world):
    run, tasks = await _start(world, db_session, "test.diamond")
    await _run_task(world, db_session, "researcher")
    await _run_task(world, db_session, "writer", ok=False, retryable=False)

    assert await _state(db_session, tasks["draft"]) == "FAILED"
    assert await _state(db_session, tasks["review"]) == "CANCELLED"
    assert await _state(db_session, tasks["analysis"]) == "READY", "independent branch continues"
    run = await db_session.get(WorkflowRun, run.id, populate_existing=True)
    assert run.state == "RUNNING"

    await _run_task(world, db_session, "analyst")
    run = await db_session.get(WorkflowRun, run.id, populate_existing=True)
    assert run.state == "FAILED"
    failed = dict(await _run_events(db_session, run.id))["WORKFLOW_RUN_FAILED"]
    assert failed["failed_task_id"] == str(tasks["draft"].id)

    editor = await db_session.get(
        AgentActivity, world["agents"]["editor"].id, populate_existing=True
    )
    assert editor.state == ActivityState.IDLE, "nobody waits for a cancelled task"


async def test_cancellation_is_transitive(db_session, world):
    run, tasks = await _start(world, db_session, "test.linear", topic="x")
    await _run_task(world, db_session, "researcher", ok=False, retryable=False)
    assert [await _state(db_session, tasks[n]) for n in ("research", "analysis", "draft")] == [
        "FAILED",
        "CANCELLED",
        "CANCELLED",
    ]
    assert (await db_session.get(WorkflowRun, run.id, populate_existing=True)).state == "FAILED"


async def test_retryable_failure_does_not_touch_downstream(db_session, world):
    _, tasks = await _start(world, db_session, "test.linear", topic="x")
    await _run_task(world, db_session, "researcher", ok=False, retryable=True)
    assert await _state(db_session, tasks["research"]) == "READY"
    assert await _state(db_session, tasks["analysis"]) == "PENDING"


async def test_cancel_workflow(db_session, world):
    run, tasks = await _start(world, db_session, "test.diamond")
    claim = await world["tm"].claim_next(db_session, world["agents"]["researcher"], "w1")

    await world["engine"].cancel(db_session, run, reason="operator stopped the story")

    assert {await _state(db_session, t) for t in tasks.values()} == {"CANCELLED"}
    run = await db_session.get(WorkflowRun, run.id, populate_existing=True)
    assert run.state == "CANCELLED"
    cancelled = dict(await _run_events(db_session, run.id))["WORKFLOW_RUN_CANCELLED"]
    assert cancelled["reason"] == "operator stopped the story"
    researcher = await db_session.get(AgentActivity, claim.agent.id, populate_existing=True)
    assert researcher.state == ActivityState.IDLE


async def test_tasks_outside_workflows_are_unaffected(db_session, world):
    task = await world["tm"].add_task(
        db_session,
        company_id=world["company"].id,
        project_id=world["project"].id,
        name="standalone",
        display_name="Standalone",
        required_role="researcher",
    )
    _, unlocked = await _run_task(world, db_session, "researcher")
    assert unlocked == [] and await _state(db_session, task) == "SUCCEEDED"
