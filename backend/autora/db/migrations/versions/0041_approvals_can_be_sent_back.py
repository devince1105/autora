"""approvals can be sent back (D-044)

A person deciding an article could approve it or reject it (drop the story); now they can also
send it back with changes asked for. That is a third decided state, RETURNED, and the check that
a decided approval says who decided and when counts it as decided.

Revision ID: 0041
Revises: 0040
Create Date: 2026-09-25 00:30:00+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0041"
down_revision: str | None = "0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(op.f("ck_approvals_state_valid"), "approvals", type_="check")
    op.create_check_constraint(
        op.f("ck_approvals_state_valid"),
        "approvals",
        "state IN ('PENDING', 'APPROVED', 'REJECTED', 'RETURNED', 'EXPIRED')",
    )
    op.drop_constraint(
        op.f("ck_approvals_decided_iff_approved_or_rejected"), "approvals", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_approvals_decided_iff_decided"),
        "approvals",
        "(state IN ('APPROVED', 'REJECTED', 'RETURNED')) = "
        "(decided_at IS NOT NULL AND decided_by IS NOT NULL)",
    )


def downgrade() -> None:
    returned = (
        op.get_bind()
        .exec_driver_sql("SELECT count(*) FROM approvals WHERE state = 'RETURNED'")
        .scalar()
    )
    if returned:
        raise RuntimeError(f"{returned} approval(s) were sent back; decide what they become first")
    op.drop_constraint(op.f("ck_approvals_decided_iff_decided"), "approvals", type_="check")
    op.create_check_constraint(
        op.f("ck_approvals_decided_iff_approved_or_rejected"),
        "approvals",
        "(state IN ('APPROVED', 'REJECTED')) = (decided_at IS NOT NULL AND decided_by IS NOT NULL)",
    )
    op.drop_constraint(op.f("ck_approvals_state_valid"), "approvals", type_="check")
    op.create_check_constraint(
        op.f("ck_approvals_state_valid"),
        "approvals",
        "state IN ('PENDING', 'APPROVED', 'REJECTED', 'EXPIRED')",
    )
