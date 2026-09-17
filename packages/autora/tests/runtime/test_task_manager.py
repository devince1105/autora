"""T-202: task manager (queue, leases, retries, reaping)."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from autora.db.models import (
    ActivityState,
    Agent,
    AgentActivity,
    AgentRun,
    EventRecord,
    Project,
    Task,
)
from autora.runtime.activity import initialize_activity
from autora.runtime.actor import Actor
from autora.runtime.events import catalog as ev
from autora.runtime.task_manager import AgentBusy, LeaseLost, TaskManager
from tests.conftest import unique_company

SETUP = Actor.system("setup")
T0 = datetime(2026, 9, 17, 6, 0, tzinfo=UTC)


class Clock:
    def __init__(self, now=T0):
        self.now = now

    def __call__(self):
        return self.now


async def _world(session, roles=("researcher", "analyst")):
    company = await unique_company(session, "tm")
    project = Project(company_id=company.id, name="p")
    session.add(project)
    await session.flush()
    agents = {}
    for role in roles:
        agent = Agent(company_id=company.id, role=role, display_name=role.title())
        session.add(agent)
        await session.flush()
        await initialize_activity(session, agent, actor=SETUP)
        agents[role] = agent
    return company, project, agents


@pytest.fixture
async def world(db_session):
    company, project, agents = await _world(db_session)
    clock = Clock()
    manager = TaskManager(clock=clock, retry_base=timedelta(seconds=10))
    return {"company": company, "project": project, "agents": agents, "tm": manager, "clock": clock}


async def _add(world, session, name="research", role="researcher", **kw):
    return await world["tm"].add_task(
        session,
        company_id=world["company"].id,
        project_id=world["project"].id,
        name=name,
        display_name=f"Do {name}",
        required_role=role,
        **kw,
    )


async def _events(session, task_id, prefix=""):
    rows = await session.scalars(
        select(EventRecord)
        .where(EventRecord.task_id == task_id, EventRecord.event_type.like(f"{prefix}%"))
        .order_by(EventRecord.seq)
    )
    return [(r.event_type, r.payload) for r in rows]


async def _activity(session, agent):
    return await session.get(AgentActivity, agent.id, populate_existing=True)


# --- creating ----------------------------------------------------------------------------


async def test_task_without_dependencies_is_ready(db_session, world):
    task = await _add(world, db_session, budget_usd=None)
    assert task.state == "READY" and task.attempt == 0
    types = [t for t, _ in await _events(db_session, task.id)]
    assert types == ["TASK_CREATED", "TASK_READY"]


async def test_pending_task_marks_idle_agents_of_its_role_waiting(db_session, world):
    research = await _add(world, db_session)
    analysis = await _add(world, db_session, "analysis", "analyst", depends_on=[research.id])
    assert analysis.state == "PENDING"
    assert [t for t, _ in await _events(db_session, analysis.id, "TASK_")] == ["TASK_CREATED"]

    analyst = await _activity(db_session, world["agents"]["analyst"])
    assert analyst.state == ActivityState.WAITING
    assert analyst.detail["reason"] == "upstream"
    assert analyst.detail["blocked_task_id"] == str(analysis.id)
    assert analyst.detail["waiting_on_roles"] == ["researcher"]
    researcher = await _activity(db_session, world["agents"]["researcher"])
    assert researcher.state == ActivityState.IDLE


async def test_mark_ready_then_claim_resolves_the_wait(db_session, world):
    research = await _add(world, db_session)
    analysis = await _add(world, db_session, "analysis", "analyst", depends_on=[research.id])
    await world["tm"].mark_ready(db_session, analysis)
    claim = await world["tm"].claim_next(db_session, world["agents"]["analyst"], "w1")
    assert claim.task.id == analysis.id


# --- claiming ----------------------------------------------------------------------------


async def test_claim_creates_run_lease_and_event(db_session, world):
    task = await _add(world, db_session)
    claim = await world["tm"].claim_next(db_session, world["agents"]["researcher"], "worker-1")

    assert claim.task.state == "RUNNING" and claim.task.attempt == 1
    assert claim.task.lease_owner == "worker-1"
    assert claim.task.lease_until == T0 + timedelta(minutes=5)
    assert claim.task.lease_token == claim.token
    assert claim.run.attempt == 1 and claim.run.state == "CREATED"
    assert claim.run.agent_id == world["agents"]["researcher"].id
    assert not claim.resumed

    [(kind, payload)] = await _events(db_session, task.id, "TASK_STARTED")
    assert payload == {
        "run_id": str(claim.run.id),
        "agent_id": str(claim.run.agent_id),
        "attempt": 1,
    }


async def test_claim_order_and_scoping(db_session, world):
    late = await _add(world, db_session, "late", priority=200)
    early = await _add(world, db_session, "early", priority=50)
    other_role = await _add(world, db_session, "analysis", "analyst")
    not_yet = await _add(world, db_session, "later")
    not_yet.available_at = T0 + timedelta(minutes=1)
    await db_session.flush()
    _, _, other_company = await _world(db_session, roles=("researcher",))

    tm, researcher = world["tm"], world["agents"]["researcher"]
    first = await tm.claim_next(db_session, researcher, "w1")
    assert first.task.id == early.id
    with pytest.raises(AgentBusy):
        await tm.claim_next(db_session, researcher, "w1")
    await tm.succeed(db_session, first, {"ok": True})

    second = await tm.claim_next(db_session, researcher, "w1")
    assert second.task.id == late.id
    await tm.succeed(db_session, second, None)

    assert await tm.claim_next(db_session, researcher, "w1") is None, "not_yet is backed off"
    world["clock"].now += timedelta(minutes=1)
    assert (await tm.claim_next(db_session, researcher, "w1")).task.id == not_yet.id

    stranger = await tm.claim_next(db_session, other_company["researcher"], "w9")
    assert stranger is None, "queues are company-scoped"
    assert (
        await tm.claim_next(db_session, world["agents"]["analyst"], "w2")
    ).task.id == other_role.id


# --- succeed / fail / abort --------------------------------------------------------------


async def test_succeed_finishes_run_and_hands_off(db_session, world):
    unlocked_id = uuid.uuid4()

    async def on_finished(session, task):
        return [ev.UnlockedTask(task_id=unlocked_id, required_role="analyst")]

    world["tm"].on_task_finished = on_finished
    task = await _add(world, db_session)
    claim = await world["tm"].claim_next(db_session, world["agents"]["researcher"], "w1")
    claim.run.started_at = T0
    world["clock"].now = T0 + timedelta(seconds=42)

    unlocked = await world["tm"].succeed(
        db_session, claim, {"sources": 3}, output_summary="3 sources"
    )

    assert [u.required_role for u in unlocked] == ["analyst"]
    task = await db_session.get(Task, task.id, populate_existing=True)
    run = await db_session.get(AgentRun, claim.run.id, populate_existing=True)
    assert task.state == "SUCCEEDED" and task.output == {"sources": 3}
    assert task.lease_owner is None and task.lease_token is None
    assert run.state == "COMPLETED" and run.finished_at == world["clock"].now
    assert run.handoff == [{"to_role": "analyst", "task_id": str(unlocked_id)}]

    [(_, succeeded)] = await _events(db_session, task.id, "TASK_SUCCEEDED")
    assert succeeded["unlocks"] == [{"task_id": str(unlocked_id), "required_role": "analyst"}]
    activity = await _activity(db_session, world["agents"]["researcher"])
    assert activity.state == ActivityState.COMPLETED
    assert activity.detail["handoff"] == [{"to_role": "analyst", "task_id": str(unlocked_id)}]
    assert activity.detail["duration_ms"] == 42_000


async def test_retryable_failure_requeues_with_backoff(db_session, world):
    tm, researcher = world["tm"], world["agents"]["researcher"]
    task = await _add(world, db_session, max_attempts=3)
    claim = await tm.claim_next(db_session, researcher, "w1")

    assert await tm.fail(
        db_session, claim, error_class="ToolError", message="timeout", retryable=True
    )
    task = await db_session.get(Task, task.id, populate_existing=True)
    assert task.state == "READY" and task.attempt == 1
    assert task.available_at == T0 + timedelta(seconds=10)
    run = await db_session.get(AgentRun, claim.run.id, populate_existing=True)
    assert run.state == "FAILED" and run.error["error_class"] == "ToolError"
    assert (await _activity(db_session, researcher)).state == ActivityState.IDLE

    types = [t for t, _ in await _events(db_session, task.id)][-4:]
    assert types == ["TASK_FAILED", "TASK_READY", "AGENT_RUN_FAILED", "AGENT_IDLE"]
    failed = [p for t, p in await _events(db_session, task.id, "AGENT_RUN_FAILED")][0]
    assert failed["final"] is False and failed["attempt"] == 1

    assert await tm.claim_next(db_session, researcher, "w1") is None
    world["clock"].now += timedelta(seconds=10)
    second = await tm.claim_next(db_session, researcher, "w1")
    assert second.task.attempt == 2 and second.run.id != claim.run.id
    world["clock"].now = T0
    assert await tm.fail(
        db_session, second, error_class="ToolError", message="again", retryable=True
    )
    assert (
        await db_session.get(Task, task.id, populate_existing=True)
    ).available_at == T0 + timedelta(seconds=20)


async def test_exhausted_attempts_or_non_retryable_is_final(db_session, world):
    tm, researcher = world["tm"], world["agents"]["researcher"]
    failed_tasks = []

    async def on_failed(session, task):
        failed_tasks.append(task.id)

    tm.on_task_failed = on_failed
    task = await _add(world, db_session, max_attempts=1)
    claim = await tm.claim_next(db_session, researcher, "w1")
    assert not await tm.fail(db_session, claim, error_class="Bad", message="m", retryable=True)
    assert (await db_session.get(Task, task.id, populate_existing=True)).state == "FAILED"
    assert (await _activity(db_session, researcher)).state == ActivityState.FAILED
    assert failed_tasks == [task.id]
    assert await tm.claim_next(db_session, researcher, "w1") is None


async def test_budget_abort_blocks_until_released(db_session, world):
    tm, researcher = world["tm"], world["agents"]["researcher"]
    task = await _add(world, db_session, max_attempts=2)
    claim = await tm.claim_next(db_session, researcher, "w1")
    await tm.abort(db_session, claim, reason="budget", message="daily cap")

    task = await db_session.get(Task, task.id, populate_existing=True)
    assert task.state == "BLOCKED_BUDGET" and task.attempt == 1
    assert (await db_session.get(AgentRun, claim.run.id, populate_existing=True)).state == "ABORTED"
    activity = await _activity(db_session, researcher)
    assert activity.state == ActivityState.WAITING and activity.detail["reason"] == "budget"
    assert [t for t, _ in await _events(db_session, task.id, "TASK_BLOCKED")] == ["TASK_BLOCKED"]
    assert await tm.claim_next(db_session, researcher, "w1") is None

    assert await tm.release_blocked(db_session, world["company"].id) == 1
    again = await tm.claim_next(db_session, researcher, "w1")
    assert again.task.id == task.id and again.task.attempt == 2
    assert again.run.id != claim.run.id

    # Out of attempts: releasing fails the task for good instead of parking it in READY.
    await tm.abort(db_session, again, reason="budget")
    assert await tm.release_blocked(db_session, world["company"].id) == 1
    task = await db_session.get(Task, task.id, populate_existing=True)
    assert task.state == "FAILED"
    [(_, failed)] = [
        (t, p) for t, p in await _events(db_session, task.id, "TASK_FAILED") if p["final"]
    ]
    assert failed["error_class"] == "BudgetExhausted"


async def test_policy_abort_fails_for_good(db_session, world):
    tm, researcher = world["tm"], world["agents"]["researcher"]
    task = await _add(world, db_session)
    claim = await tm.claim_next(db_session, researcher, "w1")
    await tm.abort(db_session, claim, reason="policy", message="tool denied")
    assert (await db_session.get(Task, task.id, populate_existing=True)).state == "FAILED"
    assert (await _activity(db_session, researcher)).state == ActivityState.FAILED


# --- lease loss and reaping --------------------------------------------------------------


async def test_reaper_retries_expired_lease_and_old_claim_is_rejected(db_session, world):
    tm, researcher = world["tm"], world["agents"]["researcher"]
    task = await _add(world, db_session, max_attempts=3)
    claim = await tm.claim_next(db_session, researcher, "crashed-worker")

    world["clock"].now = T0 + timedelta(minutes=4)
    assert await tm.reap_expired_leases(db_session) == []
    await tm.heartbeat(db_session, claim)
    world["clock"].now = T0 + timedelta(minutes=8)
    assert await tm.reap_expired_leases(db_session) == [], "heartbeat extended the lease"

    world["clock"].now = T0 + timedelta(minutes=15)
    assert await tm.reap_expired_leases(db_session) == [task.id]
    task = await db_session.get(Task, task.id, populate_existing=True)
    assert task.state == "READY" and task.attempt == 1 and task.lease_token is None
    run = await db_session.get(AgentRun, claim.run.id, populate_existing=True)
    assert run.state == "ABORTED" and run.error["error_class"] == "Aborted:timeout"
    assert (await _activity(db_session, researcher)).state == ActivityState.IDLE
    aborted = [p for t, p in await _events(db_session, task.id, "AGENT_RUN_ABORTED")]
    assert aborted[0]["reason"] == "timeout"

    for op in (
        lambda: tm.succeed(db_session, claim, {}),
        lambda: tm.fail(db_session, claim, error_class="x", message="", retryable=True),
        lambda: tm.heartbeat(db_session, claim),
    ):
        with pytest.raises(LeaseLost):
            await op()
    assert (await db_session.get(Task, task.id, populate_existing=True)).state == "READY"


async def test_reaper_fails_task_when_attempts_exhausted(db_session, world):
    tm, researcher = world["tm"], world["agents"]["researcher"]
    task = await _add(world, db_session, max_attempts=1)
    await tm.claim_next(db_session, researcher, "w1")
    world["clock"].now = T0 + timedelta(hours=1)
    assert await tm.reap_expired_leases(db_session) == [task.id]
    assert (await db_session.get(Task, task.id, populate_existing=True)).state == "FAILED"
    assert (await _activity(db_session, researcher)).state == ActivityState.FAILED


async def test_cancel_running_and_pending_tasks(db_session, world):
    tm = world["tm"]
    research = await _add(world, db_session)
    analysis = await _add(world, db_session, "analysis", "analyst", depends_on=[research.id])
    claim = await tm.claim_next(db_session, world["agents"]["researcher"], "w1")

    await tm.cancel(db_session, research, reason="operator")
    assert (await db_session.get(Task, research.id, populate_existing=True)).state == "CANCELLED"
    assert (await db_session.get(AgentRun, claim.run.id, populate_existing=True)).state == "ABORTED"
    assert (await _activity(db_session, world["agents"]["researcher"])).state == ActivityState.IDLE

    await tm.cancel(db_session, analysis, reason="upstream_cancelled")
    assert (await db_session.get(Task, analysis.id, populate_existing=True)).state == "CANCELLED"
    analyst = await _activity(db_session, world["agents"]["analyst"])
    assert analyst.state == ActivityState.IDLE and analyst.detail["reason"] == "waiting_cleared"
    await tm.cancel(db_session, analysis, reason="again")  # terminal: no-op


# --- acceptance: many workers, no double execution ----------------------------------------


async def test_four_workers_claim_1000_tasks_exactly_once(committed):
    total, workers = 1000, 4
    async with committed() as session:
        company = await unique_company(session, "race")
        project = Project(company_id=company.id, name="p")
        session.add(project)
        await session.flush()
        agents = []
        for i in range(workers):
            agent = Agent(company_id=company.id, role="researcher", display_name=f"R{i}")
            session.add(agent)
            await session.flush()
            await initialize_activity(session, agent, actor=SETUP)
            agents.append(agent)
        now = datetime.now(UTC)
        session.add_all(
            Task(
                id=uuid.uuid4(),
                company_id=company.id,
                project_id=project.id,
                name="research",
                display_name=f"task {i}",
                required_role="researcher",
                state="READY",
                created_at=now + timedelta(microseconds=i),
            )
            for i in range(total)
        )
        await session.commit()

    tm = TaskManager()
    claimed: dict[uuid.UUID, list[str]] = {}

    async def worker(agent, name):
        while True:
            async with committed() as session:
                claim = await tm.claim_next(session, agent, name)
                if claim is None:
                    await session.rollback()
                    return
                claimed.setdefault(claim.task.id, []).append(name)
                await tm.succeed(session, claim, {"by": name})
                await session.commit()

    await asyncio.gather(*(worker(agent, f"w{i}") for i, agent in enumerate(agents)))

    assert len(claimed) == total
    assert all(len(owners) == 1 for owners in claimed.values()), "a task was claimed twice"
    async with committed() as session:
        states = dict(
            (
                await session.execute(
                    select(Task.state, func.count())
                    .where(Task.company_id == company.id)
                    .group_by(Task.state)
                )
            ).all()
        )
        runs = await session.scalar(
            select(func.count()).select_from(AgentRun).where(AgentRun.company_id == company.id)
        )
    assert states == {"SUCCEEDED": total}
    assert runs == total
