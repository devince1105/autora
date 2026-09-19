"""Agent activity: "what this agent is doing right now" (logs/3d-office/02_AGENT_STATE_MODEL.md).

The state is derived from the event payload, never passed separately, so a row and its event
cannot disagree:

    AGENT_IDLE / AGENT_RESUMED   -> IDLE        AGENT_REVIEWING      -> REVIEWING
    AGENT_THINKING               -> THINKING    AGENT_RUN_COMPLETED  -> COMPLETED
    AGENT_WORKING                -> WORKING     AGENT_RUN_FAILED*    -> FAILED
    AGENT_WAITING                -> WAITING     AGENT_RUN_ABORTED    -> FAILED
    AGENT_PAUSED                 -> PAUSED      (* only when final)

Activity is a presentation-grade projection of the Task and AgentRun FSMs, which enforce the
real lifecycle rules. Only transitions that would lose information are rejected here:
leaving PAUSED other than by resume, acknowledging a failure that did not happen, clearing a
wait that is not in progress.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from autora.db.models import ActivityState, Agent, AgentActivity
from autora.runtime.actor import Actor
from autora.runtime.events import catalog as ev
from autora.runtime.events.outbox import emit
from autora.runtime.events.schema import EventEnvelope, EventPayload, new_event

COMPLETED_DISPLAY = timedelta(seconds=20)
"""How long COMPLETED is shown before the projection reports IDLE (02 §5)."""

_STATE_BY_PAYLOAD: dict[type[EventPayload], ActivityState] = {
    ev.AgentIdle: ActivityState.IDLE,
    ev.AgentResumed: ActivityState.IDLE,
    ev.AgentThinking: ActivityState.THINKING,
    ev.AgentWorking: ActivityState.WORKING,
    ev.AgentWaiting: ActivityState.WAITING,
    ev.AgentReviewing: ActivityState.REVIEWING,
    ev.AgentRunCompleted: ActivityState.COMPLETED,
    ev.AgentRunFailed: ActivityState.FAILED,
    ev.AgentRunAborted: ActivityState.FAILED,
    ev.AgentPaused: ActivityState.PAUSED,
}


class ActivityError(Exception):
    pass


async def get_activity(session: AsyncSession, agent_id: uuid.UUID) -> AgentActivity | None:
    return await session.get(AgentActivity, agent_id)


def effective_state(activity: AgentActivity, now: datetime | None = None) -> ActivityState:
    """Projection rule shared by the snapshot API and the frontend reducer:
    COMPLETED past its ``display_until`` reads as IDLE (no event is written for that)."""
    state = ActivityState(activity.state)
    if state is ActivityState.COMPLETED:
        display_until = activity.detail.get("display_until")
        if display_until is not None:
            now = now or datetime.now(UTC)
            if datetime.fromisoformat(display_until) <= now:
                return ActivityState.IDLE
    return state


async def initialize_activity(
    session: AsyncSession, agent: Agent, *, actor: Actor
) -> tuple[AgentActivity, EventEnvelope]:
    """Create the activity row for a new agent (IDLE) and emit AGENT_IDLE{initialized}."""
    if await get_activity(session, agent.id) is not None:
        raise ActivityError(f"activity for agent {agent.id} already initialized")
    return await _write(session, agent, ev.AgentIdle(reason="initialized"), actor=actor)


async def set_activity(
    session: AsyncSession,
    agent: Agent,
    payload: EventPayload,
    *,
    actor: Actor,
    run_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
    workflow_run_id: uuid.UUID | None = None,
    cycle_id: uuid.UUID | None = None,
    correlation_id: uuid.UUID | None = None,
    causation_id: uuid.UUID | None = None,
    task_name: str | None = None,
    links: Sequence[dict[str, str]] = (),
) -> tuple[AgentActivity, EventEnvelope]:
    """Move ``agent`` to the state implied by ``payload``; emit the event in the same TX."""
    current = await session.scalar(
        select(AgentActivity).where(AgentActivity.agent_id == agent.id).with_for_update()
    )
    if current is None:
        raise ActivityError(f"activity for agent {agent.id} is not initialized")
    _check(ActivityState(current.state), payload)
    return await _write(
        session,
        agent,
        payload,
        actor=actor,
        current=current,
        run_id=run_id,
        task_id=task_id,
        workflow_run_id=workflow_run_id,
        cycle_id=cycle_id,
        correlation_id=correlation_id,
        causation_id=causation_id,
        task_name=task_name,
        links=links,
    )


async def set_activity_unless_paused(
    session: AsyncSession, agent: Agent, payload: EventPayload, **kwargs: Any
) -> tuple[AgentActivity, EventEnvelope] | None:
    """Like ``set_activity``, but a PAUSED agent stays PAUSED: nothing is written, no event.

    For the task manager, which must be able to finish, fail, cancel or reclaim a run whose
    agent an operator paused meanwhile. The task and run still move on; the agent shows PAUSED
    until resumed (and the worker does not give it new work).
    """
    current = await session.scalar(
        select(AgentActivity.state).where(AgentActivity.agent_id == agent.id).with_for_update()
    )
    if current == ActivityState.PAUSED.value:
        return None
    return await set_activity(session, agent, payload, **kwargs)


def _check(current: ActivityState, payload: EventPayload) -> None:
    kind = type(payload)
    if kind not in _STATE_BY_PAYLOAD:
        raise ActivityError(f"{kind.__name__} does not change agent activity")
    if isinstance(payload, ev.AgentRunFailed | ev.AgentRunAborted) and not payload.final:
        raise ActivityError("a non-final run end is trace data; it does not change activity")
    if isinstance(payload, ev.AgentPaused) and current is ActivityState.PAUSED:
        raise ActivityError("agent is already PAUSED")
    if current is ActivityState.PAUSED and not isinstance(payload, ev.AgentResumed):
        raise ActivityError(f"agent is PAUSED; {payload.event_type} is not allowed until resumed")
    if isinstance(payload, ev.AgentResumed) and current is not ActivityState.PAUSED:
        raise ActivityError(f"cannot resume an agent that is {current}")
    if isinstance(payload, ev.AgentIdle):
        required = {
            "failure_acknowledged": ActivityState.FAILED,
            "waiting_cleared": ActivityState.WAITING,
        }.get(payload.reason)
        if payload.reason == "initialized" or (required is not None and current is not required):
            raise ActivityError(f"AGENT_IDLE{{{payload.reason}}} is not valid from {current}")


async def _write(
    session: AsyncSession,
    agent: Agent,
    payload: EventPayload,
    *,
    actor: Actor,
    current: AgentActivity | None = None,
    run_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
    workflow_run_id: uuid.UUID | None = None,
    cycle_id: uuid.UUID | None = None,
    correlation_id: uuid.UUID | None = None,
    causation_id: uuid.UUID | None = None,
    task_name: str | None = None,
    links: Sequence[dict[str, str]] = (),
) -> tuple[AgentActivity, EventEnvelope]:
    state = _STATE_BY_PAYLOAD[type(payload)]
    now = datetime.now(UTC)

    if isinstance(payload, ev.AgentRunCompleted) and payload.display_until is None:
        payload = payload.model_copy(update={"display_until": now + COMPLETED_DISPLAY})
    if links and "links" in type(payload).model_fields:
        # in the event too: the office's store builds the agent's detail from the events
        payload = payload.model_copy(update={"links": [ev.Link(**link) for link in links]})

    envelope = await emit(
        session,
        new_event(
            payload,
            company_id=agent.company_id,
            actor=actor,
            aggregate_type="agent_run" if run_id else "agent",
            aggregate_id=run_id or agent.id,
            agent_id=agent.id,
            task_id=task_id,
            run_id=run_id,
            workflow_run_id=workflow_run_id,
            cycle_id=cycle_id,
            correlation_id=correlation_id,
            causation_id=causation_id,
            occurred_at=now,
        ),
    )

    # A state without a run (IDLE, PAUSED) clears run/task references.
    runless = state in (ActivityState.IDLE, ActivityState.PAUSED)
    detail: dict[str, Any] = payload.model_dump(mode="json")
    context = {
        "run_id": None if runless else run_id,
        "task_id": None if runless else task_id,
        "workflow_run_id": None if runless else workflow_run_id,
        "task_name": None if runless else task_name,
    }
    detail.update({k: str(v) if isinstance(v, uuid.UUID) else v for k, v in context.items() if v})
    if links and not runless:
        detail["links"] = list(links)

    since = current.since if current is not None and current.state == state else now
    values = {
        "company_id": agent.company_id,
        "state": state.value,
        "detail": detail,
        "run_id": context["run_id"],
        "task_id": context["task_id"],
        "since": since,
        "last_event_seq": envelope.seq,
        "updated_at": now,
    }
    await session.execute(
        insert(AgentActivity)
        .values(agent_id=agent.id, **values)
        .on_conflict_do_update(index_elements=[AgentActivity.agent_id], set_=values)
    )
    activity = await session.get(AgentActivity, agent.id, populate_existing=True)
    assert activity is not None
    return activity, envelope
