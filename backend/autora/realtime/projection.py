"""Realtime projection: the company state a browser hydrates from (T-301, 3d-office/05 §3).

``load_snapshot`` returns agents with their activity, the tasks worth showing, the latest events
and ``last_seq``: the seq of the newest event already reflected in that state. The client then
connects with ``since=last_seq`` and applies every later event with the same rules (T-302 checks
``apply(snapshot_0, events) == snapshot_n``; T-305 mirrors them in TypeScript).

Consistency: every read must see the same moment, or ``last_seq`` would not describe the rows.
The caller runs this in a REPEATABLE READ transaction (``begin_consistent_read``). Events of one
company commit in seq order (per-company lock in the outbox), so the events visible in that
snapshot are exactly those with ``seq <= last_seq``.

Projection rules (shared with the reducers):
- **Agents**: every non-retired agent. ``activity.state`` is the *effective* state: COMPLETED
  past its ``display_until`` reads IDLE (02 §5); ``stored_state`` is what the row says.
- **Tasks**: unfinished ones, plus finished ones whose last TASK_* event is younger than
  ``FINISHED_TASK_WINDOW``. ``since`` / ``last_event_seq`` come from that last TASK_* event, so a
  reducer can compute them from events alone.
- **Recent events**: the last ``RECENT_EVENTS`` persisted events, oldest first.
- **KPIs** arrive with T-314 and the **cycle** with Phase 6; both are ``None`` until then.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel
from sqlalchemy import func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from autora.db.models import (
    ActivityState,
    Agent,
    AgentActivity,
    AgentRun,
    AgentStatus,
    Cycle,
    Department,
    EventRecord,
    Task,
    TaskState,
)
from autora.runtime.activity import effective_state
from autora.runtime.events.outbox import to_envelope
from autora.runtime.events.schema import EventEnvelope

FINISHED_TASK_WINDOW = timedelta(minutes=10)
RECENT_EVENTS = 100
_FINISHED = (TaskState.SUCCEEDED, TaskState.FAILED, TaskState.CANCELLED)


class ActivityView(BaseModel):
    state: ActivityState
    """Effective state (what to show)."""
    stored_state: ActivityState
    detail: dict[str, Any]
    since: datetime
    run_id: uuid.UUID | None
    task_id: uuid.UUID | None
    last_event_seq: int


class AgentView(BaseModel):
    id: uuid.UUID
    role: str
    display_name: str
    avatar_key: str
    department_id: uuid.UUID | None = None
    department_key: str | None = None
    """Which room of the office draws it (T-600). None for an agent with no place on the
    org chart — it works, it is simply not in a department yet."""
    activity: ActivityView


class TaskView(BaseModel):
    id: uuid.UUID
    name: str
    display_name: str
    required_role: str
    state: str
    depends_on: list[uuid.UUID]
    workflow_run_id: uuid.UUID | None
    attempt: int
    run_id: uuid.UUID | None
    """The task's latest agent run (None before the first claim, or for human nodes)."""
    agent_id: uuid.UUID | None
    since: datetime
    """When the task entered its current state (its last TASK_* event)."""
    last_event_seq: int


class CycleView(BaseModel):
    id: uuid.UUID
    seq: int
    stage: str
    deadline: datetime | None = None


class RealtimeSnapshot(BaseModel):
    company_id: uuid.UUID
    last_seq: int
    """Newest event reflected in this state; connect with ``since=last_seq``."""
    server_time: datetime
    agents: list[AgentView]
    tasks: list[TaskView]
    recent_events: list[EventEnvelope]
    kpis: dict[str, Any] | None = None
    cycle: CycleView | None = None
    """The company's latest cycle. Only what the CYCLE_* events carry, because the reducer has
    to be able to rebuild it from them — the plan and the review are read from the API, not
    followed live (T-608)."""


async def begin_consistent_read(session: AsyncSession) -> None:
    """Start the session's transaction as REPEATABLE READ (no-op if one is already open)."""
    if not session.in_transaction():
        await session.connection(execution_options={"isolation_level": "REPEATABLE READ"})


async def load_snapshot(
    session: AsyncSession, company_id: uuid.UUID, *, now: datetime | None = None
) -> RealtimeSnapshot:
    now = now or datetime.now(UTC)
    last_seq = await session.scalar(
        select(func.coalesce(func.max(EventRecord.seq), 0)).where(
            EventRecord.company_id == company_id
        )
    )
    return RealtimeSnapshot(
        company_id=company_id,
        last_seq=last_seq,
        server_time=now,
        agents=await _agents(session, company_id, now),
        tasks=await _tasks(session, company_id, now),
        recent_events=await _recent_events(session, company_id, last_seq),
        cycle=await _cycle(session, company_id),
    )


async def _cycle(session: AsyncSession, company_id: uuid.UUID) -> CycleView | None:
    """The latest cycle, open or not — the same one the reducer arrives at by following the
    events, which is what makes the two comparable."""
    cycle = await session.scalar(
        select(Cycle).where(Cycle.company_id == company_id).order_by(Cycle.seq.desc()).limit(1)
    )
    if cycle is None:
        return None
    return CycleView(id=cycle.id, seq=cycle.seq, stage=cycle.stage, deadline=cycle.stage_deadline)


async def _agents(session: AsyncSession, company_id: uuid.UUID, now: datetime) -> list[AgentView]:
    rows = (
        await session.execute(
            select(Agent, AgentActivity, Department.key)
            .join(AgentActivity, AgentActivity.agent_id == Agent.id)
            .outerjoin(Department, Department.id == Agent.department_id)
            .where(Agent.company_id == company_id, Agent.status != AgentStatus.RETIRED)
            .order_by(Agent.created_at, Agent.id)
        )
    ).all()
    return [
        AgentView(
            id=agent.id,
            role=agent.role,
            display_name=agent.display_name,
            avatar_key=agent.avatar_key,
            department_id=agent.department_id,
            department_key=department_key,
            activity=ActivityView(
                state=effective_state(activity, now),
                stored_state=ActivityState(activity.state),
                detail=activity.detail,
                since=activity.since,
                run_id=activity.run_id,
                task_id=activity.task_id,
                last_event_seq=activity.last_event_seq,
            ),
        )
        for agent, activity, department_key in rows
    ]


async def _tasks(session: AsyncSession, company_id: uuid.UUID, now: datetime) -> list[TaskView]:
    last_event = (
        select(EventRecord.seq, EventRecord.occurred_at)
        .where(EventRecord.task_id == Task.id, EventRecord.event_type.like("TASK\\_%"))
        .order_by(EventRecord.seq.desc())
        .limit(1)
        .lateral("last_event")
    )
    latest_run = (
        select(AgentRun.id, AgentRun.agent_id)
        .where(AgentRun.task_id == Task.id)
        .order_by(AgentRun.attempt.desc(), AgentRun.created_at.desc())
        .limit(1)
        .lateral("latest_run")
    )
    rows = (
        await session.execute(
            select(
                Task,
                last_event.c.seq,
                last_event.c.occurred_at,
                latest_run.c.id,
                latest_run.c.agent_id,
            )
            .select_from(Task)
            .join(last_event, literal_column("true"))
            .outerjoin(latest_run, literal_column("true"))
            .where(Task.company_id == company_id)
            .where(
                Task.state.notin_(_FINISHED)
                | (last_event.c.occurred_at > now - FINISHED_TASK_WINDOW)
            )
            .order_by(Task.created_at, Task.id)
        )
    ).all()
    return [
        TaskView(
            id=task.id,
            name=task.name,
            display_name=task.display_name,
            required_role=task.required_role,
            state=task.state,
            depends_on=list(task.depends_on or []),
            workflow_run_id=task.workflow_run_id,
            attempt=task.attempt,
            run_id=run_id,
            agent_id=agent_id,
            since=since,
            last_event_seq=seq,
        )
        for task, seq, since, run_id, agent_id in rows
    ]


async def _recent_events(
    session: AsyncSession, company_id: uuid.UUID, last_seq: int
) -> list[EventEnvelope]:
    rows = (
        await session.scalars(
            select(EventRecord)
            .where(EventRecord.company_id == company_id, EventRecord.seq <= last_seq)
            .order_by(EventRecord.seq.desc())
            .limit(RECENT_EVENTS)
        )
    ).all()
    return [to_envelope(row) for row in reversed(rows)]
