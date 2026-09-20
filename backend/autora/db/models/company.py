"""Company identity, goals and governance policies (logs/platform/02_COMPANY_MODEL.md)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import ARRAY, CheckConstraint, ForeignKey, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from autora.db.base import (
    Base,
    CreatedAtMixin,
    IdMixin,
    TimestampMixin,
    check_in,
    check_regex,
)


class CompanyType(StrEnum):
    NEWSROOM = "newsroom"
    SAAS = "saas"
    RESEARCH = "research"
    ECOMMERCE = "ecommerce"
    SOFTWARE_STUDIO = "software_studio"


class CompanyStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class GoalLevel(StrEnum):
    ANNUAL = "annual"
    QUARTER = "quarter"
    CYCLE = "cycle"


class GoalStatus(StrEnum):
    ACTIVE = "active"
    ACHIEVED = "achieved"
    MISSED = "missed"
    CANCELLED = "cancelled"


class Company(IdMixin, TimestampMixin, Base):
    __tablename__ = "companies"
    __table_args__ = (
        UniqueConstraint("slug"),
        check_regex("slug", "^[a-z0-9][a-z0-9-]{1,62}$"),
        check_in("type", CompanyType),
        check_in("status", CompanyStatus),
    )

    slug: Mapped[str]
    name: Mapped[str]
    type: Mapped[str]
    mission: Mapped[str | None]
    strategy_doc: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(server_default=CompanyStatus.ACTIVE.value)


class CompanyGoal(IdMixin, TimestampMixin, Base):
    __tablename__ = "company_goals"
    __table_args__ = (
        check_in("level", GoalLevel),
        check_in("status", GoalStatus),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    business_unit_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("business_units.id"))
    """Whose goal it is. NULL = the company's own (T-600)."""
    parent_goal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("company_goals.id"))
    level: Mapped[str]
    title: Mapped[str]
    """Human-readable goal, e.g. "Publish 3 high-quality bilingual articles"."""
    metric: Mapped[str]
    """Machine-readable KPI key the Reporting service measures, e.g. "published_articles"."""
    target: Mapped[Decimal]
    current: Mapped[Decimal | None]
    deadline: Mapped[datetime | None]
    status: Mapped[str] = mapped_column(server_default=GoalStatus.ACTIVE.value)


class CompanyPolicy(IdMixin, TimestampMixin, Base):
    """Structured governance rule, e.g. key="newsroom.primary_lang", value="zh-TW".

    Values are JSON, never natural language. Loosening a policy is a HUMAN action
    (logs/platform/07_PERMISSION_MODEL.md); that is enforced by the command layer.
    """

    __tablename__ = "company_policies"
    __table_args__ = (
        UniqueConstraint("company_id", "key"),
        check_regex("key", "^[a-z][a-z0-9_]*(\\.[a-z][a-z0-9_]*)*$"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"))
    key: Mapped[str]
    value: Mapped[Any] = mapped_column(JSONB)
    """Any JSON value: string, number, bool, list or object."""
    updated_by: Mapped[dict[str, Any] | None]
    """Actor JSON ({kind, id}) of the last writer."""


class CycleStage(StrEnum):
    PLANNING = "PLANNING"
    EXECUTING = "EXECUTING"
    MEASURING = "MEASURING"
    REVIEWING = "REVIEWING"
    DONE = "DONE"


class Cycle(IdMixin, TimestampMixin, Base):
    """One day of the company's operating loop (logs/platform/02_COMPANY_MODEL.md §2).

    The cycle is the **only root that may trigger an LLM**: nothing else creates agent work on
    its own (anti-runaway gate 1). ``plan`` is what the CEO decided in PLANNING (a ``CyclePlan``)
    and ``review`` what it concluded in REVIEWING (a ``CycleReview``); both stay null while the
    CEO agent does not exist yet, and a fallback plan is recorded in ``plan`` like any other.

    ``stage_deadline`` is what makes the loop unable to hang: a stage that is still running when
    its deadline passes is advanced anyway (gate 6), and what was left behind is recorded in
    ``CYCLE_STAGE_TIMEOUT``.
    """

    __tablename__ = "cycles"
    __table_args__ = (
        UniqueConstraint("company_id", "seq"),
        check_in("stage", CycleStage),
        CheckConstraint("seq >= 1", name="seq_positive"),
        CheckConstraint("(stage = 'DONE') = (ended_at IS NOT NULL)", name="ended_at_iff_done"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    seq: Mapped[int]
    """1 for a company's first cycle, then +1. Unique per company."""
    stage: Mapped[str] = mapped_column(server_default=CycleStage.PLANNING.value)
    plan: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    review: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    governance: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    """What the deterministic rules looked at that day and what they did (T-610). Written even
    when nothing fired: a day on which the rules found nothing wrong has to be distinguishable
    from a day on which nobody ran them."""
    started_at: Mapped[datetime] = mapped_column(server_default=func.now())
    stage_deadline: Mapped[datetime | None]
    """When the current stage is advanced whether or not its work finished."""
    ended_at: Mapped[datetime | None]


class KpiScope(StrEnum):
    COMPANY = "company"
    BUSINESS_UNIT = "business_unit"
    PROJECT = "project"


class KpiSnapshot(IdMixin, CreatedAtMixin, Base):
    """What a scope achieved and what it cost, for one cycle (platform/06 §2, T-603).

    Written by Reporting in MEASURING, after the ledger has settled, and never by a model: a
    KPI is arithmetic over rows that already exist, so it is reproducible and cheap to redo.

    ``metrics`` is a flat map of numbers, and the names say where each came from: the company
    layer's own are bare (``cost_usd``, ``revenue_usd``, ``profit_usd``), a domain's carry the
    domain's name (``newsroom.published_articles``). The core stores numbers; it does not know
    what a published article is.
    """

    __tablename__ = "kpi_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "cycle_id",
            "scope",
            "business_unit_id",
            "project_id",
            postgresql_nulls_not_distinct=True,
        ),
        check_in("scope", KpiScope),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    cycle_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cycles.id"), index=True)
    business_unit_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("business_units.id"))
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"))
    scope: Mapped[str]
    """Which of the three the numbers are about; the matching id column is set, the others null."""
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    period_start: Mapped[datetime]
    period_end: Mapped[datetime]


class CommandOutcome(StrEnum):
    DONE = "done"
    """Executed. ``result`` says what it did."""
    REFUSED = "refused"
    """The policy said no, or the command contradicted the state of the company."""
    AWAITING_APPROVAL = "awaiting_approval"
    """A person has to decide. It runs when they approve, under the same key."""


class CommandRecord(IdMixin, CreatedAtMixin, Base):
    """Every attempt to change the company, decided and recorded (platform/06 §2, T-604).

    The row exists whatever happened — executed, refused, or waiting for a person — because
    "what did this company try to do, and who let it" has to be answerable afterwards, and a
    refusal is as much a part of that answer as a change.

    ``idempotency_key`` is what makes a command safe to send twice: the second attempt returns
    the first one's outcome instead of doing it again. An agent that retries a tool call, a
    double-clicked button and a redelivered webhook all land here.
    """

    __tablename__ = "commands_log"
    __table_args__ = (
        UniqueConstraint("idempotency_key"),
        check_in("outcome", CommandOutcome),
        check_regex("command", "^[A-Z][A-Za-z0-9]*$"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    command: Mapped[str]
    actor: Mapped[dict[str, Any]] = mapped_column(JSONB)
    role: Mapped[str | None]
    """The agent role that issued it, for the policy rule that decided it."""
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    decision: Mapped[str]
    """The policy's outcome: allow / deny / needs_approval / limited."""
    outcome: Mapped[str]
    reason: Mapped[str | None]
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    event_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), server_default=text("'{}'::uuid[]")
    )
    """The events this command caused, so a change can be traced back to the decision."""
    approval_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("approvals.id"))
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id"))
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    """Which run asked. Completes the audit chain — cycle -> plan -> command -> work — and lets
    an agent's written plan be checked against what it actually asked for."""
    idempotency_key: Mapped[str]


class DocumentKind(StrEnum):
    DAILY_SUMMARY = "daily_summary"
    """One cycle, in a few lines a person can read."""
    NOTE = "note"


class Document(IdMixin, TimestampMixin, Base):
    """Something written down for people to read later (platform/10, T-607).

    Not a log and not a report anyone has to assemble: a short piece of text, written when the
    thing it describes finished, with a reference back to it. The daily summary is the first
    kind — a cycle explained in ten lines, produced by arithmetic rather than by a model, so it
    costs nothing and cannot be wrong about what happened.

    No embedding column yet: nothing searches these. It joins when something does (platform/08).
    """

    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("company_id", "kind", "ref_type", "ref_id"),
        check_in("kind", DocumentKind),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    kind: Mapped[str]
    title: Mapped[str]
    body: Mapped[str]
    ref_type: Mapped[str | None]
    """What it is about ("cycle"), with ``ref_id``. Unique per kind, so a rewrite replaces."""
    ref_id: Mapped[uuid.UUID | None]
