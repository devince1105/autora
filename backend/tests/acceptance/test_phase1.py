"""Phase 1 acceptance (logs/3d-office/09_DEVELOPMENT_ROADMAP.md, Phase 1):

Walk one agent through all 8 activity states. Every change must produce exactly one AGENT_*
event with increasing seq, and the activity row must match the last event.

When ``AUTORA_EVENT_FIXTURE_OUT`` is set, the emitted envelopes are written there as JSON so
the TypeScript side can prove it parses real events (``make phase1-acceptance``).
"""

import json
import os
import uuid
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from autora.db.models import ActivityState, Agent, Company, EventRecord
from autora.runtime.activity import get_activity, initialize_activity, set_activity
from autora.runtime.actor import Actor
from autora.runtime.events import catalog as ev
from autora.runtime.events.outbox import load_events


async def test_agent_walks_all_eight_states(db_session):
    company = Company(slug=f"phase1-{uuid.uuid4().hex[:8]}", name="AI Newsroom")
    db_session.add(company)
    await db_session.flush()
    agent = Agent(company_id=company.id, role="researcher", display_name="Researcher")
    db_session.add(agent)
    await db_session.flush()

    runner = Actor.system("agent_runner")
    run_id, task_id, approval_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    in_run = {"actor": runner, "run_id": run_id, "task_id": task_id, "task_name": "Find stories"}

    await initialize_activity(db_session, agent, actor=Actor.system("setup"))
    S = ActivityState
    operator = {"actor": Actor.human("operator")}
    completed = ev.AgentRunCompleted(
        output_summary="37 sources",
        cost_usd=Decimal("0.184"),
        steps=19,
        duration_ms=217_000,
        handoff=[ev.Handoff(to_role="analyst", task_id=uuid.uuid4())],
    )
    working = ev.AgentWorking(
        tool="web_search",
        tool_call_id="call_1",
        step_seq=1,
        progress=ev.Progress(label="sources", current=12, target=40),
    )
    steps = [
        (ev.AgentThinking(phase="plan", step_seq=0), in_run, S.THINKING),
        (working, in_run, S.WORKING),
        (ev.AgentWaiting(reason="approval", approval_id=approval_id), in_run, S.WAITING),
        (ev.AgentReviewing(phase="evaluate", attempt=1, issues_count=0), in_run, S.REVIEWING),
        (completed, in_run, S.COMPLETED),
        (ev.AgentRunFailed(error_class="E", message="t", attempt=3, final=True), in_run, S.FAILED),
        (ev.AgentIdle(reason="failure_acknowledged"), operator, S.IDLE),
        (ev.AgentPaused(reason="maintenance"), operator, S.PAUSED),
        (ev.AgentResumed(), operator, S.IDLE),
    ]  # fmt: skip

    visited = {ActivityState.IDLE}
    for payload, context, expected_state in steps:
        activity, event = await set_activity(db_session, agent, payload, **context)
        assert activity.state == expected_state
        assert activity.last_event_seq == event.seq
        visited.add(expected_state)

    assert visited == set(ActivityState), "all 8 activity states must be exercised"

    events = await load_events(db_session, company.id)
    assert len(events) == len(steps) + 1
    assert all(e.event_type.startswith("AGENT_") for e in events)
    seqs = [e.seq for e in events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)

    final = await get_activity(db_session, agent.id)
    last_row = await db_session.scalar(
        select(EventRecord).where(EventRecord.agent_id == agent.id).order_by(EventRecord.seq.desc())
    )
    assert final.state == ActivityState.IDLE
    assert final.last_event_seq == last_row.seq == events[-1].seq

    out = os.environ.get("AUTORA_EVENT_FIXTURE_OUT")
    if out:
        Path(out).write_text(
            json.dumps([json.loads(e.model_dump_json()) for e in events], indent=2) + "\n"
        )
