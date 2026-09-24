"""T-604: the command pipeline — the only way anything changes the company.

What these tests are really about: every attempt is decided by the policy and recorded whatever
happened, the same key never does the same thing twice, and a command a person must approve
waits rather than half-running.
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from autora.app import build_policy_engine, build_runtime
from autora.company import verbs
from autora.company.commands import CommandBus, UnknownCommand
from autora.company.organization import add_business_unit
from autora.db.models import (
    Approval,
    ApprovalState,
    Budget,
    BusinessUnitState,
    CommandRecord,
    CompanyGoal,
    Cycle,
    EventRecord,
    PolicyDecision,
    Project,
    ProjectState,
    Task,
    TaskState,
)
from autora.runtime.actor import Actor
from autora.runtime.approvals import ApprovalService
from autora.runtime.task_manager import TaskManager
from tests.conftest import unique_company

CEO = Actor.agent(uuid.uuid4())
HUMAN = Actor.human("founder")


@pytest.fixture
def bus():
    approvals = ApprovalService(TaskManager())
    bus = CommandBus(policy=build_policy_engine(), approvals=approvals)
    verbs.register(bus)
    bus.install()
    return bus


async def _world(session, *, with_unit: bool = False):
    company = await unique_company(session, "cmd")
    unit = None
    if with_unit:
        unit = await add_business_unit(
            session, company_id=company.id, key="ai_media", name="AI Media",
            actor=HUMAN, state=BusinessUnitState.ACTIVE,
        )  # fmt: skip
    project = Project(
        company_id=company.id,
        business_unit_id=unit.id if unit else None,
        name="p",
        state=ProjectState.ACTIVE.value,
        kill_criteria={"max_cost_usd": 5},
    )
    session.add(project)
    await session.flush()
    return company, unit, project


TOPIC = {"topic": "microgrids"}


def _key() -> str:
    return f"k-{uuid.uuid4().hex[:12]}"


# --- deciding and recording --------------------------------------------------------------------


async def test_a_command_is_decided_recorded_and_performed(db_session, bus):
    company, _, _ = await _world(db_session)

    result = await bus.submit(
        db_session,
        "CreateCycleGoal",
        {"title": "Publish 3 bilingual articles", "metric": "published_articles", "target": 3},
        company_id=company.id,
        actor=CEO,
        role="ceo",
        idempotency_key=_key(),
    )

    assert result.done
    assert result.record.command == "CreateCycleGoal"
    assert result.record.decision == "allow"
    assert result.record.actor == CEO.as_json() and result.record.role == "ceo"
    goal = await db_session.get(CompanyGoal, uuid.UUID(result.result["goal_id"]))
    assert goal.title == "Publish 3 bilingual articles"
    # the events it caused are on the record, so a change traces back to the decision
    (event_id,) = result.record.event_ids
    event = await db_session.get(EventRecord, event_id)
    assert event.event_type == "GOAL_CREATED"
    # and the policy decision is recorded on its own, as it is for tools
    decision = await db_session.scalar(
        select(PolicyDecision).where(PolicyDecision.company_id == company.id)
    )
    assert decision.action == "create_cycle_goal"


async def test_a_refusal_is_a_result_not_an_error(db_session, bus):
    """A company that denies its CEO something has done something worth seeing."""
    company, _, _ = await _world(db_session)

    result = await bus.submit(
        db_session,
        "CreateCycleGoal",
        {"title": "x", "metric": "published_articles", "target": 1},
        company_id=company.id,
        actor=CEO,
        role="writer",  # writers do not set the company's goals
        idempotency_key=_key(),
    )

    assert not result.done
    assert result.record.outcome == "refused"
    assert result.record.decision == "deny"
    assert "writer" in result.record.reason
    assert (
        await db_session.scalar(select(CompanyGoal.id).where(CompanyGoal.company_id == company.id))
        is None
    )


async def test_a_payload_that_is_not_that_command_is_refused_before_anything_runs(db_session, bus):
    company, _, _ = await _world(db_session)

    result = await bus.submit(
        db_session,
        "CreateCycleGoal",
        {"title": "", "metric": "Not A Metric", "target": -5},
        company_id=company.id,
        actor=HUMAN,
        idempotency_key=_key(),
    )

    assert result.record.outcome == "refused"
    assert "metric" in result.record.reason
    # never even decided: the payload was not a command of that name
    assert (
        await db_session.scalar(
            select(PolicyDecision.id).where(PolicyDecision.company_id == company.id)
        )
        is None
    )


async def test_the_state_of_the_company_can_refuse_what_the_policy_allowed(db_session, bus):
    company, _, project = await _world(db_session)
    project.state = ProjectState.PAUSED.value
    await db_session.flush()

    result = await bus.submit(
        db_session,
        "PauseProject",
        {"project_id": str(project.id)},
        company_id=company.id,
        actor=CEO,
        role="ceo",
        idempotency_key=_key(),
    )

    assert result.record.outcome == "refused"
    assert result.record.decision == "allow"  # the policy said yes; the company said no
    assert "not ACTIVE" in result.record.reason


async def test_an_unknown_command_is_an_error_not_a_record(db_session, bus):
    company, _, _ = await _world(db_session)

    with pytest.raises(UnknownCommand, match="known:"):
        await bus.submit(
            db_session, "BuyACompany", {}, company_id=company.id, actor=HUMAN,
            idempotency_key=_key(),
        )  # fmt: skip


# --- the same key twice -------------------------------------------------------------------------


async def test_the_same_key_does_not_do_it_twice(db_session, bus):
    """An agent retrying a tool call, a double-clicked button, a redelivered webhook."""
    company, _, _ = await _world(db_session)
    key = _key()
    args = {"title": "Publish 3", "metric": "published_articles", "target": 3}

    first = await bus.submit(
        db_session, "CreateCycleGoal", args, company_id=company.id, actor=HUMAN,
        idempotency_key=key,
    )  # fmt: skip
    again = await bus.submit(
        db_session, "CreateCycleGoal", args, company_id=company.id, actor=HUMAN,
        idempotency_key=key,
    )  # fmt: skip

    assert first.record.id == again.record.id
    assert again.result == first.result
    goals = (
        await db_session.scalars(select(CompanyGoal.id).where(CompanyGoal.company_id == company.id))
    ).all()
    assert len(goals) == 1


async def test_a_refusal_is_remembered_too(db_session, bus):
    company, _, _ = await _world(db_session)
    key = _key()
    args = {"title": "x", "metric": "m", "target": 1}

    first = await bus.submit(
        db_session, "CreateCycleGoal", args, company_id=company.id, actor=CEO, role="writer",
        idempotency_key=key,
    )  # fmt: skip
    again = await bus.submit(
        db_session, "CreateCycleGoal", args, company_id=company.id, actor=HUMAN,
        idempotency_key=key,
    )  # fmt: skip

    # even as a human, the same key gets the first answer: the key is the identity of the ask
    assert again.record.id == first.record.id
    assert again.record.outcome == "refused"


# --- approval -----------------------------------------------------------------------------------


async def test_a_command_a_person_must_approve_waits(db_session, bus):
    company, _, project = await _world(db_session)

    result = await bus.submit(
        db_session,
        "KillProject",
        {"project_id": str(project.id), "reason": "cost per article too high"},
        company_id=company.id,
        actor=CEO,
        role="ceo",
        idempotency_key=_key(),
    )

    assert result.awaiting and result.approval is not None
    assert result.approval.summary.startswith("Kill project")
    assert result.approval.payload["command"] == "KillProject"
    assert (await db_session.get(Project, project.id)).state == ProjectState.ACTIVE.value


async def test_approving_runs_it_under_the_same_key(db_session, bus):
    company, _, project = await _world(db_session)
    key = _key()
    result = await bus.submit(
        db_session, "KillProject", {"project_id": str(project.id), "reason": "too costly"},
        company_id=company.id, actor=CEO, role="ceo", idempotency_key=key,
    )  # fmt: skip

    await bus.approvals.decide(
        db_session, result.approval.id, outcome="approve", actor=HUMAN, reason="agreed"
    )

    assert (await db_session.get(Project, project.id)).state == ProjectState.KILLED.value
    record = await db_session.get(CommandRecord, result.record.id)
    assert record.outcome == "done"
    assert record.result["state"] == "KILLED"
    assert record.event_ids  # the kill event is attributed to the command
    # and the key is still the key
    again = await bus.submit(
        db_session, "KillProject", {"project_id": str(project.id), "reason": "too costly"},
        company_id=company.id, actor=CEO, role="ceo", idempotency_key=key,
    )  # fmt: skip
    assert again.record.id == record.id


async def test_rejecting_leaves_the_company_as_it_was(db_session, bus):
    company, _, project = await _world(db_session)
    result = await bus.submit(
        db_session, "KillProject", {"project_id": str(project.id), "reason": "too costly"},
        company_id=company.id, actor=CEO, role="ceo", idempotency_key=_key(),
    )  # fmt: skip

    await bus.approvals.decide(
        db_session, result.approval.id, outcome="reject", actor=HUMAN, reason="give it a week"
    )

    assert (await db_session.get(Project, project.id)).state == ProjectState.ACTIVE.value
    record = await db_session.get(CommandRecord, result.record.id)
    assert record.outcome == "refused"
    assert "give it a week" in record.reason


async def test_a_command_approved_after_the_world_moved_on_is_refused(db_session, bus):
    """Approval is not a promise that it is still possible."""
    company, _, project = await _world(db_session)
    result = await bus.submit(
        db_session, "KillProject", {"project_id": str(project.id), "reason": "too costly"},
        company_id=company.id, actor=CEO, role="ceo", idempotency_key=_key(),
    )  # fmt: skip
    project.state = ProjectState.COMPLETED.value  # it finished while the person was deciding
    await db_session.flush()

    await bus.approvals.decide(
        db_session, result.approval.id, outcome="approve", actor=HUMAN, reason="ok"
    )

    record = await db_session.get(CommandRecord, result.record.id)
    assert record.outcome == "refused" and "already COMPLETED" in record.reason
    assert (await db_session.get(Project, project.id)).state == ProjectState.COMPLETED.value


# --- money --------------------------------------------------------------------------------------


async def test_allocating_a_budget_and_raising_it_frees_the_work_it_blocked(db_session, bus):
    """The gap the runbook warned about: raising a budget used to change a number and nothing
    else, leaving the work stuck until somebody noticed (AC-13, P-5)."""
    company, _, project = await _world(db_session)
    blocked = Task(
        company_id=company.id, project_id=project.id, name="draft", display_name="Draft",
        required_role="writer", state=TaskState.BLOCKED_BUDGET.value, attempt=1,
    )  # fmt: skip
    db_session.add(blocked)
    await db_session.flush()

    first = await bus.submit(
        db_session, "AllocateBudget", {"amount": "1.00", "project_id": str(project.id)},
        company_id=company.id, actor=HUMAN, idempotency_key=_key(),
    )  # fmt: skip

    assert first.done
    assert first.result["released_tasks"] == [str(blocked.id)]
    assert (await db_session.get(Task, blocked.id)).state == TaskState.READY.value
    budget = await db_session.get(Budget, uuid.UUID(first.result["budget_id"]))
    assert budget.amount == Decimal("1.00") and budget.project_id == project.id


async def test_lowering_a_budget_frees_nothing(db_session, bus):
    company, _, project = await _world(db_session)
    await bus.submit(
        db_session, "AllocateBudget", {"amount": "5.00", "project_id": str(project.id)},
        company_id=company.id, actor=HUMAN, idempotency_key=_key(),
    )  # fmt: skip
    blocked = Task(
        company_id=company.id, project_id=project.id, name="draft", display_name="Draft",
        required_role="writer", state=TaskState.BLOCKED_BUDGET.value, attempt=1,
    )  # fmt: skip
    db_session.add(blocked)
    await db_session.flush()

    lowered = await bus.submit(
        db_session, "AllocateBudget", {"amount": "2.00", "project_id": str(project.id)},
        company_id=company.id, actor=HUMAN, idempotency_key=_key(),
    )  # fmt: skip

    assert lowered.result["released_tasks"] == []
    assert (await db_session.get(Task, blocked.id)).state == TaskState.BLOCKED_BUDGET.value


async def test_a_budget_belongs_to_one_scope(db_session, bus):
    company, unit, project = await _world(db_session, with_unit=True)

    result = await bus.submit(
        db_session,
        "AllocateBudget",
        {"amount": "5", "project_id": str(project.id), "business_unit_id": str(unit.id)},
        company_id=company.id,
        actor=HUMAN,
        idempotency_key=_key(),
    )

    assert result.record.outcome == "refused"
    assert "not both" in result.record.reason


async def test_a_business_can_have_its_own_envelope(db_session, bus):
    company, unit, _ = await _world(db_session, with_unit=True)

    result = await bus.submit(
        db_session, "AllocateBudget",
        {"amount": "20", "business_unit_id": str(unit.id), "period": "day"},
        company_id=company.id, actor=HUMAN, idempotency_key=_key(),
    )  # fmt: skip

    budget = await db_session.get(Budget, uuid.UUID(result.result["budget_id"]))
    assert budget.business_unit_id == unit.id and budget.project_id is None


async def test_a_budget_for_something_that_is_not_there(db_session, bus):
    company, _, _ = await _world(db_session)

    result = await bus.submit(
        db_session, "AllocateBudget", {"amount": "5", "project_id": str(uuid.uuid4())},
        company_id=company.id, actor=HUMAN, idempotency_key=_key(),
    )  # fmt: skip

    assert result.record.outcome == "refused" and "no project" in result.record.reason


# --- projects -----------------------------------------------------------------------------------


async def test_pausing_and_resuming_a_project(db_session, bus):
    company, _, project = await _world(db_session)

    paused = await bus.submit(
        db_session, "PauseProject", {"project_id": str(project.id), "reason": "over budget"},
        company_id=company.id, actor=CEO, role="ceo", idempotency_key=_key(),
    )  # fmt: skip
    assert paused.done and paused.result["state"] == "PAUSED"

    resumed = await bus.submit(
        db_session, "ResumeProject", {"project_id": str(project.id)},
        company_id=company.id, actor=HUMAN, idempotency_key=_key(),
    )  # fmt: skip
    assert resumed.done and resumed.result["state"] == "ACTIVE"

    events = (
        await db_session.scalars(
            select(EventRecord.event_type)
            .where(EventRecord.company_id == company.id)
            .order_by(EventRecord.seq)
        )
    ).all()
    assert list(events) == ["PROJECT_PAUSED", "PROJECT_RESUMED"]


async def test_the_ceo_pausing_says_it_was_the_ceo(db_session, bus):
    """The event's trigger is what tells auto-pause from a decision (T-606 will add the third)."""
    company, _, project = await _world(db_session)

    await bus.submit(
        db_session, "PauseProject", {"project_id": str(project.id)},
        company_id=company.id, actor=CEO, role="ceo", idempotency_key=_key(),
    )  # fmt: skip

    event = await db_session.scalar(
        select(EventRecord).where(
            EventRecord.company_id == company.id,
            EventRecord.event_type == "PROJECT_PAUSED",
        )
    )
    assert event.payload["trigger"] == "ceo"


async def test_creating_a_project_needs_a_person_and_kill_criteria(db_session, bus):
    company, _, _ = await _world(db_session)

    missing = await bus.submit(
        db_session, "CreateProject", {"name": "Newsletter"},
        company_id=company.id, actor=HUMAN, idempotency_key=_key(),
    )  # fmt: skip
    assert missing.record.outcome == "refused"
    assert "kill_criteria" in missing.record.reason

    asked = await bus.submit(
        db_session,
        "CreateProject",
        {"name": "Newsletter", "kill_criteria": {"max_cost_usd": 10}},
        company_id=company.id,
        actor=CEO,
        role="ceo",
        idempotency_key=_key(),
    )
    assert asked.awaiting  # a project is a standing commitment: a person decides


# --- work ----------------------------------------------------------------------------------------


async def test_starting_work_is_counted_against_the_cycle_s_cap(db_session):
    """Gate 5: the CEO's proposals are capped, and the cap counts this cycle's runs."""
    runtime = build_runtime()
    bus = runtime.commands
    company, _, project = await _world(db_session)
    cycle = Cycle(company_id=company.id, seq=1, stage="PLANNING")
    db_session.add(cycle)
    await db_session.flush()

    facts = await verbs.workflows_this_cycle(
        db_session,
        company.id,
        verbs.InstantiateWorkflow(template="echo.chain_v1", project_id=project.id),
    )
    assert facts == {"workflows_in_cycle": 0}

    result = await bus.submit(
        db_session,
        "InstantiateWorkflow",
        {
            "template": "echo.chain_v1",
            "project_id": str(project.id),
            "params": {"topic": "microgrids"},
        },
        company_id=company.id,
        actor=CEO,
        role="ceo",
        idempotency_key=_key(),
    )

    assert result.done, result.record.reason
    assert result.result["tasks"] >= 1
    run_id = uuid.UUID(result.result["workflow_run_id"])
    tasks = (await db_session.scalars(select(Task).where(Task.workflow_run_id == run_id))).all()
    assert all(task.cycle_id == cycle.id for task in tasks)  # cycle -> workflow -> task
    assert (
        await verbs.workflows_this_cycle(
            db_session,
            company.id,
            verbs.InstantiateWorkflow(
                template="echo.chain_v1", project_id=project.id, params={"topic": "microgrids"}
            ),
        )
    ) == {"workflows_in_cycle": 1}


async def test_a_finished_workflow_can_be_run_again(db_session):
    """AC-9's last sentence: a person can restart a failed workflow."""
    runtime = build_runtime()
    bus = runtime.commands
    company, _, project = await _world(db_session)
    started = await bus.submit(
        db_session, "InstantiateWorkflow",
        {"template": "echo.chain_v1", "project_id": str(project.id), "params": TOPIC},
        company_id=company.id, actor=HUMAN, idempotency_key=_key(),
    )  # fmt: skip
    from autora.db.models import WorkflowRun

    run = await db_session.get(WorkflowRun, uuid.UUID(started.result["workflow_run_id"]))
    run.state = "FAILED"
    await db_session.flush()

    restarted = await bus.submit(
        db_session, "RestartWorkflow", {"workflow_run_id": str(run.id)},
        company_id=company.id, actor=HUMAN, idempotency_key=_key(),
    )  # fmt: skip

    assert restarted.done
    assert restarted.result["restarted_from"] == str(run.id)
    assert restarted.result["workflow_run_id"] != str(run.id)  # a new run, not a resumed one
    assert (await db_session.get(WorkflowRun, run.id)).state == "FAILED"  # history kept


async def test_a_running_workflow_is_not_restarted(db_session):
    runtime = build_runtime()
    bus = runtime.commands
    company, _, project = await _world(db_session)
    started = await bus.submit(
        db_session, "InstantiateWorkflow",
        {"template": "echo.chain_v1", "project_id": str(project.id), "params": TOPIC},
        company_id=company.id, actor=HUMAN, idempotency_key=_key(),
    )  # fmt: skip

    again = await bus.submit(
        db_session, "RestartWorkflow",
        {"workflow_run_id": started.result["workflow_run_id"]},
        company_id=company.id, actor=HUMAN, idempotency_key=_key(),
    )  # fmt: skip

    assert again.record.outcome == "refused" and "still RUNNING" in again.record.reason


# --- strategy -------------------------------------------------------------------------------------


async def test_the_strategy_a_person_approves_is_what_the_next_snapshot_reads(db_session, bus):
    company, _, _ = await _world(db_session)
    asked = await bus.submit(
        db_session, "UpdateStrategy", {"summary": "Own bilingual local news."},
        company_id=company.id, actor=CEO, role="ceo", idempotency_key=_key(),
    )  # fmt: skip
    assert asked.awaiting

    await bus.approvals.decide(
        db_session, asked.approval.id, outcome="approve", actor=HUMAN, reason="yes"
    )

    from autora.company.reporting import Reporting
    from autora.company.snapshot import SnapshotBuilder

    snapshot = await SnapshotBuilder(Reporting()).build(db_session, company.id)
    assert snapshot.strategy_summary == "Own bilingual local news."


async def test_every_attempt_is_in_the_log_whatever_happened(db_session, bus):
    company, _, project = await _world(db_session)
    for name, args, actor, role in (
        ("CreateCycleGoal", {"title": "g", "metric": "m", "target": 1}, HUMAN, None),
        ("CreateCycleGoal", {"title": "g", "metric": "m", "target": 1}, CEO, "writer"),
        ("KillProject", {"project_id": str(project.id), "reason": "r"}, CEO, "ceo"),
    ):
        await bus.submit(
            db_session, name, args, company_id=company.id, actor=actor, role=role,
            idempotency_key=_key(),
        )  # fmt: skip

    records = (
        await db_session.scalars(
            select(CommandRecord)
            .where(CommandRecord.company_id == company.id)
            .order_by(CommandRecord.created_at, CommandRecord.id)  # ties: one now() per txn
        )
    ).all()
    assert [r.outcome for r in records] == ["done", "refused", "awaiting_approval"]
    assert [r.command for r in records] == [
        "CreateCycleGoal",
        "CreateCycleGoal",
        "KillProject",
    ]
    pending = await db_session.scalar(
        select(Approval).where(
            Approval.company_id == company.id, Approval.state == ApprovalState.PENDING.value
        )
    )
    assert pending is not None
