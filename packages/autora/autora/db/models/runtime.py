"""Runtime tables: FSM audit trail (T-104), event log / outbox (T-106), schedules (T-212).

Work execution tables (tasks, agent_runs, ...) live in ``tasks.py``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Identity,
    Index,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from autora.db.base import Base, IdMixin, TimestampMixin, check_regex


class StateTransition(IdMixin, Base):
    """Append-only record of every FSM transition (UPDATE/DELETE rejected by trigger)."""

    __tablename__ = "state_transitions"
    __table_args__ = (Index("ix_state_transitions_entity", "entity_type", "entity_id", "at"),)

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"))
    entity_type: Mapped[str]
    entity_id: Mapped[uuid.UUID]
    from_state: Mapped[str]
    to_state: Mapped[str]
    reason: Mapped[str | None]
    actor: Mapped[dict[str, Any]]
    at: Mapped[datetime] = mapped_column(server_default=func.now())


class EventRecord(Base):
    """The event log: outbox, audit trail and realtime source in one table.

    ``id`` is the envelope's ``event_id``. ``seq`` is the only ordering key readers may use.
    Rows are append-only except ``processed_at``, which the dispatcher sets once (trigger in
    migration 0003). Written only through ``autora.runtime.events.outbox.emit``.
    """

    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_company_seq", "company_id", "seq"),
        Index("ix_events_run_seq", "run_id", "seq"),
        Index("ix_events_task_seq", "task_id", "seq"),
        Index("ix_events_correlation_seq", "correlation_id", "seq"),
        Index("ix_events_type_occurred", "event_type", "occurred_at"),
        Index("ix_events_unprocessed", "seq", postgresql_where=text("processed_at IS NULL")),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), unique=True)
    event_type: Mapped[str]
    schema_version: Mapped[int]
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"))
    occurred_at: Mapped[datetime]
    aggregate_type: Mapped[str]
    aggregate_id: Mapped[uuid.UUID]
    agent_id: Mapped[uuid.UUID | None]
    task_id: Mapped[uuid.UUID | None]
    run_id: Mapped[uuid.UUID | None]
    workflow_run_id: Mapped[uuid.UUID | None]
    cycle_id: Mapped[uuid.UUID | None]
    correlation_id: Mapped[uuid.UUID | None]
    causation_id: Mapped[uuid.UUID | None]
    actor: Mapped[dict[str, Any]]
    payload: Mapped[dict[str, Any]]
    recorded_at: Mapped[datetime] = mapped_column(server_default=func.now())
    processed_at: Mapped[datetime | None]


class Schedule(IdMixin, TimestampMixin, Base):
    """A time trigger. The table is the source of truth: workers poll due rows with
    ``FOR UPDATE SKIP LOCKED`` and a lease, so any number of workers fire each run once."""

    __tablename__ = "schedules"
    __table_args__ = (
        UniqueConstraint("company_id", "name"),
        check_regex("name", "^[a-z][a-z0-9_]*(\\.[a-z][a-z0-9_]*)*$"),
        check_regex("handler", "^[a-z][a-z0-9_]*(\\.[a-z][a-z0-9_]*)*$"),
        CheckConstraint(
            "(lease_owner IS NULL) = (lease_until IS NULL)", name="lease_fields_together"
        ),
        Index("ix_schedules_due", "next_run_at", postgresql_where=text("enabled")),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"))
    name: Mapped[str]
    """e.g. "cycle.daily_start"."""
    cron: Mapped[str]
    """Standard 5-field cron, evaluated in ``timezone``."""
    timezone: Mapped[str] = mapped_column(server_default="UTC")
    handler: Mapped[str]
    """Registered handler key, e.g. "cycle.start"."""
    payload: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    enabled: Mapped[bool] = mapped_column(server_default="true")
    next_run_at: Mapped[datetime]
    last_run_at: Mapped[datetime | None]
    last_error: Mapped[str | None]
    consecutive_failures: Mapped[int] = mapped_column(server_default="0")
    lease_owner: Mapped[str | None]
    lease_until: Mapped[datetime | None]
