"""cycles.governance (T-610)

What the deterministic rules evaluated on that day, and what they did. Recorded even when
nothing fired, so "the rules ran and found nothing wrong" is distinguishable from "the rules
never ran" (AC-14).

Revision ID: 0030
Revises: 0029
Create Date: 2026-09-20 10:10:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "cycles",
        sa.Column("governance", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("cycles", "governance")
