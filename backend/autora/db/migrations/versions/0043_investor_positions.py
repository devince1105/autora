"""investor_positions: the tracked investors' 13F positions, for the stock pages (D-049)

One row per position in each tracked investor's latest original 13F-HR, compared with the quarter
before (sold-out positions included), written by ``newsroom.holdings.refresh_holdings``. A stock
page reads it by CUSIP: who holds the stock and what they did with it this quarter.

Revision ID: 0043
Revises: 0042
Create Date: 2026-09-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0043"
down_revision: str | None = "0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "investor_positions",
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("filer", sa.Text(), nullable=False),
        sa.Column("cik", sa.Text(), nullable=False),
        sa.Column("accession", sa.Text(), nullable=False),
        sa.Column("period", sa.Date(), nullable=False),
        sa.Column("previous_period", sa.Date(), nullable=True),
        sa.Column("cusip", sa.Text(), nullable=False),
        sa.Column("issuer", sa.Text(), nullable=False),
        sa.Column("title_of_class", sa.Text(), nullable=False),
        sa.Column("put_call", sa.Text(), server_default="", nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=20, scale=0), nullable=False),
        sa.Column("value_usd", sa.Numeric(precision=20, scale=0), nullable=False),
        sa.Column("previous_amount", sa.Numeric(precision=20, scale=0), nullable=False),
        sa.Column("previous_value_usd", sa.Numeric(precision=20, scale=0), nullable=False),
        sa.Column("change", sa.Text(), nullable=False),
        sa.Column("portfolio_value_usd", sa.Numeric(precision=20, scale=0), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "change IN ('new', 'increased', 'decreased', 'unchanged', 'sold_out')",
            name=op.f("ck_investor_positions_change_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_investor_positions_company_id_companies"),
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["sources.id"], name=op.f("fk_investor_positions_source_id_sources")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_investor_positions")),
    )
    op.create_index(
        "ix_investor_positions_cusip", "investor_positions", ["company_id", "cusip"], unique=False
    )
    op.create_index(
        "ix_investor_positions_source", "investor_positions", ["source_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_investor_positions_source", table_name="investor_positions")
    op.drop_index("ix_investor_positions_cusip", table_name="investor_positions")
    op.drop_table("investor_positions")
