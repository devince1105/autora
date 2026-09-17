"""T-210: trace recording and the run trace query."""

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from autora.db.models import StepKind
from autora.infra.blobstore import LocalFSBlobStore
from autora.runtime.actor import Actor
from autora.runtime.events import catalog as ev
from autora.runtime.events import new_event
from autora.runtime.events.outbox import emit
from autora.runtime.trace import get_run_trace, record_step, step_blob_key
from autora.runtime.trace.recorder import TraceError
from tests.conftest import running_agent_run


@pytest.fixture
def blobs(tmp_path):
    return LocalFSBlobStore(tmp_path / "blobs")


@pytest.fixture
async def run(db_session):
    return await running_agent_run(db_session, "trace")


async def _emit(session, run, payload):
    return await emit(
        session,
        new_event(
            payload,
            company_id=run.company_id,
            actor=Actor.agent(run.agent_id),
            aggregate_type="agent_run",
            aggregate_id=run.id,
            agent_id=run.agent_id,
            run_id=run.id,
            task_id=run.task_id,
        ),
    )


async def test_steps_get_consecutive_seq_and_payload_goes_to_blob(db_session, blobs, run):
    think = await record_step(
        db_session,
        blobs,
        run.id,
        kind=StepKind.THINK,
        summary="plan: search then fetch",
        prompt_hash="sha256:abc",
        cost_usd=Decimal("0.012"),
        payload={"messages": [{"role": "user", "content": "Find today's AI stories"}]},
    )
    act = await record_step(
        db_session,
        blobs,
        run.id,
        kind=StepKind.ACT,
        tool_calls=[{"tool": "web_search", "tool_call_id": "c1"}],
    )

    assert (think.seq, act.seq) == (0, 1)
    await db_session.refresh(run)
    assert run.steps_count == 2
    assert think.blob_key == step_blob_key(run.company_id, run.id, 0)
    assert act.blob_key is None
    stored = json.loads(await blobs.get(think.blob_key))
    assert stored["messages"][0]["content"] == "Find today's AI stories"


async def test_cannot_record_on_finished_or_missing_run(db_session, blobs, run):
    run.state = "COMPLETED"
    run.finished_at = datetime.now(UTC)
    await db_session.flush()
    with pytest.raises(TraceError, match="closed"):
        await record_step(db_session, blobs, run.id, kind=StepKind.THINK)
    with pytest.raises(TraceError, match="does not exist"):
        await record_step(db_session, blobs, uuid.uuid4(), kind=StepKind.THINK)


async def test_trace_joins_real_events_with_steps(db_session, blobs, run):
    await _emit(
        db_session,
        run,
        ev.AgentRunStarted(attempt=1, task_name="research", required_role="researcher"),
    )
    await record_step(db_session, blobs, run.id, kind=StepKind.THINK, summary="plan")
    await _emit(db_session, run, ev.AgentThinking(phase="plan", step_seq=0))
    await record_step(db_session, blobs, run.id, kind=StepKind.ACT, summary="search")
    await _emit(
        db_session,
        run,
        ev.ToolCalled(tool="web_search", tool_call_id="c1", side_effect="read", step_seq=1),
    )
    await _emit(
        db_session, run, ev.ToolCompleted(tool="web_search", tool_call_id="c1", duration_ms=900)
    )
    await record_step(
        db_session, blobs, run.id, kind=StepKind.OBSERVE, summary="no event refers to me"
    )
    # an event of another run must not leak into this trace
    other_run_id = uuid.uuid4()
    await emit(db_session, new_event(
        ev.AgentThinking(phase="plan", step_seq=0), company_id=run.company_id,
        actor=Actor.system("x"), aggregate_type="agent_run", aggregate_id=other_run_id,
        run_id=other_run_id,
    ))  # fmt: skip

    trace = await get_run_trace(db_session, run.id)

    assert [e.event_type for e in trace.entries] == [
        "AGENT_RUN_STARTED",
        "AGENT_THINKING",
        "TOOL_CALLED",
        "TOOL_COMPLETED",
    ]
    assert [e.seq for e in trace.entries] == sorted(e.seq for e in trace.entries)
    started, thinking, called, completed = trace.entries
    assert started.step is None
    assert thinking.step.kind == "think" and thinking.step.summary == "plan"
    assert called.step.seq == 1 and called.step.kind == "act"
    assert completed.step is None  # TOOL_COMPLETED carries no step_seq
    assert [s.seq for s in trace.steps] == [0, 1, 2], "steps without events are still listed"
    assert trace.state == "RUNNING" and trace.attempt == 1


async def test_unknown_run_has_no_trace(db_session):
    assert await get_run_trace(db_session, uuid.uuid4()) is None
