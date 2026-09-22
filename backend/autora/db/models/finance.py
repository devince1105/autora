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


class MembershipState(StrEnum):
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"


class Price(IdMixin, TimestampMixin, Base):
    """What a product costs, and how long one payment of it lasts (T-701, D-024).

    Not a column on ``products``: a price changes and a product does not. Raising the yearly
    price retires one row and adds another, so every payment keeps pointing at the price it was
    sold at. The price is the company's own — with one-time payments there is nothing at the
    provider to point at, the amount goes out with each order.
    """

    __tablename__ = "prices"
    __table_args__ = (
        check_in("interval", PriceInterval),
        check_in("state", PriceState),
        check_regex("currency", "^[A-Z]{3}$"),
        CheckConstraint("amount > 0", name="amount_positive"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), index=True)
    amount: Mapped[Decimal]
    currency: Mapped[str] = mapped_column(server_default="TWD")
    interval: Mapped[str]
    """How long one payment buys: a month or a year of access."""
    state: Mapped[str] = mapped_column(server_default=PriceState.ACTIVE.value)


class Membership(IdMixin, TimestampMixin, Base):
    """A customer's access to a product, and until when it lasts (T-701, D-024).

    Bought, not billed: each payment extends ``expires_at`` by the price's interval, and nothing
    charges anybody again. One row per customer and product — renewing extends it, coming back
    after it lapsed reopens it; the periods each payment bought are on the payments.

    Whether somebody may read is ``expires_at > now``, answered at the moment it is asked.
    ``state`` is bookkeeping for the company: EXPIRED is written once a day by the cycle, and it
    is what churns the customer.
    """

    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("customer_id", "product_id"),
        check_in("state", MembershipState),
        CheckConstraint("expires_at > started_at", name="expires_after_start"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    business_unit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("business_units.id"))
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id"), index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"))
    state: Mapped[str] = mapped_column(server_default=MembershipState.ACTIVE.value)
    started_at: Mapped[datetime]
    """When the current unbroken stretch began: the first purchase, or the return after a lapse."""
    expires_at: Mapped[datetime]


class Payment(IdMixin, CreatedAtMixin, Base):
    """Money a provider says arrived, the ledger row it became, and what it bought (D-024).

    Written only by an integration, never by an agent (platform/06 §1). Append-only like the
    ledger it feeds: a refund will be its own row, not an edit to this one. The unique
    ``(provider, external_ref)`` is what makes a notification delivered twice one payment.
    """

    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("provider", "external_ref"),
        UniqueConstraint("transaction_id"),
        check_regex("currency", "^[A-Z]{3}$"),
        check_regex("provider", "^[a-z][a-z0-9_]*$"),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint(
            "(membership_id IS NULL) = (grants_from IS NULL) "
            "AND (membership_id IS NULL) = (grants_until IS NULL) "
            "AND (grants_until IS NULL OR grants_until > grants_from)",
            name="grant_complete",
        ),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    business_unit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("business_units.id"))
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id"), index=True)
    price_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("prices.id"))
    membership_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("memberships.id"), index=True
    )
    """NULL for a payment that bought no access. Every payment today buys some, but the ledger
    should not have to change the day one does not."""
    grants_from: Mapped[datetime | None]
    grants_until: Mapped[datetime | None]
    """The stretch of access this payment bought. The history a membership row does not keep."""
    transaction_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("transactions.id"))
    provider: Mapped[str]
    """Which provider took the money, as a token (``payuni``). The core never branches on it."""
    external_ref: Mapped[str]
    """The provider's id for the charge (PAYUNi's trade number). The idempotency key, in effect."""
    amount: Mapped[Decimal]
    currency: Mapped[str] = mapped_column(server_default="TWD")
    paid_at: Mapped[datetime]
