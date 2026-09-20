"""commands log (T-604)

Every attempt to change the company: what was asked, who asked, what the policy said, and what
happened. Refusals are rows too — "what did this company try to do" is not answerable from the
changes alone. The unique idempotency key is what makes a command safe to send twice.

Revision ID: d4da4effc45d
Revises: 0025
Create Date: 2026-09-20 06:30:54.869111+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "commands_log",
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("command", sa.Text(), nullable=False),
        sa.Column("actor", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("role", sa.Text(), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "event_ids", sa.ARRAY(sa.UUID()), server_default=sa.text("'{}'::uuid[]"), nullable=False
        ),
        sa.Column("approval_id", sa.UUID(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "command ~ '^[A-Z][A-Za-z0-9]*$'", name=op.f("ck_commands_log_command_format")
        ),
        sa.CheckConstraint(
            "outcome IN ('done', 'refused', 'awaiting_approval')",
            name=op.f("ck_commands_log_outcome_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["approval_id"], ["approvals.id"], name=op.f("fk_commands_log_approval_id_approvals")
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_commands_log_company_id_companies")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_commands_log")),
        sa.UniqueConstraint("idempotency_key", name=op.f("uq_commands_log_idempotency_key")),
    )
    op.create_index(
        op.f("ix_commands_log_company_id"), "commands_log", ["company_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_commands_log_company_id"), table_name="commands_log")
    op.drop_table("commands_log")
