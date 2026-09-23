"""orders (T-702, D-024)

What somebody said they wanted to buy, before the money left the site. A one-time payment comes
back through the provider knowing only its own trade number; the order is what that number
means — which price, for which reader, for how much.

Revision ID: 0040
Revises: 0039
Create Date: 2026-09-23 08:01:54.560717+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0040"
down_revision: str | None = "0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "orders",
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("price_id", sa.UUID(), nullable=False),
        sa.Column("customer_ref", sa.Text(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("currency", sa.Text(), server_default="TWD", nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("mer_trade_no", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), server_default="PENDING", nullable=False),
        sa.Column("payment_id", sa.UUID(), nullable=True),
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
            "(state <> 'PAID') = (payment_id IS NULL)",
            name=op.f("ck_orders_paid_order_has_its_payment"),
        ),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name=op.f("ck_orders_currency_format")),
        sa.CheckConstraint(
            "provider ~ '^[a-z][a-z0-9_]*$'", name=op.f("ck_orders_provider_format")
        ),
        sa.CheckConstraint(
            "state IN ('PENDING', 'PAID', 'FAILED', 'EXPIRED')", name=op.f("ck_orders_state_valid")
        ),
        sa.CheckConstraint("amount > 0", name=op.f("ck_orders_amount_positive")),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_orders_company_id_companies")
        ),
        sa.ForeignKeyConstraint(
            ["payment_id"], ["payments.id"], name=op.f("fk_orders_payment_id_payments")
        ),
        sa.ForeignKeyConstraint(
            ["price_id"], ["prices.id"], name=op.f("fk_orders_price_id_prices")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_orders")),
        sa.UniqueConstraint(
            "provider", "mer_trade_no", name=op.f("uq_orders_provider_mer_trade_no")
        ),
    )
    op.create_index(op.f("ix_orders_company_id"), "orders", ["company_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_orders_company_id"), table_name="orders")
    op.drop_table("orders")
