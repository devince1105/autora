"""documents (T-607)

Short pieces of text written for people: the first kind is the daily summary of a cycle. Unique
per (company, kind, reference), so rewriting a cycle's summary replaces it rather than stacking
copies.

Revision ID: c70b16fc3125
Revises: 0028
Create Date: 2026-09-20 08:56:06.609113+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("ref_type", sa.Text(), nullable=True),
        sa.Column("ref_id", sa.UUID(), nullable=True),
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
            "kind IN ('daily_summary', 'note')", name=op.f("ck_documents_kind_valid")
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_documents_company_id_companies")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
        sa.UniqueConstraint(
            "company_id",
            "kind",
            "ref_type",
            "ref_id",
            name=op.f("uq_documents_company_id_kind_ref_type_ref_id"),
        ),
    )
    op.create_index(op.f("ix_documents_company_id"), "documents", ["company_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_documents_company_id"), table_name="documents")
    op.drop_table("documents")
