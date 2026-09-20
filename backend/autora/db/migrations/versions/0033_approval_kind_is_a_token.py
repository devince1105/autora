"""approvals.kind is a token, not a fixed list (ARCHITECTURE_V2_1 §9)

The runtime knew what an article was: its approval kinds were an enum with 'article' in it, so
the core carried a newsroom word that import-linter could not see. The five kinds the runtime
asks about are still its own; a domain's kind is now just a lowercase token, checked for shape
and not for meaning — the same arrangement transactions.category has had all along.

Existing rows keep their values: 'article' is still 'article', it is simply the newsroom's word
now rather than the runtime's.

Revision ID: 0033
Revises: 0032
Create Date: 2026-09-20 13:40:00.000000+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD = "ck_approvals_kind_valid"
NEW = "ck_approvals_kind_matches"
PATTERN = "^[a-z][a-z0-9_]*$"


def upgrade() -> None:
    op.drop_constraint(op.f(OLD), "approvals", type_="check")
    op.create_check_constraint(op.f(NEW), "approvals", f"kind ~ '{PATTERN}'")


def downgrade() -> None:
    op.drop_constraint(op.f(NEW), "approvals", type_="check")
    op.create_check_constraint(
        op.f(OLD),
        "approvals",
        "kind IN ('tool_call', 'command', 'project', 'kill', 'strategy', 'article')",
    )
