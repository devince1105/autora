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


class CustomerKind(StrEnum):
    SUBSCRIBER = "subscriber"
    SPONSOR = "sponsor"
    CLIENT = "client"


class Customer(IdMixin, TimestampMixin, Base):
    """Somebody who pays a business of this company (ARCHITECTURE_V2_1 §7, T-612).

    **This table holds no personal data, by design.** No name, no email, no address, no card —
    only ``external_ref``, the id the payment provider knows them by. Everything a person could
    be identified by stays with the provider, which is where it is already protected and where
    a deletion request goes. What the company needs from a customer is what it earns from them
    and when they left, and neither of those needs a name.

    The row exists so that "what does each customer earn us" is answerable on the day the first
    payment arrives, rather than being a migration nobody has time for that week. It is small
    on purpose: invoices, tax, receivables and reconciliation are deliberately not here.
    """

    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint("company_id", "external_ref"),
        check_in("kind", CustomerKind),
        CheckConstraint(
            "churned_at IS NULL OR churned_at >= acquired_at", name="churned_after_acquired"
        ),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    business_unit_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("business_units.id"))
    """Which business they are a customer of. NULL only while a company has no businesses."""
    external_ref: Mapped[str]
    """The payment provider's id for them. The only identifier this company stores."""
    kind: Mapped[str]
    acquired_at: Mapped[datetime]
    churned_at: Mapped[datetime | None]
    """When they stopped paying. A churned customer is kept: what they earned still happened."""


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
        UniqueConstraint(
            "company_id",
            "business_unit_id",
            "project_id",
            "period",
            postgresql_nulls_not_distinct=True,
        ),
        check_in("period", BudgetPeriod),
        check_regex("currency", "^[A-Z]{3}$"),
        CheckConstraint("amount >= 0", name="amount_non_negative"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"))
    business_unit_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("business_units.id"))
    """The envelope's owner, between the company and a project: company (both NULL) -> business
    unit -> project. Four layers of cap, all checked by the same guard (T-600)."""
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
    business_unit_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("business_units.id"))
    """Which business earned or spent it. Denormalised on purpose: a project implies its unit,
    but revenue from a product has no project, and a unit's P&L must be one query (T-600)."""
    product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("products.id"))
    """Which offering earned it. Set on revenue, usually NULL on costs."""
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"))
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customers.id"))
    """Who paid it (T-612). NULL on costs, and on revenue that came from nobody in particular."""
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
