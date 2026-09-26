from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from datetime import timedelta
from functools import lru_cache
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from autora.accounts import Reader, reader_for
from autora.app import Runtime, build_runtime
from autora.db.session import get_sessionmaker
from autora.infra.email import Sender, build_sender
from autora.infra.settings import Settings, get_settings
from autora.runtime.actor import Actor

_bearer = HTTPBearer(auto_error=False)


def settings_dep() -> Settings:
    return get_settings()


@lru_cache(maxsize=1)
def sender_dep() -> Sender:
    """One email sender per process. Tests override it with one that keeps what it sent."""
    return build_sender(get_settings())


@lru_cache(maxsize=1)
def runtime_dep() -> Runtime:
    """One wired runtime per process (task manager, workflow engine, approvals, policy)."""
    return build_runtime(get_settings())


async def get_session() -> AsyncIterator[AsyncSession]:
    """One session per request. Handlers commit explicitly; anything uncommitted rolls back."""
    async with get_sessionmaker()() as session:
        yield session


ADMIN_COOKIE = "autora_admin"
"""The back office's own cookie (D-055): apart from a reader's, so signing out of the site does
not sign an admin out of /admin, and a reader's cookie never opens it."""
ADMIN_SESSION_VALID_FOR = timedelta(days=14)
"""Shorter than a reader's 60 days: this one can spend the company's money."""


async def admin_for(session: AsyncSession, token: str | None, settings: Settings) -> Reader | None:
    """The admin holding this cookie: a live session, for an address still on ADMIN_EMAILS."""
    reader = await reader_for(session, token)
    if reader is None or reader.email not in settings.admin_emails:
        return None
    return reader


def admin_actor(reader: Reader) -> Actor:
    """How the company records an admin: by reader id, never by address (D-024)."""
    return Actor.human(f"admin:{reader.id}")


async def require_operator(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    settings: Annotated[Settings, Depends(settings_dep)],
    session: Annotated[AsyncSession, Depends(get_session)],
    autora_admin: Annotated[str | None, Cookie()] = None,
) -> Actor:
    """The back office's caller: the API_BEARER_TOKEN (scripts, CI, the way in when email
    fails), or an admin signed in with an emailed link (D-055)."""
    expected = settings.api_bearer_token.get_secret_value()
    if credentials is not None and secrets.compare_digest(credentials.credentials, expected):
        return Actor.human("operator")
    reader = await admin_for(session, autora_admin, settings)
    if reader is not None:
        return admin_actor(reader)
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="sign in to the back office, or send the operator token",
        headers={"WWW-Authenticate": "Bearer"},
    )


Session = Annotated[AsyncSession, Depends(get_session)]
EmailSender = Annotated[Sender, Depends(sender_dep)]
Operator = Annotated[Actor, Depends(require_operator)]
RuntimeDep = Annotated[Runtime, Depends(runtime_dep)]
