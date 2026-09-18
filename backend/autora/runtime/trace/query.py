from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autora.db.models import AgentRun, AgentStep, EventRecord


@dataclass(frozen=True)
class StepView:
    seq: int
    kind: str
    summary: str | None
    tool_calls: list[dict[str, Any]] | None
    cost_usd: Decimal
    has_blob: bool
    created_at: datetime


@dataclass(frozen=True)
class TraceEntry:
    seq: int
    """Event seq (the only ordering key)."""
    event_type: str
    occurred_at: datetime
    actor: dict[str, Any]
    payload: dict[str, Any]
    step: StepView | None
    """The agent step this event belongs to, when the payload carries ``step_seq``."""


@dataclass(frozen=True)
class Trace:
    run_id: uuid.UUID
    company_id: uuid.UUID
    task_id: uuid.UUID
    agent_id: uuid.UUID
    attempt: int
    state: str
    cost_usd: Decimal
    entries: list[TraceEntry]
    steps: list[StepView]
    """All steps in order, including any that no event refers to."""


def _view(step: AgentStep) -> StepView:
    return StepView(
        seq=step.seq,
        kind=step.kind,
        summary=step.summary,
        tool_calls=step.tool_calls,
        cost_usd=step.cost_usd,
        has_blob=step.blob_key is not None,
        created_at=step.created_at,
    )


async def get_run_trace(session: AsyncSession, run_id: uuid.UUID) -> Trace | None:
    run = await session.get(AgentRun, run_id)
    if run is None:
        return None
    steps = {
        s.seq: _view(s)
        for s in await session.scalars(
            select(AgentStep).where(AgentStep.run_id == run_id).order_by(AgentStep.seq)
        )
    }
    events = await session.scalars(
        select(EventRecord).where(EventRecord.run_id == run_id).order_by(EventRecord.seq)
    )
    entries = []
    for event in events:
        step_seq = event.payload.get("step_seq")
        entries.append(
            TraceEntry(
                seq=event.seq,
                event_type=event.event_type,
                occurred_at=event.occurred_at,
                actor=event.actor,
                payload=event.payload,
                step=steps.get(step_seq) if isinstance(step_seq, int) else None,
            )
        )
    return Trace(
        run_id=run.id,
        company_id=run.company_id,
        task_id=run.task_id,
        agent_id=run.agent_id,
        attempt=run.attempt,
        state=run.state,
        cost_usd=run.cost_usd,
        entries=entries,
        steps=list(steps.values()),
    )
