"""official_trades: owner, note — members of Congress's reports (D-051)

A House Periodic Transaction Report says whose each trade is (SP: the member's spouse, JT: joint,
DC: a dependent child) and describes it ("Purchased 100 call options…"). Both are kept, so that a
spouse's option is never shown as the member buying the stock. The President's reports have
neither: null.

Revision ID: 0045
Revises: 0044
Create Date: 2026-09-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0045"
down_revision: str | None = "0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("official_trades", sa.Column("owner", sa.Text(), nullable=True))
    op.add_column("official_trades", sa.Column("note", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("official_trades", "note")
    op.drop_column("official_trades", "owner")
