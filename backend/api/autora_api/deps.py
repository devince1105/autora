from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

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


def require_operator(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    settings: Annotated[Settings, Depends(settings_dep)],
) -> Actor:
    """MVP auth: a single operator identified by API_BEARER_TOKEN (logs/platform/12 §Admin)."""
    expected = settings.api_bearer_token.get_secret_value()
    if credentials is None or not secrets.compare_digest(credentials.credentials, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return Actor.human("operator")


Session = Annotated[AsyncSession, Depends(get_session)]
EmailSender = Annotated[Sender, Depends(sender_dep)]
Operator = Annotated[Actor, Depends(require_operator)]
RuntimeDep = Annotated[Runtime, Depends(runtime_dep)]
