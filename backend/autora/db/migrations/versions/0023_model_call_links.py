"""model calls point at their workflow and cycle (T-602)

What one article cost end to end is the sum over its workflow (AC-S8), and what a day cost is
the sum over its cycle — neither was answerable while a call only knew its task. Existing rows
keep NULL: they were made before there was anything to point at.

Revision ID: ff495478c0d8
Revises: 0022
Create Date: 2026-09-20 04:31:37.213557+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("model_calls", sa.Column("workflow_run_id", sa.UUID(), nullable=True))
    op.add_column("model_calls", sa.Column("cycle_id", sa.UUID(), nullable=True))
    op.create_index("ix_model_calls_cycle", "model_calls", ["cycle_id"], unique=False)
    op.create_index("ix_model_calls_workflow_run", "model_calls", ["workflow_run_id"], unique=False)
    op.create_foreign_key(
        op.f("fk_model_calls_workflow_run_id_workflow_runs"),
        "model_calls",
        "workflow_runs",
        ["workflow_run_id"],
        ["id"],
    )
    op.create_foreign_key(
        op.f("fk_model_calls_cycle_id_cycles"), "model_calls", "cycles", ["cycle_id"], ["id"]
    )


def downgrade() -> None:
    op.drop_constraint(op.f("fk_model_calls_cycle_id_cycles"), "model_calls", type_="foreignkey")
    op.drop_constraint(
        op.f("fk_model_calls_workflow_run_id_workflow_runs"), "model_calls", type_="foreignkey"
    )
    op.drop_index("ix_model_calls_workflow_run", table_name="model_calls")
    op.drop_index("ix_model_calls_cycle", table_name="model_calls")
    op.drop_column("model_calls", "cycle_id")
    op.drop_column("model_calls", "workflow_run_id")
