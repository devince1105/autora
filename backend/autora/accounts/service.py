"""Signing in without a password (D-025).

The whole flow: somebody types their address, we email them a link, they open it, they have a
session. Nothing to remember, nothing to leak but a mailbox.

The rules that make it safe enough to be worth having:

- **The token is random and stored hashed.** What is in the database cannot be used to sign in.
- **A link is valid once, for fifteen minutes.** Redeeming marks it used in the same
  transaction that creates the session, so a link that is clicked twice signs in once.
- **Asking says nothing.** Requesting a link answers the same way whether or not the address
  has ever been seen; a stranger cannot use the form to learn who reads this site.
- **A reader is made when they first ask**, not when they first pay. Buying is what gives them
  a customer (D-024); reading is what gives them a reader.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autora.accounts.models import LoginToken, Reader, ReaderSession

ADDRESS_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
"""What counts as an address here: no spaces, one @, a dot after it. Deliberately loose —
the real check is whether a link sent to it arrives."""

LINK_VALID_FOR = timedelta(minutes=15)
SESSION_VALID_FOR = timedelta(days=60)
SESSION_COOKIE = "autora_reader"


class AccountError(Exception):
    pass


@dataclass(frozen=True)
class Link:
    """What to email somebody. The token is in memory here and nowhere else."""

    reader: Reader
    token: str
    expires_at: datetime


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def normalise(email: str) -> str:
    email = email.strip().lower()
    if len(email) > 254 or not re.match(ADDRESS_PATTERN, email):
        raise AccountError(f"{email!r} is not an address a link can be sent to")
    return email


async def by_email(session: AsyncSession, email: str) -> Reader | None:
    return await session.scalar(select(Reader).where(Reader.email == normalise(email)))


async def request_link(session: AsyncSession, email: str, *, now: datetime | None = None) -> Link:
    """Make a one-time link for this address, creating the reader if it is new."""
    now = now or datetime.now(UTC)
    address = normalise(email)
    reader = await by_email(session, address)
    if reader is None:
        reader = Reader(email=address)
        session.add(reader)
        await session.flush()
    token = secrets.token_urlsafe(32)
    expires_at = now + LINK_VALID_FOR
    # created_at from the same clock as expires_at, not the database's own: the table checks
    # expires_at > created_at, and two clocks — a caller's ``now``, the server's now() — can
    # disagree by more than the fifteen minutes a link lives
    session.add(
        LoginToken(
            reader_id=reader.id,
            token_hash=hash_token(token),
            created_at=now,
            expires_at=expires_at,
        )
    )
    await session.flush()
    return Link(reader=reader, token=token, expires_at=expires_at)


async def redeem(
    session: AsyncSession, token: str, *, now: datetime | None = None
) -> tuple[Reader, str]:
    """Turn a link into a session. Returns the reader and the session token for the cookie.

    Raises when the link is unknown, expired or already used — all the same message, because
    telling them apart helps nobody but somebody guessing.
    """
    now = now or datetime.now(UTC)
    row = await session.scalar(
        select(LoginToken).where(LoginToken.token_hash == hash_token(token)).with_for_update()
    )
    if row is None or row.used_at is not None or row.expires_at <= now:
        raise AccountError("this link no longer works; ask for a new one")
    row.used_at = now
    reader = await session.get(Reader, row.reader_id)
    assert reader is not None
    reader.last_seen_at = now
    session_token = secrets.token_urlsafe(32)
    session.add(
        ReaderSession(
            reader_id=reader.id,
            token_hash=hash_token(session_token),
            created_at=now,  # one clock, as for the link
            expires_at=now + SESSION_VALID_FOR,
        )
    )
    await session.flush()
    return reader, session_token


async def reader_for(
    session: AsyncSession, session_token: str | None, *, now: datetime | None = None
) -> Reader | None:
    """Who is holding this cookie, if anybody still is."""
    if not session_token:
        return None
    now = now or datetime.now(UTC)
    row = await session.scalar(
        select(ReaderSession).where(ReaderSession.token_hash == hash_token(session_token))
    )
    if row is None or row.revoked_at is not None or row.expires_at <= now:
        return None
    reader = await session.get(Reader, row.reader_id)
    if reader is not None:
        reader.last_seen_at = now
    return reader


async def sign_out(
    session: AsyncSession, session_token: str | None, *, now: datetime | None = None
) -> bool:
    if not session_token:
        return False
    row = await session.scalar(
        select(ReaderSession).where(ReaderSession.token_hash == hash_token(session_token))
    )
    if row is None or row.revoked_at is not None:
        return False
    row.revoked_at = now or datetime.now(UTC)
    await session.flush()
    return True


def customer_ref(reader_id: uuid.UUID) -> str:
    """How the company knows a reader: a reference, never an address (D-018, D-024)."""
    return f"reader:{reader_id}"
