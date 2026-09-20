"""organization: business units, departments, roles, products (T-600)

Four tables between a company and its agents (logs/ARCHITECTURE_V2.md), plus the columns that
attribute money and work to a business. Everything here is additive and every new column is
nullable: a company that existed before the organisation did keeps running without an org
chart, and the seed gives it one.

``agents.role`` is deliberately untouched. It stays the string the runtime dispatches on; the
new ``role_id`` only resolves it to a position.

Revision ID: a60620da4750
Revises: 0023
Create Date: 2026-09-20 04:53:07.301919+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "business_units",
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("mission", sa.Text(), nullable=True),
        sa.Column("state", sa.Text(), server_default="PROPOSED", nullable=False),
        sa.Column("kill_criteria", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
        sa.CheckConstraint("key ~ '^[a-z][a-z0-9_]*$'", name=op.f("ck_business_units_key_format")),
        sa.CheckConstraint(
            "state IN ('PROPOSED', 'ACTIVE', 'PAUSED', 'WOUND_DOWN')",
            name=op.f("ck_business_units_state_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_business_units_company_id_companies")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_business_units")),
        sa.UniqueConstraint("company_id", "key", name=op.f("uq_business_units_company_id_key")),
    )
    op.create_index(
        op.f("ix_business_units_company_id"), "business_units", ["company_id"], unique=False
    )
    op.create_table(
        "departments",
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("business_unit_id", sa.UUID(), nullable=True),
        sa.Column("parent_department_id", sa.UUID(), nullable=True),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=True),
        sa.Column("office_zone_key", sa.Text(), nullable=True),
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
        sa.CheckConstraint("key ~ '^[a-z][a-z0-9_]*$'", name=op.f("ck_departments_key_format")),
        sa.CheckConstraint(
            "id <> parent_department_id", name=op.f("ck_departments_not_its_own_parent")
        ),
        sa.ForeignKeyConstraint(
            ["business_unit_id"],
            ["business_units.id"],
            name=op.f("fk_departments_business_unit_id_business_units"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_departments_company_id_companies")
        ),
        sa.ForeignKeyConstraint(
            ["parent_department_id"],
            ["departments.id"],
            name=op.f("fk_departments_parent_department_id_departments"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_departments")),
        sa.UniqueConstraint("company_id", "key", name=op.f("uq_departments_company_id_key")),
    )
    op.create_index(op.f("ix_departments_company_id"), "departments", ["company_id"], unique=False)
    op.create_table(
        "products",
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("business_unit_id", sa.UUID(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("state", sa.Text(), server_default="DRAFT", nullable=False),
        sa.Column("public_url", sa.Text(), nullable=True),
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
        sa.CheckConstraint("key ~ '^[a-z][a-z0-9_]*$'", name=op.f("ck_products_key_format")),
        sa.CheckConstraint(
            "state IN ('DRAFT', 'LIVE', 'RETIRED')", name=op.f("ck_products_state_valid")
        ),
        sa.ForeignKeyConstraint(
            ["business_unit_id"],
            ["business_units.id"],
            name=op.f("fk_products_business_unit_id_business_units"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_products_company_id_companies")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_products")),
        sa.UniqueConstraint("company_id", "key", name=op.f("uq_products_company_id_key")),
    )
    op.create_index(
        op.f("ix_products_business_unit_id"), "products", ["business_unit_id"], unique=False
    )
    op.create_index(op.f("ix_products_company_id"), "products", ["company_id"], unique=False)
    op.create_table(
        "roles",
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("department_id", sa.UUID(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("reports_to_role_id", sa.UUID(), nullable=True),
        sa.Column("is_lead", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("responsibilities", sa.Text(), nullable=True),
        sa.Column(
            "defaults",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
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
        sa.CheckConstraint("key ~ '^[a-z][a-z0-9_]*$'", name=op.f("ck_roles_key_format")),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_roles_company_id_companies")
        ),
        sa.ForeignKeyConstraint(
            ["department_id"], ["departments.id"], name=op.f("fk_roles_department_id_departments")
        ),
        sa.ForeignKeyConstraint(
            ["reports_to_role_id"], ["roles.id"], name=op.f("fk_roles_reports_to_role_id_roles")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_roles")),
        sa.UniqueConstraint("company_id", "key", name=op.f("uq_roles_company_id_key")),
    )
    op.create_index(op.f("ix_roles_company_id"), "roles", ["company_id"], unique=False)
    op.create_index(op.f("ix_roles_department_id"), "roles", ["department_id"], unique=False)
    op.add_column("agents", sa.Column("role_id", sa.UUID(), nullable=True))
    op.add_column("agents", sa.Column("department_id", sa.UUID(), nullable=True))
    op.create_foreign_key(op.f("fk_agents_role_id_roles"), "agents", "roles", ["role_id"], ["id"])
    op.create_foreign_key(
        op.f("fk_agents_department_id_departments"),
        "agents",
        "departments",
        ["department_id"],
        ["id"],
    )
    op.add_column("budgets", sa.Column("business_unit_id", sa.UUID(), nullable=True))
    op.drop_constraint(op.f("uq_budgets_company_id_project_id_period"), "budgets", type_="unique")
    op.create_unique_constraint(
        op.f("uq_budgets_company_id_business_unit_id_project_id_period"),
        "budgets",
        ["company_id", "business_unit_id", "project_id", "period"],
        postgresql_nulls_not_distinct=True,
    )
    op.create_foreign_key(
        op.f("fk_budgets_business_unit_id_business_units"),
        "budgets",
        "business_units",
        ["business_unit_id"],
        ["id"],
    )
    op.add_column("company_goals", sa.Column("business_unit_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f("fk_company_goals_business_unit_id_business_units"),
        "company_goals",
        "business_units",
        ["business_unit_id"],
        ["id"],
    )
    op.add_column("projects", sa.Column("business_unit_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f("fk_projects_business_unit_id_business_units"),
        "projects",
        "business_units",
        ["business_unit_id"],
        ["id"],
    )
    op.add_column("transactions", sa.Column("business_unit_id", sa.UUID(), nullable=True))
    op.add_column("transactions", sa.Column("product_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f("fk_transactions_business_unit_id_business_units"),
        "transactions",
        "business_units",
        ["business_unit_id"],
        ["id"],
    )
    op.create_foreign_key(
        op.f("fk_transactions_product_id_products"),
        "transactions",
        "products",
        ["product_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_transactions_product_id_products"), "transactions", type_="foreignkey"
    )
    op.drop_constraint(
        op.f("fk_transactions_business_unit_id_business_units"), "transactions", type_="foreignkey"
    )
    op.drop_column("transactions", "product_id")
    op.drop_column("transactions", "business_unit_id")
    op.drop_constraint(
        op.f("fk_projects_business_unit_id_business_units"), "projects", type_="foreignkey"
    )
    op.drop_column("projects", "business_unit_id")
    op.drop_constraint(
        op.f("fk_company_goals_business_unit_id_business_units"),
        "company_goals",
        type_="foreignkey",
    )
    op.drop_column("company_goals", "business_unit_id")
    op.drop_constraint(
        op.f("fk_budgets_business_unit_id_business_units"), "budgets", type_="foreignkey"
    )
    op.drop_constraint(
        op.f("uq_budgets_company_id_business_unit_id_project_id_period"), "budgets", type_="unique"
    )
    op.create_unique_constraint(
        op.f("uq_budgets_company_id_project_id_period"),
        "budgets",
        ["company_id", "project_id", "period"],
        postgresql_nulls_not_distinct=True,
    )
    op.drop_column("budgets", "business_unit_id")
    op.drop_constraint(op.f("fk_agents_department_id_departments"), "agents", type_="foreignkey")
    op.drop_constraint(op.f("fk_agents_role_id_roles"), "agents", type_="foreignkey")
    op.drop_column("agents", "department_id")
    op.drop_column("agents", "role_id")
    op.drop_index(op.f("ix_roles_department_id"), table_name="roles")
    op.drop_index(op.f("ix_roles_company_id"), table_name="roles")
    op.drop_table("roles")
    op.drop_index(op.f("ix_products_company_id"), table_name="products")
    op.drop_index(op.f("ix_products_business_unit_id"), table_name="products")
    op.drop_table("products")
    op.drop_index(op.f("ix_departments_company_id"), table_name="departments")
    op.drop_table("departments")
    op.drop_index(op.f("ix_business_units_company_id"), table_name="business_units")
    op.drop_table("business_units")
