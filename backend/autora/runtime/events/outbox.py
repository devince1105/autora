"""Persisting events: the transactional outbox (logs/3d-office/05_REALTIME_ARCHITECTURE.md §1).

``emit(session, envelope)`` writes the event in the caller's transaction, so the event exists
if and only if the state change commits. It also queues ``NOTIFY autora_events`` which Postgres
delivers only on commit.

Ordering guarantee: readers follow ``seq`` with a cursor (``seq > last_seq``). Sequence values
are assigned at insert time, so two concurrent transactions could otherwise commit out of seq
order and a reader could skip the smaller seq forever. ``emit`` takes a transaction-scoped
advisory lock per company before inserting, which makes commit order equal seq order within a
company. Keep event-emitting transactions short: the lock is held until commit.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func, insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from autora.db.models import EventRecord
from autora.runtime.events.schema import EventEnvelope, Persistence, parse_event

EVENTS_CHANNEL = "autora_events"
EPHEMERAL_CHANNEL = "autora_ephemeral"
EPHEMERAL_MAX_BYTES = 7900  # NOTIFY payloads must stay under 8000 bytes
_LOCK_NAMESPACE = 0x41_55_54  # arbitrary int4 namespace for pg_advisory_xact_lock(int, int)


class EventEmitError(Exception):
    pass


async def emit(session: AsyncSession, envelope: EventEnvelope) -> EventEnvelope:
    """Append ``envelope`` to the event log. Returns a copy carrying the assigned ``seq``."""
    if envelope.persistence is Persistence.EPHEMERAL:
        raise EventEmitError(f"{envelope.event_type} is ephemeral; it is never written to the log")
    if envelope.seq is not None:
        raise EventEmitError(
            f"event {envelope.event_id} was already persisted (seq={envelope.seq})"
        )

    company = str(envelope.company_id)
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:ns, hashtext(:company))"),
        {"ns": _LOCK_NAMESPACE, "company": company},
    )
    seq = await session.scalar(
        insert(EventRecord)
        .values(
            id=envelope.event_id,
            event_type=envelope.event_type,
            schema_version=envelope.schema_version,
            company_id=envelope.company_id,
            occurred_at=envelope.occurred_at,
            aggregate_type=envelope.aggregate_type,
            aggregate_id=envelope.aggregate_id,
            agent_id=envelope.agent_id,
            task_id=envelope.task_id,
            run_id=envelope.run_id,
            workflow_run_id=envelope.workflow_run_id,
            cycle_id=envelope.cycle_id,
            correlation_id=envelope.correlation_id,
            causation_id=envelope.causation_id,
            actor=envelope.actor.as_json(),
            payload=envelope.payload.model_dump(mode="json"),
        )
        .returning(EventRecord.seq)
    )
    await session.execute(select(func.pg_notify(EVENTS_CHANNEL, f"{company}:{seq}")))
    return envelope.model_copy(update={"seq": seq})


async def publish_ephemeral(session: AsyncSession, envelope: EventEnvelope) -> None:
    """Send an ephemeral event (progress, agent heartbeat) to live viewers. Never stored; the
    NOTIFY is delivered when the caller commits. Oversized payloads are refused."""
    if envelope.persistence is not Persistence.EPHEMERAL:
        raise EventEmitError(f"{envelope.event_type} is persisted; use emit()")
    data = envelope.model_dump_json(exclude={"seq"})
    if len(data.encode("utf-8")) > EPHEMERAL_MAX_BYTES:
        raise EventEmitError(f"{envelope.event_type} payload exceeds {EPHEMERAL_MAX_BYTES} bytes")
    await session.execute(select(func.pg_notify(EPHEMERAL_CHANNEL, data)))


def to_envelope(row: EventRecord) -> EventEnvelope:
    return parse_event(
        {
            "event_id": row.id,
            "seq": row.seq,
            "event_type": row.event_type,
            "schema_version": row.schema_version,
            "company_id": row.company_id,
            "occurred_at": row.occurred_at,
            "aggregate_type": row.aggregate_type,
            "aggregate_id": row.aggregate_id,
            "agent_id": row.agent_id,
            "task_id": row.task_id,
            "run_id": row.run_id,
            "workflow_run_id": row.workflow_run_id,
            "cycle_id": row.cycle_id,
            "correlation_id": row.correlation_id,
            "causation_id": row.causation_id,
            "actor": row.actor,
            "payload": row.payload,
        }
    )


async def load_events(
    session: AsyncSession,
    company_id: uuid.UUID,
    *,
    after_seq: int = 0,
    until_seq: int | None = None,
    event_types: Sequence[str] | None = None,
    agent_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
    limit: int = 200,
) -> list[EventEnvelope]:
    """Events of one company with ``after_seq < seq <= until_seq``, ordered by seq."""
    stmt = select(EventRecord).where(
        EventRecord.company_id == company_id, EventRecord.seq > after_seq
    )
    if until_seq is not None:
        stmt = stmt.where(EventRecord.seq <= until_seq)
    if event_types:
        stmt = stmt.where(EventRecord.event_type.in_(event_types))
    if agent_id is not None:
        stmt = stmt.where(EventRecord.agent_id == agent_id)
    if run_id is not None:
        stmt = stmt.where(EventRecord.run_id == run_id)
    rows = await session.scalars(stmt.order_by(EventRecord.seq).limit(limit))
    return [to_envelope(row) for row in rows]
