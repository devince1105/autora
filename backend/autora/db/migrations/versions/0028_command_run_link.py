"""commands know their run (T-605a)

Which run asked for a change. It completes the audit chain — cycle, plan, command, work — and
it is what lets an agent's written plan be checked against what it actually asked the company
for, rather than taken on trust.

Revision ID: 844afefb4884
Revises: 0027
Create Date: 2026-09-20 07:08:34.450233+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("commands_log", sa.Column("task_id", sa.UUID(), nullable=True))
    op.add_column("commands_log", sa.Column("run_id", sa.UUID(), nullable=True))
    op.create_index(op.f("ix_commands_log_run_id"), "commands_log", ["run_id"], unique=False)
    op.create_foreign_key(
        op.f("fk_commands_log_task_id_tasks"), "commands_log", "tasks", ["task_id"], ["id"]
    )
    op.create_foreign_key(
        op.f("fk_commands_log_run_id_agent_runs"), "commands_log", "agent_runs", ["run_id"], ["id"]
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_commands_log_run_id_agent_runs"), "commands_log", type_="foreignkey"
    )
    op.drop_constraint(op.f("fk_commands_log_task_id_tasks"), "commands_log", type_="foreignkey")
    op.drop_index(op.f("ix_commands_log_run_id"), table_name="commands_log")
    op.drop_column("commands_log", "run_id")
    op.drop_column("commands_log", "task_id")
