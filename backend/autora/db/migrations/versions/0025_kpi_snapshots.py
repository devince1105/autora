"""kpi snapshots (T-603)

What a company, a business or a project achieved in one cycle, and what it cost. The unique key
is (cycle, scope, business unit, project) with NULLs treated as equal, so running the
measurement again writes the same row rather than a second one.

Revision ID: 536fad30bb3f
Revises: 0024
Create Date: 2026-09-20 05:37:04.644864+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "kpi_snapshots",
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("cycle_id", sa.UUID(), nullable=True),
        sa.Column("business_unit_id", sa.UUID(), nullable=True),
        sa.Column("project_id", sa.UUID(), nullable=True),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column(
            "metrics",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "scope IN ('company', 'business_unit', 'project')",
            name=op.f("ck_kpi_snapshots_scope_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["business_unit_id"],
            ["business_units.id"],
            name=op.f("fk_kpi_snapshots_business_unit_id_business_units"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_kpi_snapshots_company_id_companies")
        ),
        sa.ForeignKeyConstraint(
            ["cycle_id"], ["cycles.id"], name=op.f("fk_kpi_snapshots_cycle_id_cycles")
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_kpi_snapshots_project_id_projects")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_kpi_snapshots")),
        sa.UniqueConstraint(
            "cycle_id",
            "scope",
            "business_unit_id",
            "project_id",
            name=op.f("uq_kpi_snapshots_cycle_id_scope_business_unit_id_project_id"),
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(
        op.f("ix_kpi_snapshots_company_id"), "kpi_snapshots", ["company_id"], unique=False
    )
    op.create_index(op.f("ix_kpi_snapshots_cycle_id"), "kpi_snapshots", ["cycle_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_kpi_snapshots_cycle_id"), table_name="kpi_snapshots")
    op.drop_index(op.f("ix_kpi_snapshots_company_id"), table_name="kpi_snapshots")
    op.drop_table("kpi_snapshots")
