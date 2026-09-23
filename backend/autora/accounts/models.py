"""Who the readers are (D-024, D-025).

**The only place in this system that holds a person's identity.** An email address lives here
and nowhere else: not in the ledger, not on customers, not in any event payload, not in
anything an agent can read. ``.importlinter`` keeps it that way — no layer below may import
this package — and the company knows a reader only as ``customers.external_ref``, the string
``reader:<id>``.

Three tables, and each is as small as it can be:

- ``readers``: an address and when it was last seen. No name, no password.
- ``login_tokens``: a one-time link, stored hashed, valid for minutes.
- ``reader_sessions``: the cookie's other half, also stored hashed, valid for weeks.

Tokens are hashed because these rows are a way into somebody's account: a leaked database dump
should not be a leaked set of live sessions.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from autora.db.base import Base, CreatedAtMixin, IdMixin, TimestampMixin


class Reader(IdMixin, TimestampMixin, Base):
    """Somebody who signs in to read. Identified by their address, nothing else."""

    __tablename__ = "readers"
    __table_args__ = (
        UniqueConstraint("email"),
        CheckConstraint("email = lower(email)", name="email_is_lowercase"),
        CheckConstraint("position('@' in email) > 1", name="email_has_an_at"),
    )

    email: Mapped[str]
    """Lowercased on the way in, so one person is one row however they typed it."""
    last_seen_at: Mapped[datetime | None]


class LoginToken(IdMixin, CreatedAtMixin, Base):
    """A link sent to an address: valid once, for minutes (D-025)."""

    __tablename__ = "login_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash"),
        CheckConstraint("expires_at > created_at", name="expires_after_created"),
    )

    reader_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("readers.id"), index=True)
    token_hash: Mapped[str]
    """SHA-256 of the token in the link. The token itself is only ever in the email."""
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None]
    """Set the moment it is redeemed, so the same link cannot be used twice."""


class ReaderSession(IdMixin, CreatedAtMixin, Base):
    """A signed-in browser. The cookie holds the token; this holds its hash."""

    __tablename__ = "reader_sessions"
    __table_args__ = (
        UniqueConstraint("token_hash"),
        CheckConstraint("expires_at > created_at", name="expires_after_created"),
    )

    reader_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("readers.id"), index=True)
    token_hash: Mapped[str]
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]
    """Signing out. The row stays: when a session ended is worth knowing."""
