"""Model call ledger (T-207) and in-flight cost reservations (T-209).

``model_calls`` is the source of truth for model cost: one row per provider call, successful or
not. Budgets and the cycle expense settlement (T-602) aggregate it; nothing edits it
(append-only trigger in migration 0006).

``cost_reservations`` holds the estimated cost of calls that are in progress, so two concurrent
calls cannot both squeeze under the same budget.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, text
from sqlalchemy.orm import Mapped, mapped_column

from autora.db.base import Base, CreatedAtMixin, IdMixin, check_in


class ModelCallStatus(StrEnum):
    OK = "ok"
    ERROR = "error"


class ModelCall(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "model_calls"
    __table_args__ = (
        check_in("status", ModelCallStatus),
        CheckConstraint(
            "tokens_in >= 0 AND tokens_out >= 0 AND cache_read_tokens >= 0 "
            "AND cache_write_tokens >= 0 AND cost_usd >= 0 AND latency_ms >= 0",
            name="usage_non_negative",
        ),
        Index("ix_model_calls_company_created", "company_id", "created_at"),
        Index("ix_model_calls_project_created", "project_id", "created_at"),
        Index("ix_model_calls_run", "run_id"),
        Index("ix_model_calls_task", "task_id"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"))
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"))
    agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agents.id"))
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id"))
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_runs.id"))
    role: Mapped[str]
    capability: Mapped[str]
    alias: Mapped[str]
    provider: Mapped[str]
    model_id: Mapped[str]
    status: Mapped[str]
    stop_reason: Mapped[str | None]
    tokens_in: Mapped[int] = mapped_column(BigInteger, server_default="0")
    tokens_out: Mapped[int] = mapped_column(BigInteger, server_default="0")
    cache_read_tokens: Mapped[int] = mapped_column(BigInteger, server_default="0")
    cache_write_tokens: Mapped[int] = mapped_column(BigInteger, server_default="0")
    cost_usd: Mapped[Decimal] = mapped_column(server_default="0")
    latency_ms: Mapped[int] = mapped_column(server_default="0")
    error: Mapped[dict[str, Any] | None]


class CostReservation(IdMixin, CreatedAtMixin, Base):
    """Estimated cost held for an in-flight model call.

    Open (neither settled nor released) reservations count against budgets until they are older
    than the guard's TTL, so a crashed worker cannot block a budget forever.
    """

    __tablename__ = "cost_reservations"
    __table_args__ = (
        CheckConstraint("amount >= 0", name="amount_non_negative"),
        CheckConstraint(
            "NOT (settled_at IS NOT NULL AND released_at IS NOT NULL)", name="settled_xor_released"
        ),
        CheckConstraint(
            "(settled_at IS NULL) = (model_call_id IS NULL)", name="settled_iff_model_call"
        ),
        Index(
            "ix_cost_reservations_open",
            "company_id",
            "created_at",
            postgresql_where=text("settled_at IS NULL AND released_at IS NULL"),
        ),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"))
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"))
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id"))
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_runs.id"))
    amount: Mapped[Decimal]
    settled_at: Mapped[datetime | None]
    released_at: Mapped[datetime | None]
    model_call_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("model_calls.id"))
    actual_cost: Mapped[Decimal | None]
