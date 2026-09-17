"""Work execution tables: workflow runs, tasks, agent runs and their steps (T-201).

Specs: logs/platform/10_DATABASE_SCHEMA.md (Runtime), logs/3d-office/07_DATABASE_MODEL.md §2,
lifecycles in ``autora.runtime.lifecycles``.

Invariants enforced by the database rather than by convention:
- a task holds a lease exactly when it is RUNNING (claim and release must be consistent);
- an agent run has ``finished_at`` exactly when it is in a terminal state;
- one agent run per (task, attempt);
- agent steps are append-only (the trace must not be rewritten).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from autora.db.base import Base, CreatedAtMixin, IdMixin, TimestampMixin, check_in, check_regex


class WorkflowRunState(StrEnum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TaskState(StrEnum):
    PENDING = "PENDING"
    """Waiting for dependencies."""
    READY = "READY"
    """Claimable by a worker (or awaiting a human/service node)."""
    RUNNING = "RUNNING"
    """Leased by a worker."""
    WAITING_APPROVAL = "WAITING_APPROVAL"
    BLOCKED_BUDGET = "BLOCKED_BUDGET"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    """Final failure: attempts exhausted or non-retryable."""
    CANCELLED = "CANCELLED"


class AgentRunState(StrEnum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    EVALUATING = "EVALUATING"
    REPAIRING = "REPAIRING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ABORTED = "ABORTED"


AGENT_RUN_TERMINAL = (AgentRunState.COMPLETED, AgentRunState.FAILED, AgentRunState.ABORTED)


class StepKind(StrEnum):
    THINK = "think"
    ACT = "act"
    OBSERVE = "observe"
    EVALUATE = "evaluate"
    REPAIR = "repair"


def _in_list(values) -> str:
    return ", ".join(f"'{v}'" for v in values)


class WorkflowRun(IdMixin, TimestampMixin, Base):
    __tablename__ = "workflow_runs"
    __table_args__ = (
        check_in("state", WorkflowRunState),
        check_regex("template_name", "^[a-z][a-z0-9_]*(\\.[a-z][a-z0-9_]*)*$"),
        Index("ix_workflow_runs_company_state", "company_id", "state"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"))
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"))
    cycle_id: Mapped[uuid.UUID | None]
    """cycles.id; FK added with the cycles table (Phase 3)."""
    template_name: Mapped[str]
    """e.g. "newsroom.story_to_article_v2"."""
    params: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    state: Mapped[str] = mapped_column(server_default=WorkflowRunState.RUNNING.value)
    finished_at: Mapped[datetime | None]


class Task(IdMixin, TimestampMixin, Base):
    __tablename__ = "tasks"
    __table_args__ = (
        check_in("state", TaskState),
        check_regex("name", "^[a-z][a-z0-9_]*$"),
        check_regex("required_role", "^[a-z][a-z0-9_]*$"),
        CheckConstraint("max_attempts >= 1", name="max_attempts_positive"),
        CheckConstraint("attempt >= 0 AND attempt <= max_attempts", name="attempt_within_max"),
        CheckConstraint("budget_usd IS NULL OR budget_usd >= 0", name="budget_non_negative"),
        CheckConstraint(
            "(state = 'RUNNING') = (lease_owner IS NOT NULL AND lease_until IS NOT NULL)",
            name="lease_iff_running",
        ),
        # Worker queue: claim the best READY task.
        Index(
            "ix_tasks_ready_queue",
            "company_id",
            "priority",
            "created_at",
            postgresql_where=text("state = 'READY'"),
        ),
        # Reaper: find expired leases.
        Index("ix_tasks_running_lease", "lease_until", postgresql_where=text("state = 'RUNNING'")),
        Index("ix_tasks_workflow_run", "workflow_run_id"),
        Index("ix_tasks_company_state", "company_id", "state"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"))
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"))
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workflow_runs.id"))
    cycle_id: Mapped[uuid.UUID | None]
    name: Mapped[str]
    """Template node name, e.g. "research"."""
    display_name: Mapped[str]
    """Shown to people, e.g. "Find today's AI stories"."""
    required_role: Mapped[str]
    """Agent role, or "human" / "system" for approval and service nodes."""
    state: Mapped[str] = mapped_column(server_default=TaskState.PENDING.value)
    depends_on: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), server_default=text("'{}'::uuid[]")
    )
    input: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    output: Mapped[dict[str, Any] | None]
    output_schema_ref: Mapped[str | None]
    """Registered output schema name the Evaluator validates against."""
    attempt: Mapped[int] = mapped_column(server_default="0")
    max_attempts: Mapped[int] = mapped_column(server_default="3")
    budget_usd: Mapped[Decimal | None]
    progress: Mapped[dict[str, Any] | None]
    """{label, current, target}; None means no quantifiable progress (show elapsed time)."""
    priority: Mapped[int] = mapped_column(server_default="100")
    """Lower runs first."""
    lease_owner: Mapped[str | None]
    lease_until: Mapped[datetime | None]


class AgentRun(IdMixin, TimestampMixin, Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        UniqueConstraint("task_id", "attempt"),
        check_in("state", AgentRunState),
        CheckConstraint("attempt >= 1", name="attempt_positive"),
        CheckConstraint(
            "cost_usd >= 0 AND tokens_in >= 0 AND tokens_out >= 0 AND steps_count >= 0",
            name="usage_non_negative",
        ),
        CheckConstraint(
            f"(state IN ({_in_list(AGENT_RUN_TERMINAL)})) = (finished_at IS NOT NULL)",
            name="finished_iff_terminal",
        ),
        Index("ix_agent_runs_agent_created", "agent_id", "created_at"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"))
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id"))
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"))
    attempt: Mapped[int]
    state: Mapped[str] = mapped_column(server_default=AgentRunState.CREATED.value)
    input: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    output: Mapped[dict[str, Any] | None]
    evaluation: Mapped[dict[str, Any] | None]
    """{passed, score, issues[]}."""
    handoff: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    """[{to_role, task_id}] computed by the DAG when the run completes."""
    error: Mapped[dict[str, Any] | None]
    """{error_class, message}."""
    cost_usd: Mapped[Decimal] = mapped_column(server_default="0")
    tokens_in: Mapped[int] = mapped_column(BigInteger, server_default="0")
    tokens_out: Mapped[int] = mapped_column(BigInteger, server_default="0")
    steps_count: Mapped[int] = mapped_column(server_default="0")
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]


class AgentStep(IdMixin, CreatedAtMixin, Base):
    """One step of a run's Observe→Think→Act→Evaluate loop. Append-only (trigger)."""

    __tablename__ = "agent_steps"
    __table_args__ = (
        UniqueConstraint("run_id", "seq"),
        check_in("kind", StepKind),
        CheckConstraint("seq >= 0", name="seq_non_negative"),
        CheckConstraint("cost_usd >= 0", name="cost_non_negative"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"))
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_runs.id"))
    seq: Mapped[int]
    kind: Mapped[str]
    prompt_hash: Mapped[str | None]
    tool_calls: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    summary: Mapped[str | None]
    blob_key: Mapped[str | None]
    """Full prompt/response payload in the BlobStore (T-210)."""
    cost_usd: Mapped[Decimal] = mapped_column(server_default="0")
