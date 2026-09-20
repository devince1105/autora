"""Agent definitions (logs/platform/03_AGENT_RUNTIME.md §2).

An Agent row is a *definition*: role, permissions, model policy (aliases, never model ids),
budget. Executions are ``agent_runs`` (T-201); "what it is doing now" is
``agent_activity`` (T-107, logs/3d-office/02_AGENT_STATE_MODEL.md).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Index, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from autora.db.base import (
    Base,
    CreatedAtMixin,
    IdMixin,
    TimestampMixin,
    check_in,
    check_regex,
)


class AgentStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    RETIRED = "retired"


class ActivityState(StrEnum):
    IDLE = "IDLE"
    THINKING = "THINKING"
    WORKING = "WORKING"
    WAITING = "WAITING"
    REVIEWING = "REVIEWING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    PAUSED = "PAUSED"


class Agent(IdMixin, TimestampMixin, Base):
    __tablename__ = "agents"
    __table_args__ = (
        UniqueConstraint("company_id", "display_name"),
        # Roles are open-ended (defined by domains), but always a lowercase identifier:
        # ceo, researcher, analyst, writer, editor, marketing, ...
        check_regex("role", "^[a-z][a-z0-9_]*$"),
        check_in("status", AgentStatus),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"))
    role: Mapped[str]
    """The runtime's key: a behavior is registered for it and tasks ask for it by name."""
    role_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("roles.id"))
    """The same key, resolved to a position in the organisation (T-600). Nullable: agents hired
    before the organisation existed keep working, they simply have no place on the org chart."""
    department_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("departments.id"))
    """Where this agent works. The 3D office puts it in that department's room."""
    display_name: Mapped[str]
    description: Mapped[str | None]
    avatar_key: Mapped[str] = mapped_column(server_default="default")
    """Visual asset key for the 3D office. Never a layout position."""
    capabilities: Mapped[list[str]] = mapped_column(server_default=text("'{}'::text[]"))
    tools: Mapped[list[str]] = mapped_column(server_default=text("'{}'::text[]"))
    permissions: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    model_policy: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    """capability -> model alias, e.g. {"drafting": "frontier"}."""
    budget: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    """{per_run_usd, per_day_usd, max_steps, max_tokens}."""
    status: Mapped[str] = mapped_column(server_default=AgentStatus.ACTIVE.value)


class AgentActivity(Base):
    """What an agent is doing right now. One row per agent.

    Written only by ``autora.runtime.activity`` in the same transaction as the matching
    ``AGENT_*`` event, so the realtime snapshot (this table) and the event stream never
    disagree. ``last_event_seq`` is the seq of that event.
    """

    __tablename__ = "agent_activity"
    __table_args__ = (check_in("state", ActivityState),)

    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"), primary_key=True)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    state: Mapped[str]
    detail: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    run_id: Mapped[uuid.UUID | None]
    task_id: Mapped[uuid.UUID | None]
    since: Mapped[datetime]
    last_event_seq: Mapped[int] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class MemoryKind(StrEnum):
    RUN = "run"
    """What this agent did on one task, and how it went."""
    NOTE = "note"
    """Something a person or another part of the company wants it to keep in mind."""


class AgentMemoryEntry(IdMixin, CreatedAtMixin, Base):
    """What an agent remembers between runs (platform/08 §1, T-609).

    Not its working memory — that is ``agent_steps``, the transcript of one run, and it stays
    there. This is the small, structured trace of *the last few runs*: what the task was, what
    came out, and why it was sent back. It is what lets a writer notice it is being asked for a
    third revision of the same draft.

    Bounded twice, because a memory that grows without limit stops being memory and becomes a
    cost: rows expire (30 days), and an agent keeps at most the newest ``ROW_CAP``. Both are
    enforced when writing, so no cleanup job is required for correctness.
    """

    __tablename__ = "agent_memory"
    __table_args__ = (
        Index("ix_agent_memory_agent_recent", "agent_id", "created_at"),
        check_in("kind", MemoryKind),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"))
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"), index=True)
    kind: Mapped[str]
    content: Mapped[dict[str, Any]] = mapped_column(JSONB)
    """{task, outcome, summary, issues[]} for a run; free-form for a note."""
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id"))
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_runs.id"))
    expires_at: Mapped[datetime | None]
