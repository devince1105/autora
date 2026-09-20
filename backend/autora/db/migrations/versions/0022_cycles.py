"""cycles (T-601)

The table the whole autonomous loop hangs off, plus the two foreign keys that were deferred
when ``tasks`` and ``workflow_runs`` were created: with them the audit chain
cycle -> workflow -> task is enforced by the database, not by convention (P-10).
``events.cycle_id`` deliberately stays without one, like every other id column on that table:
the log outlives what it points at.

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-20 04:11:01.386917+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cycles",
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("stage", sa.Text(), server_default="PLANNING", nullable=False),
        sa.Column("plan", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("review", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("stage_deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
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
            "(stage = 'DONE') = (ended_at IS NOT NULL)", name=op.f("ck_cycles_ended_at_iff_done")
        ),
        sa.CheckConstraint(
            "stage IN ('PLANNING', 'EXECUTING', 'MEASURING', 'REVIEWING', 'DONE')",
            name=op.f("ck_cycles_stage_valid"),
        ),
        sa.CheckConstraint("seq >= 1", name=op.f("ck_cycles_seq_positive")),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_cycles_company_id_companies")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cycles")),
        sa.UniqueConstraint("company_id", "seq", name=op.f("uq_cycles_company_id_seq")),
    )
    op.create_index(op.f("ix_cycles_company_id"), "cycles", ["company_id"], unique=False)
    op.create_foreign_key(
        op.f("fk_workflow_runs_cycle_id_cycles"), "workflow_runs", "cycles", ["cycle_id"], ["id"]
    )
    op.create_foreign_key(op.f("fk_tasks_cycle_id_cycles"), "tasks", "cycles", ["cycle_id"], ["id"])


def downgrade() -> None:
    op.drop_constraint(op.f("fk_tasks_cycle_id_cycles"), "tasks", type_="foreignkey")
    op.drop_constraint(
        op.f("fk_workflow_runs_cycle_id_cycles"), "workflow_runs", type_="foreignkey"
    )
    op.drop_index(op.f("ix_cycles_company_id"), table_name="cycles")
    op.drop_table("cycles")
