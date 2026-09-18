"""T-301: realtime projection and GET /api/companies/{id}/realtime/snapshot."""

import os
import statistics
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, insert, select

from autora.app import build_worker
from autora.company.events import GoalCreated
from autora.db.models import (
    ActivityState,
    Agent,
    AgentActivity,
    EventRecord,
    Project,
    ProjectState,
    Task,
)
from autora.realtime.projection import (
    RECENT_EVENTS,
    begin_consistent_read,
    load_snapshot,
)
from autora.runtime.actor import Actor
from autora.runtime.events import catalog as ev
from autora.runtime.events import new_event
from autora.runtime.events.outbox import emit
from tests.conftest import unique_company
from tests.echo_fixtures import start_echo


async def _snapshot(committed, company_id, now=None):
    async with committed() as session:
        await begin_consistent_read(session)
        return await load_snapshot(session, company_id, now=now)


async def _max_seq(committed, company_id) -> int:
    async with committed() as session:
        return await session.scalar(
            select(func.max(EventRecord.seq)).where(EventRecord.company_id == company_id)
        )


async def _last_task_event(committed, task_id) -> EventRecord:
    async with committed() as session:
        return await session.scalar(
            select(EventRecord)
            .where(EventRecord.task_id == task_id, EventRecord.event_type.like("TASK\\_%"))
            .order_by(EventRecord.seq.desc())
            .limit(1)
        )


# --- what the snapshot contains ----------------------------------------------------------


async def test_snapshot_before_and_after_a_workflow(committed, e2e_settings, echo_company):
    company_id = echo_company.company.id
    agents = echo_company.agents
    wf, tasks = await start_echo(committed, echo_company)

    before = await _snapshot(committed, company_id)
    assert before.last_seq == await _max_seq(committed, company_id)
    assert [a.role for a in before.agents] == ["researcher", "analyst", "writer"]
    states = {a.role: (a.activity.state, a.activity.detail.get("reason")) for a in before.agents}
    assert states == {
        "researcher": (ActivityState.IDLE, "initialized"),
        "analyst": (ActivityState.WAITING, "upstream"),
        "writer": (ActivityState.WAITING, "upstream"),
    }
    view = {t.name: t for t in before.tasks}
    assert [view[n].state for n in ("echo_research", "echo_analyze", "echo_write")] == [
        "READY", "PENDING", "PENDING",
    ]  # fmt: skip
    assert view["echo_analyze"].depends_on == [tasks["echo_research"].id]
    assert view["echo_research"].workflow_run_id == wf.id
    assert view["echo_research"].run_id is None and view["echo_research"].attempt == 0
    assert before.recent_events[-1].seq == before.last_seq
    assert before.kpis is None and before.cycle is None

    await build_worker(
        e2e_settings, session_factory=committed, company_ids=frozenset({company_id})
    ).run_until_idle()

    after = await _snapshot(committed, company_id)
    assert after.last_seq == await _max_seq(committed, company_id) > before.last_seq
    assert {a.activity.state for a in after.agents} == {ActivityState.COMPLETED}
    done = {t.name: t for t in after.tasks}
    assert {t.state for t in done.values()} == {"SUCCEEDED"}, "finished tasks stay 10 minutes"
    research = done["echo_research"]
    last = await _last_task_event(committed, research.id)
    assert (research.last_event_seq, research.since) == (last.seq, last.occurred_at)
    assert last.event_type == "TASK_SUCCEEDED"
    assert research.attempt == 1 and research.agent_id == agents["researcher"].id
    assert research.run_id is not None
    researcher = next(a for a in after.agents if a.role == "researcher")
    assert researcher.activity.run_id == research.run_id
    assert researcher.activity.detail["handoff"] == [
        {"to_role": "analyst", "task_id": str(tasks["echo_analyze"].id)}
    ]


async def test_projection_rules_follow_the_clock(committed, e2e_settings, echo_company):
    """COMPLETED past display_until reads IDLE; finished tasks leave after 10 minutes."""
    company_id = echo_company.company.id
    await start_echo(committed, echo_company)
    await build_worker(
        e2e_settings, session_factory=committed, company_ids=frozenset({company_id})
    ).run_until_idle()

    now = datetime.now(UTC)
    soon = await _snapshot(committed, company_id, now=now + timedelta(seconds=5))
    later = await _snapshot(committed, company_id, now=now + timedelta(minutes=11))

    assert {a.activity.state for a in soon.agents} == {ActivityState.COMPLETED}
    assert {a.activity.state for a in later.agents} == {ActivityState.IDLE}
    assert {a.activity.stored_state for a in later.agents} == {ActivityState.COMPLETED}
    assert len(soon.tasks) == 3 and later.tasks == []
    assert later.last_seq == soon.last_seq, "the clock changes the view, not the state"


async def test_recent_events_are_the_latest_hundred_oldest_first(committed):
    async with committed() as session:
        company = await unique_company(session, "rt-events")
        for i in range(RECENT_EVENTS + 20):
            await emit(
                session,
                new_event(
                    GoalCreated(title=f"g{i}", level="cycle", metric="m", target=1),
                    company_id=company.id,
                    actor=Actor.system("test"),
                    aggregate_type="goal",
                    aggregate_id=uuid.uuid4(),
                ),
            )
        await session.commit()

    snapshot = await _snapshot(committed, company.id)
    seqs = [e.seq for e in snapshot.recent_events]
    assert len(seqs) == RECENT_EVENTS and seqs == sorted(seqs)
    assert seqs[-1] == snapshot.last_seq
    assert snapshot.recent_events[-1].payload.title == f"g{RECENT_EVENTS + 19}"


async def test_empty_company(committed):
    async with committed() as session:
        company = await unique_company(session, "rt-empty")
        await session.commit()
    snapshot = await _snapshot(committed, company.id)
    assert (snapshot.last_seq, snapshot.agents, snapshot.tasks, snapshot.recent_events) == (
        0, [], [], [],
    )  # fmt: skip


# --- consistency ---------------------------------------------------------------------------


async def test_one_snapshot_sees_one_moment(committed, echo_company):
    """A write committed while the snapshot transaction is open is not half-visible."""
    company_id = echo_company.company.id
    researcher = echo_company.agents["researcher"]
    async with committed() as reader:
        await begin_consistent_read(reader)
        first = await load_snapshot(reader, company_id)

        async with committed() as writer:
            from autora.runtime.activity import set_activity

            agent = await writer.get(Agent, researcher.id)
            await set_activity(
                writer, agent, ev.AgentPaused(reason="test"), actor=Actor.human("operator")
            )
            await writer.commit()

        second = await load_snapshot(reader, company_id)

    assert second.last_seq == first.last_seq
    assert [a.activity for a in second.agents] == [a.activity for a in first.agents]
    fresh = await _snapshot(committed, company_id)
    assert fresh.last_seq > first.last_seq
    assert next(a for a in fresh.agents if a.id == researcher.id).activity.state == "PAUSED"


# --- performance (3d-office/07 §4: < 50 ms locally) ----------------------------------------


@pytest.fixture
async def busy_company(committed):
    """12 agents, 300 tasks (100 unfinished), 20,000 events."""
    async with committed() as session:
        company = await unique_company(session, "rt-perf")
        project = Project(
            company_id=company.id, name="p", state=ProjectState.ACTIVE.value,
            kill_criteria={"max_cost_usd": 1},
        )  # fmt: skip
        session.add(project)
        await session.flush()
        now = datetime.now(UTC)
        agents = [
            {"id": uuid.uuid4(), "company_id": company.id, "role": f"role_{i}",
             "display_name": f"A{i}"}
            for i in range(12)
        ]  # fmt: skip
        await session.execute(insert(Agent), agents)
        await session.execute(
            insert(AgentActivity),
            [
                {"agent_id": a["id"], "company_id": company.id, "state": "IDLE", "detail": {},
                 "since": now, "last_event_seq": 0}
                for a in agents
            ],
        )  # fmt: skip
        tasks = [
            {"id": uuid.uuid4(), "company_id": company.id, "project_id": project.id,
             "name": f"t_{i}", "display_name": f"T{i}", "required_role": "role_0",
             "state": "READY" if i < 100 else "SUCCEEDED"}
            for i in range(300)
        ]  # fmt: skip
        await session.execute(insert(Task), tasks)
        events = []
        for n in range(20_000):
            task = tasks[n % len(tasks)]
            events.append(
                {"id": uuid.uuid4(), "event_type": "TASK_READY", "schema_version": 1,
                 "company_id": company.id,
                 "occurred_at": now - timedelta(hours=1) + timedelta(milliseconds=n),
                 "aggregate_type": "task", "aggregate_id": task["id"], "task_id": task["id"],
                 "actor": {"kind": "system", "id": "perf"},
                 "payload": {"required_role": "role_0"}}
            )  # fmt: skip
        for start in range(0, len(events), 5000):
            await session.execute(insert(EventRecord), events[start : start + 5000])
        await session.commit()
    async with committed() as session:
        await session.execute(select(1).select_from(EventRecord).limit(1))  # warm the pool
    return company


async def test_snapshot_is_fast(committed, busy_company):
    timings = []
    for _ in range(15):
        started = time.perf_counter()
        snapshot = await _snapshot(committed, busy_company.id)
        timings.append((time.perf_counter() - started) * 1000)
    assert len(snapshot.agents) == 12
    assert len(snapshot.tasks) == 100, "finished tasks older than the window are left out"
    assert len(snapshot.recent_events) == RECENT_EVENTS
    median = statistics.median(timings[5:])
    print(f"\nsnapshot median {median:.1f} ms (p0 {min(timings):.1f}, max {max(timings):.1f})")
    # The target is local; shared CI runners are slower and noisier, so they get headroom.
    budget = 150 if os.environ.get("CI") else 50
    assert median < budget, f"snapshot took {median:.1f} ms (target < {budget} ms)"
