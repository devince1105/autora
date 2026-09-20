"""T-206: approvals (agent-run approvals, human task nodes, standalone, expiry)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from autora.db.models import (
    ActivityState,
    Agent,
    AgentActivity,
    AgentRun,
    Approval,
    ApprovalKind,
    EventRecord,
    Project,
    Task,
)
from autora.runtime.activity import initialize_activity
from autora.runtime.actor import Actor
from autora.runtime.approvals import ApprovalError, ApprovalService
from autora.runtime.dag import NodeSpec, TemplateRegistry, WorkflowEngine, WorkflowTemplate
from autora.runtime.task_manager import AgentBusy, TaskManager
from tests.conftest import unique_company

OPERATOR = Actor.human("operator")
PIPELINE = WorkflowTemplate(
    name="test.approve_then_publish",
    nodes=(
        NodeSpec("draft", "Draft", "writer"),
        NodeSpec("approve", "Approve article", "human", depends_on=("draft",)),
        NodeSpec("publish", "Publish", "publisher", depends_on=("approve",)),
    ),
)

DOMAIN_KIND = "shipment"
"""A kind this domain-less layer has never heard of. The runtime stores the token and
asks a person about it; what it means belongs to whoever asked (ARCHITECTURE_V2_1 §9)."""


class Clock:
    def __init__(self):
        self.now = datetime(2026, 9, 18, 6, 0, tzinfo=UTC)

    def __call__(self):
        return self.now


@pytest.fixture
async def world(db_session):
    company = await unique_company(db_session, "appr")
    project = Project(company_id=company.id, name="p")
    db_session.add(project)
    await db_session.flush()
    agents = {}
    for name, role in (("w1", "writer"), ("w2", "writer"), ("pub", "publisher")):
        agent = Agent(company_id=company.id, role=role, display_name=name)
        db_session.add(agent)
        await db_session.flush()
        await initialize_activity(db_session, agent, actor=Actor.system("setup"))
        agents[name] = agent
    clock = Clock()
    tm = TaskManager(clock=clock)
    templates = TemplateRegistry()
    templates.register(PIPELINE)
    engine = WorkflowEngine(tm, templates)
    service = ApprovalService(tm, clock=clock)
    return {
        "company": company,
        "project": project,
        "agents": agents,
        "tm": tm,
        "engine": engine,
        "approvals": service,
        "clock": clock,
    }


async def _task(world, session, name="draft", role="writer"):
    return await world["tm"].add_task(
        session,
        company_id=world["company"].id,
        project_id=world["project"].id,
        name=name,
        display_name=name.title(),
        required_role=role,
    )


async def _request_for_run(world, session, claim):
    return await world["approvals"].request_for_run(
        session,
        claim,
        kind=ApprovalKind.TOOL_CALL,
        action="publish_article",
        payload={"tool": "publish_article", "args": {"article_id": "a1"}},
        summary="Writer wants to publish article a1",
    )


async def _reload(session, model, id_):
    return await session.get(model, id_, populate_existing=True)


async def _activity(session, agent):
    return await _reload(session, AgentActivity, agent.id)


# --- approval in the middle of an agent run ----------------------------------------------


async def test_run_is_suspended_and_resumed_by_the_same_agent(db_session, world):
    tm, w1, w2 = world["tm"], world["agents"]["w1"], world["agents"]["w2"]
    task = await _task(world, db_session)
    claim = await tm.claim_next(db_session, w1, "worker-1")
    approval = await _request_for_run(world, db_session, claim)

    task = await _reload(db_session, Task, task.id)
    run = await _reload(db_session, AgentRun, claim.run.id)
    assert task.state == "WAITING_APPROVAL" and task.lease_owner is None
    assert run.state == "WAITING_APPROVAL"
    activity = await _activity(db_session, w1)
    assert activity.state == ActivityState.WAITING
    assert activity.detail["reason"] == "approval"
    assert activity.detail["approval_id"] == str(approval.id)
    assert approval.expires_at == world["clock"].now + timedelta(hours=24)

    with pytest.raises(AgentBusy, match="waiting for an approval"):
        await tm.claim_next(db_session, w1, "worker-1")

    await world["approvals"].decide(db_session, approval.id, outcome="approve", actor=OPERATOR)
    assert (await _reload(db_session, Task, task.id)).state == "READY"
    assert await tm.claim_next(db_session, w2, "worker-2") is None, "only w1 may resume it"

    resumed = await tm.claim_next(db_session, w1, "worker-1")
    assert resumed.resumed and resumed.run.id == claim.run.id and resumed.task.attempt == 1
    [granted] = await world["approvals"].approved_for_run(db_session, claim.run.id)
    assert granted.payload["args"] == {"article_id": "a1"}

    await tm.succeed(db_session, resumed, {"published": True})
    assert (await _reload(db_session, AgentRun, claim.run.id)).state == "COMPLETED"

    kinds = (
        await db_session.scalars(
            select(EventRecord.event_type)
            .where(EventRecord.aggregate_id == approval.id)
            .order_by(EventRecord.seq)
        )
    ).all()
    assert kinds == ["APPROVAL_REQUESTED", "APPROVAL_APPROVED"]


async def test_rejection_cancels_task_and_frees_agent(db_session, world):
    tm, w1 = world["tm"], world["agents"]["w1"]
    task = await _task(world, db_session)
    claim = await tm.claim_next(db_session, w1, "worker-1")
    approval = await _request_for_run(world, db_session, claim)

    await world["approvals"].decide(
        db_session, approval.id, outcome="reject", actor=OPERATOR, reason="not newsworthy"
    )
    assert (await _reload(db_session, Task, task.id)).state == "CANCELLED"
    assert (await _reload(db_session, AgentRun, claim.run.id)).state == "ABORTED"
    assert (await _activity(db_session, w1)).state == ActivityState.IDLE
    decided = await _reload(db_session, Approval, approval.id)
    assert decided.state == "REJECTED" and decided.reason == "not newsworthy"
    assert decided.decided_by == OPERATOR.as_json()

    another = await _task(world, db_session, name="next_draft")
    assert (await tm.claim_next(db_session, w1, "worker-1")).task.id == another.id


async def test_cancelling_a_waiting_task_aborts_its_run(db_session, world):
    tm, w1 = world["tm"], world["agents"]["w1"]
    task = await _task(world, db_session)
    claim = await tm.claim_next(db_session, w1, "worker-1")
    approval = await _request_for_run(world, db_session, claim)

    await tm.cancel(db_session, task, reason="story dropped")
    assert (await _reload(db_session, AgentRun, claim.run.id)).state == "ABORTED"
    assert (await _activity(db_session, w1)).state == ActivityState.IDLE

    # Deciding afterwards records the decision but has nothing left to release.
    await world["approvals"].decide(db_session, approval.id, outcome="approve", actor=OPERATOR)
    assert (await _reload(db_session, Task, task.id)).state == "CANCELLED"


# --- human task node ----------------------------------------------------------------------


async def test_human_node_approval_unlocks_the_next_step(db_session, world):
    tm = world["tm"]
    run, tasks = await world["engine"].instantiate(
        db_session,
        "test.approve_then_publish",
        company_id=world["company"].id,
        project_id=world["project"].id,
    )
    claim = await tm.claim_next(db_session, world["agents"]["w1"], "worker-1")
    await tm.succeed(db_session, claim, {"article_id": "a1"})
    approve = await _reload(db_session, Task, tasks["approve"].id)
    assert approve.state == "READY"

    approval = await world["approvals"].request_for_task(
        db_session, approve, kind=DOMAIN_KIND, summary="Publish 'EU AI Act explained'?"
    )
    assert (await _reload(db_session, Task, approve.id)).state == "WAITING_APPROVAL"
    assert (await _reload(db_session, Task, tasks["publish"].id)).state == "PENDING"

    await world["approvals"].decide(db_session, approval.id, outcome="approve", actor=OPERATOR)

    approve = await _reload(db_session, Task, approve.id)
    assert approve.state == "SUCCEEDED"
    assert approve.output["approved_by"] == OPERATOR.as_json()
    assert (await _reload(db_session, Task, tasks["publish"].id)).state == "READY"
    succeeded = await db_session.scalar(
        select(EventRecord.payload).where(
            EventRecord.task_id == approve.id, EventRecord.event_type == "TASK_SUCCEEDED"
        )
    )
    assert succeeded["run_id"] is None
    assert succeeded["unlocks"] == [
        {"task_id": str(tasks["publish"].id), "required_role": "publisher"}
    ]


async def test_rejected_article_cancels_publishing(db_session, world):
    tm = world["tm"]
    run, tasks = await world["engine"].instantiate(
        db_session,
        "test.approve_then_publish",
        company_id=world["company"].id,
        project_id=world["project"].id,
    )
    claim = await tm.claim_next(db_session, world["agents"]["w1"], "worker-1")
    await tm.succeed(db_session, claim, {})
    approval = await world["approvals"].request_for_task(
        db_session,
        await _reload(db_session, Task, tasks["approve"].id),
        kind=DOMAIN_KIND,
        summary="Publish?",
    )
    await world["approvals"].decide(db_session, approval.id, outcome="reject", actor=OPERATOR)
    states = [(await _reload(db_session, Task, tasks[n].id)).state for n in ("approve", "publish")]
    assert states == ["CANCELLED", "CANCELLED"]


async def test_only_ready_tasks_can_await_an_approval(db_session, world):
    task = await _task(world, db_session)
    await world["tm"].claim_next(db_session, world["agents"]["w1"], "worker-1")
    with pytest.raises(ApprovalError, match="only READY"):
        await world["approvals"].request_for_task(db_session, task, kind=DOMAIN_KIND, summary="x")


# --- rules --------------------------------------------------------------------------------


async def test_only_humans_decide_and_only_once(db_session, world):
    approval = await world["approvals"].request(
        db_session,
        company_id=world["company"].id,
        kind=ApprovalKind.PROJECT,
        ref_type="project",
        ref_id=uuid.uuid4(),
        summary="CEO proposes a new project",
        requested_by=Actor.agent(uuid.uuid4()),
    )
    with pytest.raises(ApprovalError, match="only a human"):
        await world["approvals"].decide(
            db_session, approval.id, outcome="approve", actor=Actor.system("x")
        )
    await world["approvals"].decide(db_session, approval.id, outcome="approve", actor=OPERATOR)
    with pytest.raises(ApprovalError, match="already APPROVED"):
        await world["approvals"].decide(db_session, approval.id, outcome="reject", actor=OPERATOR)
    with pytest.raises(ApprovalError, match="not found"):
        await world["approvals"].decide(db_session, uuid.uuid4(), outcome="approve", actor=OPERATOR)


async def test_one_pending_request_per_subject(db_session, world):
    ref = uuid.uuid4()
    kwargs = {
        "company_id": world["company"].id,
        "kind": ApprovalKind.KILL,
        "ref_type": "project",
        "ref_id": ref,
        "summary": "Kill project?",
        "requested_by": Actor.agent(uuid.uuid4()),
    }
    await world["approvals"].request(db_session, **kwargs)
    with pytest.raises(ApprovalError, match="already has a pending approval"):
        await world["approvals"].request(db_session, **kwargs)


async def test_expired_approval_keeps_task_waiting(db_session, world):
    tm, w1 = world["tm"], world["agents"]["w1"]
    task = await _task(world, db_session)
    claim = await tm.claim_next(db_session, w1, "worker-1")
    approval = await _request_for_run(world, db_session, claim)

    world["clock"].now += timedelta(hours=23)
    assert await world["approvals"].expire_due(db_session) == []
    world["clock"].now += timedelta(hours=2)
    assert await world["approvals"].expire_due(db_session) == [approval.id]

    assert (await _reload(db_session, Approval, approval.id)).state == "EXPIRED"
    assert (await _reload(db_session, Task, task.id)).state == "WAITING_APPROVAL", "D-001"
    with pytest.raises(ApprovalError, match="already EXPIRED"):
        await world["approvals"].decide(db_session, approval.id, outcome="approve", actor=OPERATOR)

    # The operator can ask again; the new approval releases the same suspended run.
    again = await world["approvals"].request(
        db_session,
        company_id=world["company"].id,
        kind=ApprovalKind.TOOL_CALL,
        ref_type="agent_run",
        ref_id=claim.run.id,
        summary="re-requested",
        requested_by=OPERATOR,
    )
    assert again.state == "PENDING"
