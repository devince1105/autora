"""memberships replace subscriptions (D-024)

One payment buys a year of access; nothing is billed again. So the provider-side subscription
(its id, its states, its billing period) goes, and a membership takes its place: one row per
customer and product with an ``expires_at`` each payment extends. The period a payment bought
is kept on the payment itself. Prices lose their provider reference: with one-time payments the
price is the company's own and goes out with each order.

``subscriptions`` existed for a day (0035) and was never written outside tests. This refuses to
drop it if that is not true, rather than drop somebody's rows; the downgrade refuses the same way.

Revision ID: 0037
Revises: 0036
Create Date: 2026-09-22 13:20:29.440471+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _refuse_if_rows(table: str) -> None:
    rows = op.get_bind().execute(sa.text(f"SELECT count(*) FROM {table}")).scalar()
    if rows:
        raise RuntimeError(f"{table} has {rows} row(s); migrate them by hand before dropping it")


def upgrade() -> None:
    _refuse_if_rows("subscriptions")
    op.create_table(
        "memberships",
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("business_unit_id", sa.UUID(), nullable=False),
        sa.Column("customer_id", sa.UUID(), nullable=False),
        sa.Column("product_id", sa.UUID(), nullable=False),
        sa.Column("state", sa.Text(), server_default="ACTIVE", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
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
            "state IN ('ACTIVE', 'EXPIRED')", name=op.f("ck_memberships_state_valid")
        ),
        sa.CheckConstraint(
            "expires_at > started_at", name=op.f("ck_memberships_expires_after_start")
        ),
        sa.ForeignKeyConstraint(
            ["business_unit_id"],
            ["business_units.id"],
            name=op.f("fk_memberships_business_unit_id_business_units"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_memberships_company_id_companies")
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"], ["customers.id"], name=op.f("fk_memberships_customer_id_customers")
        ),
        sa.ForeignKeyConstraint(
            ["product_id"], ["products.id"], name=op.f("fk_memberships_product_id_products")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memberships")),
        sa.UniqueConstraint(
            "customer_id", "product_id", name=op.f("uq_memberships_customer_id_product_id")
        ),
    )
    op.create_index(op.f("ix_memberships_company_id"), "memberships", ["company_id"], unique=False)
    op.create_index(
        op.f("ix_memberships_customer_id"), "memberships", ["customer_id"], unique=False
    )
    op.add_column("payments", sa.Column("price_id", sa.UUID(), nullable=True))
    op.add_column("payments", sa.Column("membership_id", sa.UUID(), nullable=True))
    op.add_column("payments", sa.Column("grants_from", sa.DateTime(timezone=True), nullable=True))
    op.add_column("payments", sa.Column("grants_until", sa.DateTime(timezone=True), nullable=True))
    op.drop_index(op.f("ix_payments_subscription_id"), table_name="payments")
    op.create_index(op.f("ix_payments_membership_id"), "payments", ["membership_id"], unique=False)
    op.drop_constraint(
        op.f("fk_payments_subscription_id_subscriptions"), "payments", type_="foreignkey"
    )
    op.create_foreign_key(
        op.f("fk_payments_price_id_prices"), "payments", "prices", ["price_id"], ["id"]
    )
    op.create_foreign_key(
        op.f("fk_payments_membership_id_memberships"),
        "payments",
        "memberships",
        ["membership_id"],
        ["id"],
    )
    op.drop_column("payments", "subscription_id")
    op.drop_index(op.f("ix_subscriptions_company_id"), table_name="subscriptions")
    op.drop_index(op.f("ix_subscriptions_customer_id"), table_name="subscriptions")
    op.drop_table("subscriptions")
    op.create_check_constraint(
        op.f("ck_payments_grant_complete"),
        "payments",
        "(membership_id IS NULL) = (grants_from IS NULL) "
        "AND (membership_id IS NULL) = (grants_until IS NULL) "
        "AND (grants_until IS NULL OR grants_until > grants_from)",
    )
    op.drop_constraint(op.f("uq_prices_company_id_provider_external_ref"), "prices", type_="unique")
    op.drop_column("prices", "provider")
    op.drop_column("prices", "external_ref")


def downgrade() -> None:
    _refuse_if_rows("memberships")
    op.drop_constraint(op.f("ck_payments_grant_complete"), "payments", type_="check")
    op.add_column(
        "prices", sa.Column("external_ref", sa.TEXT(), autoincrement=False, nullable=True)
    )
    op.add_column(
        "prices", sa.Column("provider", sa.TEXT(), server_default="stripe", nullable=False)
    )
    op.alter_column("prices", "provider", server_default=None)
    op.create_check_constraint(
        op.f("ck_prices_provider_format"), "prices", "provider ~ '^[a-z][a-z0-9_]*$'"
    )
    op.create_unique_constraint(
        op.f("uq_prices_company_id_provider_external_ref"),
        "prices",
        ["company_id", "provider", "external_ref"],
        postgresql_nulls_not_distinct=False,
    )
    op.create_table(
        "subscriptions",
        sa.Column("company_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("business_unit_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("customer_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("product_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("price_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("state", sa.TEXT(), autoincrement=False, nullable=False),
        sa.Column("provider", sa.TEXT(), autoincrement=False, nullable=False),
        sa.Column("external_ref", sa.TEXT(), autoincrement=False, nullable=False),
        sa.Column(
            "started_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False
        ),
        sa.Column(
            "current_period_end",
            postgresql.TIMESTAMP(timezone=True),
            autoincrement=False,
            nullable=True,
        ),
        sa.Column(
            "canceled_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True
        ),
        sa.Column("id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            autoincrement=False,
            nullable=False,
        ),
        sa.CheckConstraint(
            "provider ~ '^[a-z][a-z0-9_]*$'::text", name=op.f("ck_subscriptions_provider_format")
        ),
        sa.CheckConstraint(
            "state <> 'CANCELED'::text OR canceled_at IS NOT NULL",
            name=op.f("ck_subscriptions_canceled_has_a_date"),
        ),
        sa.CheckConstraint(
            "state = ANY (ARRAY['TRIALING'::text, 'ACTIVE'::text, 'PAST_DUE'::text, 'CANCELED'::text])",
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
            postgresql_include=[],
            postgresql_nulls_not_distinct=False,
        ),
    )
    op.create_index(
        op.f("ix_subscriptions_customer_id"), "subscriptions", ["customer_id"], unique=False
    )
    op.create_index(
        op.f("ix_subscriptions_company_id"), "subscriptions", ["company_id"], unique=False
    )
    op.add_column(
        "payments", sa.Column("subscription_id", sa.UUID(), autoincrement=False, nullable=True)
    )
    op.drop_constraint(
        op.f("fk_payments_membership_id_memberships"), "payments", type_="foreignkey"
    )
    op.drop_constraint(op.f("fk_payments_price_id_prices"), "payments", type_="foreignkey")
    op.create_foreign_key(
        op.f("fk_payments_subscription_id_subscriptions"),
        "payments",
        "subscriptions",
        ["subscription_id"],
        ["id"],
    )
    op.drop_index(op.f("ix_payments_membership_id"), table_name="payments")
    op.create_index(
        op.f("ix_payments_subscription_id"), "payments", ["subscription_id"], unique=False
    )
    op.drop_column("payments", "grants_until")
    op.drop_column("payments", "grants_from")
    op.drop_column("payments", "membership_id")
    op.drop_column("payments", "price_id")
    op.drop_index(op.f("ix_memberships_customer_id"), table_name="memberships")
    op.drop_index(op.f("ix_memberships_company_id"), table_name="memberships")
    op.drop_table("memberships")
