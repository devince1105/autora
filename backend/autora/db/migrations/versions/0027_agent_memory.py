"""agent memory (T-609)

The small structured trace of an agent's last few runs, which its next run is given. Bounded by
a row cap and an expiry, both enforced on write — the table is a memory, not a log.

Revision ID: a86d889484d2
Revises: 0026
Create Date: 2026-09-20 06:49:35.865309+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_memory",
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("task_id", sa.UUID(), nullable=True),
        sa.Column("run_id", sa.UUID(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("kind IN ('run', 'note')", name=op.f("ck_agent_memory_kind_valid")),
        sa.ForeignKeyConstraint(
            ["agent_id"], ["agents.id"], name=op.f("fk_agent_memory_agent_id_agents")
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_agent_memory_company_id_companies")
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["agent_runs.id"], name=op.f("fk_agent_memory_run_id_agent_runs")
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name=op.f("fk_agent_memory_task_id_tasks")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_memory")),
    )
    op.create_index(op.f("ix_agent_memory_agent_id"), "agent_memory", ["agent_id"], unique=False)
    op.create_index(
        "ix_agent_memory_agent_recent", "agent_memory", ["agent_id", "created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_agent_memory_agent_recent", table_name="agent_memory")
    op.drop_index(op.f("ix_agent_memory_agent_id"), table_name="agent_memory")
    op.drop_table("agent_memory")
