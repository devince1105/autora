"""opportunities, signals and business proposals (T-611)

How a company decides which business to be in: something that might be a business, what was
observed about it, and what we would actually do about it. Plus one nullable column on
projects, which is the whole of the business loop's execution model.

Revision ID: 0031
Revises: 0030
Create Date: 2026-09-20 10:01:33.591172+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "opportunities",
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("thesis", sa.Text(), nullable=True),
        sa.Column("market", sa.Text(), nullable=True),
        sa.Column("state", sa.Text(), server_default="DISCOVERED", nullable=False),
        sa.Column("score", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column("discovered_by_run_id", sa.UUID(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("business_unit_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("key ~ '^[a-z][a-z0-9_]*$'", name=op.f("ck_opportunities_key_format")),
        sa.CheckConstraint(
            "state <> 'APPROVED' OR business_unit_id IS NOT NULL",
            name=op.f("ck_opportunities_approved_opportunity_became_a_business"),
        ),
        sa.CheckConstraint(
            "state IN ('DISCOVERED', 'EVALUATING', 'VALIDATING', 'APPROVED', 'REJECTED', 'EXPIRED')",
            name=op.f("ck_opportunities_state_valid"),
        ),
        sa.CheckConstraint(
            "state NOT IN ('APPROVED', 'REJECTED', 'EXPIRED') OR decided_at IS NOT NULL",
            name=op.f("ck_opportunities_decided_opportunity_has_a_date"),
        ),
        sa.ForeignKeyConstraint(
            ["business_unit_id"],
            ["business_units.id"],
            name=op.f("fk_opportunities_business_unit_id_business_units"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_opportunities_company_id_companies")
        ),
        sa.ForeignKeyConstraint(
            ["discovered_by_run_id"],
            ["agent_runs.id"],
            name=op.f("fk_opportunities_discovered_by_run_id_agent_runs"),
            use_alter=True,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_opportunities")),
        sa.UniqueConstraint("company_id", "key", name=op.f("uq_opportunities_company_id_key")),
    )
    op.create_index(
        op.f("ix_opportunities_company_id"), "opportunities", ["company_id"], unique=False
    )
    op.create_table(
        "opportunity_signals",
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("opportunity_id", sa.UUID(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("metric", sa.Text(), nullable=True),
        sa.Column("value", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence_ref", sa.Text(), nullable=True),
        sa.Column("recorded_by_run_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_opportunity_signals_company_id_companies"),
        ),
        sa.ForeignKeyConstraint(
            ["opportunity_id"],
            ["opportunities.id"],
            name=op.f("fk_opportunity_signals_opportunity_id_opportunities"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["recorded_by_run_id"],
            ["agent_runs.id"],
            name=op.f("fk_opportunity_signals_recorded_by_run_id_agent_runs"),
            use_alter=True,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_opportunity_signals")),
    )
    op.create_index(
        op.f("ix_opportunity_signals_company_id"),
        "opportunity_signals",
        ["company_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_opportunity_signals_opportunity_id"),
        "opportunity_signals",
        ["opportunity_id"],
        unique=False,
    )
    op.create_table(
        "business_proposals",
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("opportunity_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("state", sa.Text(), server_default="DRAFT", nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("business_model", sa.Text(), nullable=True),
        sa.Column("target_market", sa.Text(), nullable=True),
        sa.Column("target_customer", sa.Text(), nullable=True),
        sa.Column("proposed_product", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("expected_revenue_model", sa.Text(), nullable=True),
        sa.Column("expected_margin", sa.Numeric(precision=6, scale=4), nullable=True),
        sa.Column("estimated_startup_cost", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("estimated_monthly_cost", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("required_agents", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("required_capabilities", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("risks", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("validation_plan", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("kill_criteria", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("authored_by_run_id", sa.UUID(), nullable=True),
        sa.Column("approval_id", sa.UUID(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("validation_project_id", sa.UUID(), nullable=True),
        sa.Column("business_unit_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "state IN ('DRAFT', 'SUBMITTED', 'APPROVED', 'REJECTED', 'SUPERSEDED')",
            name=op.f("ck_business_proposals_state_valid"),
        ),
        sa.CheckConstraint(
            "state NOT IN ('APPROVED', 'REJECTED') OR decided_at IS NOT NULL",
            name=op.f("ck_business_proposals_decided_proposal_has_a_date"),
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_business_proposals_version_positive")),
        sa.ForeignKeyConstraint(
            ["approval_id"],
            ["approvals.id"],
            name=op.f("fk_business_proposals_approval_id_approvals"),
        ),
        sa.ForeignKeyConstraint(
            ["authored_by_run_id"],
            ["agent_runs.id"],
            name=op.f("fk_business_proposals_authored_by_run_id_agent_runs"),
            use_alter=True,
        ),
        sa.ForeignKeyConstraint(
            ["business_unit_id"],
            ["business_units.id"],
            name=op.f("fk_business_proposals_business_unit_id_business_units"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_business_proposals_company_id_companies"),
        ),
        sa.ForeignKeyConstraint(
            ["opportunity_id"],
            ["opportunities.id"],
            name=op.f("fk_business_proposals_opportunity_id_opportunities"),
        ),
        sa.ForeignKeyConstraint(
            ["validation_project_id"],
            ["projects.id"],
            name=op.f("fk_business_proposals_validation_project_id_projects"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_business_proposals")),
        sa.UniqueConstraint(
            "opportunity_id", "version", name=op.f("uq_business_proposals_opportunity_id_version")
        ),
    )
    op.create_index(
        op.f("ix_business_proposals_company_id"), "business_proposals", ["company_id"], unique=False
    )
    op.create_index(
        op.f("ix_business_proposals_opportunity_id"),
        "business_proposals",
        ["opportunity_id"],
        unique=False,
    )
    op.add_column("projects", sa.Column("opportunity_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f("fk_projects_opportunity_id_opportunities"),
        "projects",
        "opportunities",
        ["opportunity_id"],
        ["id"],
    )
    # the three links back to the run that wrote the row, added after both tables exist
    for table, column in (
        ("opportunities", "discovered_by_run_id"),
        ("opportunity_signals", "recorded_by_run_id"),
        ("business_proposals", "authored_by_run_id"),
    ):
        op.create_foreign_key(
            op.f(f"fk_{table}_{column}_agent_runs"),
            table,
            "agent_runs",
            [column],
            ["id"],
            use_alter=True,
        )


def downgrade() -> None:
    for table, column in (
        ("opportunities", "discovered_by_run_id"),
        ("opportunity_signals", "recorded_by_run_id"),
        ("business_proposals", "authored_by_run_id"),
    ):
        op.drop_constraint(op.f(f"fk_{table}_{column}_agent_runs"), table, type_="foreignkey")
    op.drop_constraint(
        op.f("fk_projects_opportunity_id_opportunities"), "projects", type_="foreignkey"
    )
    op.drop_column("projects", "opportunity_id")
    op.drop_index(op.f("ix_business_proposals_opportunity_id"), table_name="business_proposals")
    op.drop_index(op.f("ix_business_proposals_company_id"), table_name="business_proposals")
    op.drop_table("business_proposals")
    op.drop_index(op.f("ix_opportunity_signals_opportunity_id"), table_name="opportunity_signals")
    op.drop_index(op.f("ix_opportunity_signals_company_id"), table_name="opportunity_signals")
    op.drop_table("opportunity_signals")
    op.drop_index(op.f("ix_opportunities_company_id"), table_name="opportunities")
    op.drop_table("opportunities")
