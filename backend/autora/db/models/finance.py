"""Budgets and the transaction ledger (logs/platform/06_BUSINESS_REVENUE.md).

``transactions`` is the single financial fact table. Revenue and expenses are views over it.
Rows are append-only: a database trigger rejects UPDATE and DELETE (migration 0002), so a
correction is a new compensating transaction, never an edit.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import CheckConstraint, ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from autora.db.base import (
    Base,
    CreatedAtMixin,
    IdMixin,
    TimestampMixin,
    check_in,
    check_regex,
)


class BudgetPeriod(StrEnum):
    CYCLE = "cycle"
    DAY = "day"
    MONTH = "month"


class TransactionKind(StrEnum):
    EXPENSE = "expense"
    REVENUE = "revenue"
    CAPITAL_IN = "capital_in"
    CAPITAL_OUT = "capital_out"
    TRANSFER = "transfer"


class TransactionSource(StrEnum):
    SYSTEM = "system"
    HUMAN = "human"
    INTEGRATION = "integration"


class Budget(IdMixin, TimestampMixin, Base):
    """A recurring spending envelope. ``project_id`` NULL means the company-wide cap."""

    __tablename__ = "budgets"
    __table_args__ = (
        UniqueConstraint("company_id", "project_id", "period", postgresql_nulls_not_distinct=True),
        check_in("period", BudgetPeriod),
        check_regex("currency", "^[A-Z]{3}$"),
        CheckConstraint("amount >= 0", name="amount_non_negative"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"))
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"))
    period: Mapped[str]
    amount: Mapped[Decimal]
    currency: Mapped[str] = mapped_column(server_default="USD")
    hard_cap: Mapped[bool] = mapped_column(server_default="true")


class Transaction(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint("idempotency_key"),
        check_in("kind", TransactionKind),
        check_in("source", TransactionSource),
        check_regex("category", "^[a-z][a-z0-9_]*$"),
        check_regex("currency", "^[A-Z]{3}$"),
        # The sign lives in `kind`; amounts are always positive.
        CheckConstraint("amount > 0", name="amount_positive"),
        Index("ix_transactions_company_occurred", "company_id", "occurred_at"),
        Index("ix_transactions_project_occurred", "project_id", "occurred_at"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"))
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"))
    kind: Mapped[str]
    category: Mapped[str]
    """model_cost, tool_cost, ads, subscription, sponsorship, ..."""
    amount: Mapped[Decimal]
    currency: Mapped[str] = mapped_column(server_default="USD")
    ref_type: Mapped[str | None]
    ref_id: Mapped[uuid.UUID | None]
    occurred_at: Mapped[datetime]
    source: Mapped[str]
    idempotency_key: Mapped[str]
    memo: Mapped[str | None]
