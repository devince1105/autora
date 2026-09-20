"""Runtime tables: FSM audit trail (T-104), event log / outbox (T-106), schedules (T-212),
policy decisions (T-205), approvals (T-206).

Work execution tables (tasks, agent_runs, ...) live in ``tasks.py``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
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

from autora.db.base import Base, IdMixin, TimestampMixin, check_in, check_regex


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
    """No FK, like run_id and workflow_run_id above: the log outlives what it points at."""
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


class PolicyDecision(IdMixin, Base):
    """Audit row for every policy decision (append-only, trigger in migration 0009)."""

    __tablename__ = "policy_decisions"
    __table_args__ = (
        CheckConstraint("outcome IN ('allow', 'needs_approval', 'deny')", name="outcome_valid"),
        Index("ix_policy_decisions_company_created", "company_id", "created_at"),
        Index("ix_policy_decisions_run", "run_id"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"))
    actor: Mapped[dict[str, Any]]
    role: Mapped[str | None]
    action: Mapped[str]
    args_hash: Mapped[str]
    outcome: Mapped[str]
    rule_id: Mapped[str]
    reason: Mapped[str]
    agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agents.id"))
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_runs.id"))
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ApprovalState(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class ApprovalKind(StrEnum):
    TOOL_CALL = "tool_call"
    COMMAND = "command"
    PROJECT = "project"
    KILL = "kill"
    STRATEGY = "strategy"
    ARTICLE = "article"


class Approval(IdMixin, TimestampMixin, Base):
    """A request for a human decision (logs/platform/07 §4).

    Linked to what it blocks: a suspended agent run (``run_id`` + ``task_id``), a human task node
    (``task_id`` only), or nothing (a command whose caller acts on the APPROVAL_* event).
    """

    __tablename__ = "approvals"
    __table_args__ = (
        check_in("state", ApprovalState),
        check_in("kind", ApprovalKind),
        CheckConstraint(
            "(state IN ('APPROVED', 'REJECTED')) = "
            "(decided_at IS NOT NULL AND decided_by IS NOT NULL)",
            name="decided_iff_approved_or_rejected",
        ),
        CheckConstraint("run_id IS NULL OR task_id IS NOT NULL", name="run_implies_task"),
        # One open request per thing being decided.
        Index(
            "uq_approvals_pending_ref",
            "ref_type",
            "ref_id",
            unique=True,
            postgresql_where=text("state = 'PENDING'"),
        ),
        Index("ix_approvals_company_state", "company_id", "state", "created_at"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"))
    kind: Mapped[str]
    ref_type: Mapped[str]
    ref_id: Mapped[uuid.UUID]
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id"))
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_runs.id"))
    action: Mapped[str | None]
    """The policy action that needs approval, e.g. "publish_article"."""
    payload: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    """What exactly is being approved (tool args, command body). Shown to the operator."""
    summary: Mapped[str]
    requested_by: Mapped[dict[str, Any]]
    state: Mapped[str] = mapped_column(server_default=ApprovalState.PENDING.value)
    expires_at: Mapped[datetime | None]
    decided_by: Mapped[dict[str, Any] | None]
    decided_at: Mapped[datetime | None]
    reason: Mapped[str | None]
