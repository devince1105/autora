"""T-107: agent activity service."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from autora.db.models import ActivityState, Agent, Company, EventRecord
from autora.runtime.activity import (
    COMPLETED_DISPLAY,
    ActivityError,
    effective_state,
    get_activity,
    initialize_activity,
    set_activity,
)
from autora.runtime.actor import Actor
from autora.runtime.events import catalog as ev

RUNTIME = Actor.system("agent_runner")


async def _agent(session, role="researcher") -> Agent:
    company = Company(slug=f"act-{uuid.uuid4().hex[:12]}", name="Activity Co", type="newsroom")
    session.add(company)
    await session.flush()
    agent = Agent(company_id=company.id, role=role, display_name=role.title())
    session.add(agent)
    await session.flush()
    await initialize_activity(session, agent, actor=Actor.system("setup"))
    return agent


async def test_initialize_creates_idle_row_and_event(db_session):
    agent = await _agent(db_session)
    activity = await get_activity(db_session, agent.id)
    assert activity.state == ActivityState.IDLE
    event = await db_session.scalar(select(EventRecord).where(EventRecord.agent_id == agent.id))
    assert event.event_type == "AGENT_IDLE"
    assert event.payload == {"reason": "initialized"}
    assert activity.last_event_seq == event.seq

    with pytest.raises(ActivityError, match="already initialized"):
        await initialize_activity(db_session, agent, actor=RUNTIME)


async def test_row_and_event_written_together_with_context(db_session):
    agent = await _agent(db_session)
    run_id, task_id = uuid.uuid4(), uuid.uuid4()

    activity, event = await set_activity(
        db_session,
        agent,
        ev.AgentWorking(
            tool="web_search",
            tool_call_id="c1",
            step_seq=2,
            progress=ev.Progress(label="sources", current=12, target=40),
        ),
        actor=Actor.agent(agent.id),
        run_id=run_id,
        task_id=task_id,
        task_name="Find today's AI stories",
        links=[{"label": "Story", "href": "/newsroom/stories/x"}],
    )

    assert activity.state == ActivityState.WORKING
    assert activity.last_event_seq == event.seq
    assert activity.run_id == run_id and activity.task_id == task_id
    assert activity.detail["tool"] == "web_search"
    assert activity.detail["progress"] == {"label": "sources", "current": 12, "target": 40}
    assert activity.detail["task_name"] == "Find today's AI stories"
    assert activity.detail["links"] == [{"label": "Story", "href": "/newsroom/stories/x"}]
    assert event.event_type == "AGENT_WORKING"
    assert (event.agent_id, event.run_id, event.task_id) == (agent.id, run_id, task_id)
    assert event.aggregate_type == "agent_run"


async def test_since_kept_within_state_and_reset_on_change(db_session):
    agent = await _agent(db_session)
    run_id = uuid.uuid4()
    first, _ = await set_activity(
        db_session,
        agent,
        ev.AgentWorking(tool="web_search", tool_call_id="1", step_seq=1),
        actor=RUNTIME,
        run_id=run_id,
    )
    since = first.since
    again, _ = await set_activity(
        db_session,
        agent,
        ev.AgentWorking(tool="fetch_url", tool_call_id="2", step_seq=2),
        actor=RUNTIME,
        run_id=run_id,
    )
    assert again.since == since and again.detail["tool"] == "fetch_url"
    changed, _ = await set_activity(
        db_session, agent, ev.AgentReviewing(phase="evaluate", attempt=1, issues_count=0),
        actor=RUNTIME, run_id=run_id,
    )  # fmt: skip
    assert changed.since > since


async def test_runless_states_clear_run_references(db_session):
    agent = await _agent(db_session)
    await set_activity(
        db_session, agent, ev.AgentThinking(phase="plan", step_seq=0),
        actor=RUNTIME, run_id=uuid.uuid4(), task_id=uuid.uuid4(), task_name="t",
    )  # fmt: skip
    paused, _ = await set_activity(
        db_session, agent, ev.AgentPaused(reason="operator"), actor=Actor.human("op")
    )
    assert paused.run_id is None and paused.task_id is None
    assert "task_name" not in paused.detail


async def test_completed_gets_display_until_and_projects_to_idle_later(db_session):
    agent = await _agent(db_session)
    activity, event = await set_activity(
        db_session,
        agent,
        ev.AgentRunCompleted(cost_usd=Decimal("0.18"), steps=19, duration_ms=1000),
        actor=RUNTIME,
        run_id=uuid.uuid4(),
    )
    display_until = datetime.fromisoformat(activity.detail["display_until"])
    assert event.payload.display_until == display_until
    assert timedelta(0) < display_until - datetime.now(UTC) <= COMPLETED_DISPLAY

    assert effective_state(activity) == ActivityState.COMPLETED
    later = display_until + timedelta(milliseconds=1)
    assert effective_state(activity, now=later) == ActivityState.IDLE


@pytest.mark.parametrize(
    ("setup", "payload", "message"),
    [
        ([], ev.ToolCalled(tool="x", tool_call_id="1", side_effect="read", step_seq=0),
         "does not change agent activity"),
        ([], ev.AgentRunFailed(error_class="E", message="m", attempt=1, final=False),
         "non-final"),
        ([], ev.AgentResumed(), "cannot resume"),
        ([], ev.AgentIdle(reason="failure_acknowledged"), "not valid from IDLE"),
        ([], ev.AgentIdle(reason="initialized"), "not valid"),
        ([ev.AgentPaused()], ev.AgentThinking(phase="plan", step_seq=0), "PAUSED"),
        ([ev.AgentPaused()], ev.AgentPaused(), "already PAUSED"),
    ],
)  # fmt: skip
async def test_rejected_changes_write_nothing(db_session, setup, payload, message):
    agent = await _agent(db_session)
    for step in setup:
        await set_activity(db_session, agent, step, actor=RUNTIME)
    before = await db_session.scalar(
        select(func.count()).select_from(EventRecord).where(EventRecord.agent_id == agent.id)
    )

    with pytest.raises(ActivityError, match=message):
        await set_activity(db_session, agent, payload, actor=RUNTIME)

    after = await db_session.scalar(
        select(func.count()).select_from(EventRecord).where(EventRecord.agent_id == agent.id)
    )
    assert after == before


async def test_uninitialized_agent_rejected(db_session):
    company = Company(slug=f"act-{uuid.uuid4().hex[:12]}", name="x", type="newsroom")
    db_session.add(company)
    await db_session.flush()
    agent = Agent(company_id=company.id, role="writer", display_name="Writer")
    db_session.add(agent)
    await db_session.flush()
    with pytest.raises(ActivityError, match="not initialized"):
        await set_activity(
            db_session, agent, ev.AgentThinking(phase="plan", step_seq=0), actor=RUNTIME
        )
