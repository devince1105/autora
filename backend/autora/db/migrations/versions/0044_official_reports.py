"""official_reports, official_trades: public officials' transaction reports, transcribed (D-051)

The President's OGE Form 278-T reports are scanned PDFs. Each report is transcribed page by page
by a model into official_trades and shown on the stock pages only once a person has approved it
(official_reports.status). Amounts are the reports' ranges; a stock page finds a trade by its
ticker.

Revision ID: 0044
Revises: 0043
Create Date: 2026-09-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0044"
down_revision: str | None = "0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "official_reports",
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("person", sa.Text(), nullable=False),
        sa.Column("filer", sa.Text(), nullable=False),
        sa.Column("form", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("received_on", sa.Date(), nullable=False),
        sa.Column("pages", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("approval_id", sa.UUID(), nullable=True),
        sa.Column("model", sa.Text(), nullable=False),
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
            "status IN ('pending', 'approved', 'rejected')",
            name=op.f("ck_official_reports_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_official_reports_company_id_companies")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_official_reports")),
        sa.UniqueConstraint("company_id", "url", name=op.f("uq_official_reports_company_id_url")),
    )
    op.create_table(
        "official_trades",
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("report_id", sa.UUID(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("ticker", sa.Text(), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("traded_on", sa.Date(), nullable=True),
        sa.Column("late", sa.Boolean(), nullable=True),
        sa.Column("amount_min", sa.Numeric(precision=20, scale=0), nullable=True),
        sa.Column("amount_max", sa.Numeric(precision=20, scale=0), nullable=True),
        sa.Column("amount_text", sa.Text(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_official_trades_company_id_companies")
        ),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["official_reports.id"],
            name=op.f("fk_official_trades_report_id_official_reports"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_official_trades")),
    )
    op.create_index("ix_official_trades_report", "official_trades", ["report_id"], unique=False)
    op.create_index(
        "ix_official_trades_ticker", "official_trades", ["company_id", "ticker"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_official_trades_ticker", table_name="official_trades")
    op.drop_index("ix_official_trades_report", table_name="official_trades")
    op.drop_table("official_trades")
    op.drop_table("official_reports")
