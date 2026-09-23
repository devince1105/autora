"""articles can be members only (D-025)

Most articles stay free; a few are for members. The column is the whole paywall on the data
side — who may read is then a question about the reader's membership, asked when the page is
served.

Revision ID: 0039
Revises: 0038
Create Date: 2026-09-23 02:28:17.163830+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0039"
down_revision: str | None = "0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("articles", sa.Column("access", sa.Text(), server_default="free", nullable=False))
    op.create_check_constraint(
        op.f("ck_articles_access_valid"), "articles", "access IN ('free', 'members')"
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_articles_access_valid"), "articles", type_="check")
    op.drop_column("articles", "access")
