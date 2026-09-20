"""customers, and the transaction that names one (T-612)

Somebody who pays a business of this company. No name, no email, no card — only the payment
provider's id for them (ARCHITECTURE_V2_1 §7). The one nullable column on transactions is what
makes "what does each customer earn us" answerable on the day the first payment arrives.

Revision ID: 0032
Revises: 0031
Create Date: 2026-09-20 12:07:54.902495+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "customers",
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("business_unit_id", sa.UUID(), nullable=True),
        sa.Column("external_ref", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("churned_at", sa.DateTime(timezone=True), nullable=True),
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
            "kind IN ('subscriber', 'sponsor', 'client')", name=op.f("ck_customers_kind_valid")
        ),
        sa.CheckConstraint(
            "churned_at IS NULL OR churned_at >= acquired_at",
            name=op.f("ck_customers_churned_after_acquired"),
        ),
        sa.ForeignKeyConstraint(
            ["business_unit_id"],
            ["business_units.id"],
            name=op.f("fk_customers_business_unit_id_business_units"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_customers_company_id_companies")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_customers")),
        sa.UniqueConstraint(
            "company_id", "external_ref", name=op.f("uq_customers_company_id_external_ref")
        ),
    )
    op.create_index(op.f("ix_customers_company_id"), "customers", ["company_id"], unique=False)
    op.add_column("transactions", sa.Column("customer_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f("fk_transactions_customer_id_customers"),
        "transactions",
        "customers",
        ["customer_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_transactions_customer_id_customers"), "transactions", type_="foreignkey"
    )
    op.drop_column("transactions", "customer_id")
    op.drop_index(op.f("ix_customers_company_id"), table_name="customers")
    op.drop_table("customers")
