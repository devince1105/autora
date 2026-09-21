"""companies.type goes (D-019)

A company is a portfolio of businesses. Which industry it is in belongs to a business unit —
a company running AI Media and AI Education is in two of them, and one column cannot say so.
The field was also never read: nothing branched on it, no page showed it, and every row said
'newsroom', including companies created by the industry-agnostic tests that have no newsroom
at all.

The values are not preserved. They were all the same, none were true of anything, and the
COMPANY_CREATED events already recorded keep whatever they carried.

Revision ID: 0034
Revises: 0033
Create Date: 2026-09-21 00:10:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(op.f("ck_companies_type_valid"), "companies", type_="check")
    op.drop_column("companies", "type")


def downgrade() -> None:
    op.add_column(
        "companies",
        sa.Column("type", sa.Text(), nullable=False, server_default="newsroom"),
    )
    op.create_check_constraint(
        op.f("ck_companies_type_valid"),
        "companies",
        "type IN ('newsroom', 'saas', 'research', 'ecommerce', 'software_studio')",
    )
