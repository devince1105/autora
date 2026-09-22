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
    currency: Mapped[str] = mapped_column(server_default="TWD")
    """The base currency (D-023). The cost guard converts it to the meter's USD to compare."""
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
        CheckConstraint(
            "(source_amount IS NULL) = (source_currency IS NULL) "
            "AND (source_amount IS NULL) = (fx_rate IS NULL)",
            name="conversion_complete",
        ),
        CheckConstraint("fx_rate IS NULL OR fx_rate > 0", name="fx_rate_positive"),
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
    """In ``currency``, which is always the base (D-023): the ledger has one currency, so a
    balance is a sum and never a conversion."""
    currency: Mapped[str] = mapped_column(server_default="TWD")
    source_amount: Mapped[Decimal | None]
    """What arrived, before conversion — the model calls' USD, say. NULL when it arrived in
    the base. Kept so the row can be checked against the meter it came from."""
    source_currency: Mapped[str | None]
    fx_rate: Mapped[Decimal | None]
    """Base units per source unit, as used on this row. Changing ``FX_RATES`` later does not
    touch it: the past was converted at the rate of its day."""
    ref_type: Mapped[str | None]
    ref_id: Mapped[uuid.UUID | None]
    occurred_at: Mapped[datetime]
    source: Mapped[str]
    idempotency_key: Mapped[str]
    memo: Mapped[str | None]


class PriceInterval(StrEnum):
    MONTH = "month"
    YEAR = "year"


class PriceState(StrEnum):
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"


class SubscriptionState(StrEnum):
    TRIALING = "TRIALING"
    ACTIVE = "ACTIVE"
    PAST_DUE = "PAST_DUE"
    CANCELED = "CANCELED"


class Price(IdMixin, TimestampMixin, Base):
    """What a product costs, per interval (T-701, D-022).

    Not a column on ``products``: a price changes and a product does not. Raising the monthly
    price retires one row and adds another, so every subscription keeps pointing at the price it
    was sold at — which is the price its payments were for.
    """

    __tablename__ = "prices"
    __table_args__ = (
        UniqueConstraint("company_id", "provider", "external_ref"),
        check_in("interval", PriceInterval),
        check_in("state", PriceState),
        check_regex("currency", "^[A-Z]{3}$"),
        check_regex("provider", "^[a-z][a-z0-9_]*$"),
        CheckConstraint("amount > 0", name="amount_positive"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), index=True)
    amount: Mapped[Decimal]
    currency: Mapped[str] = mapped_column(server_default="TWD")
    interval: Mapped[str]
    state: Mapped[str] = mapped_column(server_default=PriceState.ACTIVE.value)
    provider: Mapped[str]
    """Which payment provider sells it, as a token (``stripe``). The core never branches on it."""
    external_ref: Mapped[str | None]
    """The provider's id for this price. NULL until it exists there."""


class Subscription(IdMixin, TimestampMixin, Base):
    """A customer paying a price, again and again, until they stop (T-701, D-022).

    The provider owns the truth — it bills, retries and cancels — and this row is the company's
    copy of what it said, kept so agents and reports can read it without calling anybody.
    Payments are separate rows: a subscription is a promise, a payment is money that arrived.
    """

    __tablename__ = "subscriptions"
    __table_args__ = (
        UniqueConstraint("company_id", "provider", "external_ref"),
        check_in("state", SubscriptionState),
        check_regex("provider", "^[a-z][a-z0-9_]*$"),
        CheckConstraint(
            "state <> 'CANCELED' OR canceled_at IS NOT NULL", name="canceled_has_a_date"
        ),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    business_unit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("business_units.id"))
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id"), index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"))
    price_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("prices.id"))
    state: Mapped[str]
    provider: Mapped[str]
    external_ref: Mapped[str]
    started_at: Mapped[datetime]
    current_period_end: Mapped[datetime | None]
    """When the provider will next try to charge. What "paid up until" means for this row."""
    canceled_at: Mapped[datetime | None]


class Payment(IdMixin, CreatedAtMixin, Base):
    """Money a provider says arrived, and the ledger row it became (T-701, D-022).

    Written only by an integration, never by an agent (platform/06 §1). Append-only like the
    ledger it feeds: a refund will be its own row, not an edit to this one. The unique
    ``(provider, external_ref)`` is what makes a webhook delivered twice one payment.
    """

    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("provider", "external_ref"),
        UniqueConstraint("transaction_id"),
        check_regex("currency", "^[A-Z]{3}$"),
        check_regex("provider", "^[a-z][a-z0-9_]*$"),
        CheckConstraint("amount > 0", name="amount_positive"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    business_unit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("business_units.id"))
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id"), index=True)
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("subscriptions.id"), index=True
    )
    """NULL for a one-off payment. Every payment today is a subscription's, but the ledger
    should not have to change the day one is not."""
    transaction_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("transactions.id"))
    provider: Mapped[str]
    external_ref: Mapped[str]
    """The provider's id for the charge (a Stripe invoice). The idempotency key, in effect."""
    amount: Mapped[Decimal]
    currency: Mapped[str] = mapped_column(server_default="TWD")
    paid_at: Mapped[datetime]
