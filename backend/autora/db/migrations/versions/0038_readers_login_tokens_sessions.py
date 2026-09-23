"""readers, login tokens, sessions (D-025)

The only tables in this database that hold a person's identity. An address, and two kinds of
token — a login link and a session — both stored as SHA-256 hashes, so what is written here
cannot be used to sign in as anybody. ``.importlinter`` keeps every layer below from importing
the package that reads them.

Revision ID: 0038
Revises: 0037
Create Date: 2026-09-23 02:26:51.976618+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "readers",
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint("position('@' in email) > 1", name=op.f("ck_readers_email_has_an_at")),
        sa.CheckConstraint("email = lower(email)", name=op.f("ck_readers_email_is_lowercase")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_readers")),
        sa.UniqueConstraint("email", name=op.f("uq_readers_email")),
    )
    op.create_table(
        "login_tokens",
        sa.Column("reader_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "expires_at > created_at", name=op.f("ck_login_tokens_expires_after_created")
        ),
        sa.ForeignKeyConstraint(
            ["reader_id"], ["readers.id"], name=op.f("fk_login_tokens_reader_id_readers")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_login_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_login_tokens_token_hash")),
    )
    op.create_index(op.f("ix_login_tokens_reader_id"), "login_tokens", ["reader_id"], unique=False)
    op.create_table(
        "reader_sessions",
        sa.Column("reader_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "expires_at > created_at", name=op.f("ck_reader_sessions_expires_after_created")
        ),
        sa.ForeignKeyConstraint(
            ["reader_id"], ["readers.id"], name=op.f("fk_reader_sessions_reader_id_readers")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reader_sessions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_reader_sessions_token_hash")),
    )
    op.create_index(
        op.f("ix_reader_sessions_reader_id"), "reader_sessions", ["reader_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_reader_sessions_reader_id"), table_name="reader_sessions")
    op.drop_table("reader_sessions")
    op.drop_index(op.f("ix_login_tokens_reader_id"), table_name="login_tokens")
    op.drop_table("login_tokens")
    op.drop_table("readers")
