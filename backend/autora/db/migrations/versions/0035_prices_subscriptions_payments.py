"""prices, subscriptions, payments (T-701, D-022)

The first revenue model is a subscription sold through a payment provider. A price is what a
product costs per interval; a subscription is a customer paying it; a payment is money the
provider says arrived, and the ledger row it became. Payments are append-only, like the ledger.

Revision ID: 0035
Revises: 0034
Create Date: 2026-09-22 12:26:56.047289+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "prices",
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("product_id", sa.UUID(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("currency", sa.Text(), server_default="USD", nullable=False),
        sa.Column("interval", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), server_default="ACTIVE", nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("external_ref", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name=op.f("ck_prices_currency_format")),
        sa.CheckConstraint("interval IN ('month', 'year')", name=op.f("ck_prices_interval_valid")),
        sa.CheckConstraint(
            "provider ~ '^[a-z][a-z0-9_]*$'", name=op.f("ck_prices_provider_format")
        ),
        sa.CheckConstraint("state IN ('ACTIVE', 'RETIRED')", name=op.f("ck_prices_state_valid")),
        sa.CheckConstraint("amount > 0", name=op.f("ck_prices_amount_positive")),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_prices_company_id_companies")
        ),
        sa.ForeignKeyConstraint(
            ["product_id"], ["products.id"], name=op.f("fk_prices_product_id_products")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_prices")),
        sa.UniqueConstraint(
            "company_id",
            "provider",
            "external_ref",
            name=op.f("uq_prices_company_id_provider_external_ref"),
        ),
    )
    op.create_index(op.f("ix_prices_company_id"), "prices", ["company_id"], unique=False)
    op.create_index(op.f("ix_prices_product_id"), "prices", ["product_id"], unique=False)
    op.create_table(
        "subscriptions",
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("business_unit_id", sa.UUID(), nullable=False),
        sa.Column("customer_id", sa.UUID(), nullable=False),
        sa.Column("product_id", sa.UUID(), nullable=False),
        sa.Column("price_id", sa.UUID(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("external_ref", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "provider ~ '^[a-z][a-z0-9_]*$'", name=op.f("ck_subscriptions_provider_format")
        ),
        sa.CheckConstraint(
            "state <> 'CANCELED' OR canceled_at IS NOT NULL",
            name=op.f("ck_subscriptions_canceled_has_a_date"),
        ),
        sa.CheckConstraint(
            "state IN ('TRIALING', 'ACTIVE', 'PAST_DUE', 'CANCELED')",
            name=op.f("ck_subscriptions_state_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["business_unit_id"],
            ["business_units.id"],
            name=op.f("fk_subscriptions_business_unit_id_business_units"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_subscriptions_company_id_companies")
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"], ["customers.id"], name=op.f("fk_subscriptions_customer_id_customers")
        ),
        sa.ForeignKeyConstraint(
            ["price_id"], ["prices.id"], name=op.f("fk_subscriptions_price_id_prices")
        ),
        sa.ForeignKeyConstraint(
            ["product_id"], ["products.id"], name=op.f("fk_subscriptions_product_id_products")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_subscriptions")),
        sa.UniqueConstraint(
            "company_id",
            "provider",
            "external_ref",
            name=op.f("uq_subscriptions_company_id_provider_external_ref"),
        ),
    )
    op.create_index(
        op.f("ix_subscriptions_company_id"), "subscriptions", ["company_id"], unique=False
    )
    op.create_index(
        op.f("ix_subscriptions_customer_id"), "subscriptions", ["customer_id"], unique=False
    )
    op.create_table(
        "payments",
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("business_unit_id", sa.UUID(), nullable=False),
        sa.Column("customer_id", sa.UUID(), nullable=False),
        sa.Column("subscription_id", sa.UUID(), nullable=True),
        sa.Column("transaction_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("external_ref", sa.Text(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("currency", sa.Text(), server_default="USD", nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name=op.f("ck_payments_currency_format")),
        sa.CheckConstraint(
            "provider ~ '^[a-z][a-z0-9_]*$'", name=op.f("ck_payments_provider_format")
        ),
        sa.CheckConstraint("amount > 0", name=op.f("ck_payments_amount_positive")),
        sa.ForeignKeyConstraint(
            ["business_unit_id"],
            ["business_units.id"],
            name=op.f("fk_payments_business_unit_id_business_units"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_payments_company_id_companies")
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"], ["customers.id"], name=op.f("fk_payments_customer_id_customers")
        ),
        sa.ForeignKeyConstraint(
            ["subscription_id"],
            ["subscriptions.id"],
            name=op.f("fk_payments_subscription_id_subscriptions"),
        ),
        sa.ForeignKeyConstraint(
            ["transaction_id"],
            ["transactions.id"],
            name=op.f("fk_payments_transaction_id_transactions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payments")),
        sa.UniqueConstraint(
            "provider", "external_ref", name=op.f("uq_payments_provider_external_ref")
        ),
        sa.UniqueConstraint("transaction_id", name=op.f("uq_payments_transaction_id")),
    )
    op.create_index(op.f("ix_payments_company_id"), "payments", ["company_id"], unique=False)
    op.create_index(op.f("ix_payments_customer_id"), "payments", ["customer_id"], unique=False)
    op.create_index(
        op.f("ix_payments_subscription_id"), "payments", ["subscription_id"], unique=False
    )
    op.execute(
        "CREATE TRIGGER payments_append_only BEFORE UPDATE OR DELETE ON payments "
        "FOR EACH ROW EXECUTE FUNCTION autora_forbid_mutation()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS payments_append_only ON payments")
    op.drop_index(op.f("ix_payments_subscription_id"), table_name="payments")
    op.drop_index(op.f("ix_payments_customer_id"), table_name="payments")
    op.drop_index(op.f("ix_payments_company_id"), table_name="payments")
    op.drop_table("payments")
    op.drop_index(op.f("ix_subscriptions_customer_id"), table_name="subscriptions")
    op.drop_index(op.f("ix_subscriptions_company_id"), table_name="subscriptions")
    op.drop_table("subscriptions")
    op.drop_index(op.f("ix_prices_product_id"), table_name="prices")
    op.drop_index(op.f("ix_prices_company_id"), table_name="prices")
    op.drop_table("prices")
