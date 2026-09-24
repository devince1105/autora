"""articles: listed, revised_at (D-045)

A published article can now be revised: the site keeps showing its published version while the
new one is written, reviewed and approved. Whether it is on the site becomes its own column,
``listed``, rather than "its state is PUBLISHED"; ``revised_at`` says when a revision went up.
Every existing article was listed if it was published, which is what the old rule said.

Revision ID: 0042
Revises: 0041
Create Date: 2026-09-25 01:10:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0042"
down_revision: str | None = "0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "articles", sa.Column("listed", sa.Boolean(), server_default="true", nullable=False)
    )
    op.add_column("articles", sa.Column("revised_at", sa.DateTime(timezone=True), nullable=True))
    # taken down (D-044) was ARCHIVED; that is what unlisted means now
    op.execute("UPDATE articles SET listed = false WHERE state = 'ARCHIVED'")


def downgrade() -> None:
    op.drop_column("articles", "revised_at")
    op.drop_column("articles", "listed")
