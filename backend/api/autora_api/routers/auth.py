"""Readers signing in, and what the site may know about them (D-025).

- POST /api/auth/link    {email, company?} -> 202, always
- POST /api/auth/verify  {token} -> the reader, and a session cookie
- POST /api/auth/logout  -> 204, the session revoked and the cookie cleared
- GET  /api/auth/me      -> who is signed in, and until when they are a member

No bearer token: these belong to the public site. The answer to "who are you" is whatever the
cookie proves and nothing else, and requesting a link answers the same way for an address that
has an account and one that does not — the form must not become a way to ask who reads here.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from autora.accounts import (
    SESSION_COOKIE,
    AccountError,
    customer_ref,
    reader_for,
    redeem,
    request_link,
    service,
    sign_out,
)
from autora.accounts import emails as reader_emails
from autora.accounts.service import SESSION_VALID_FOR
from autora.company import memberships
from autora.db.models import Company
from autora.infra.email import EmailError
from autora.infra.settings import Settings
from autora_api.deps import EmailSender, Session, settings_dep

router = APIRouter(tags=["auth"])

ADDRESS = service.ADDRESS_PATTERN
"""The same shape the accounts layer accepts, so the form and the service agree."""

SessionCookie = Annotated[str | None, Cookie(alias=SESSION_COOKIE)]
SettingsDep = Annotated[Settings, Depends(settings_dep)]
CompanySlug = Annotated[str | None, Query(max_length=100)]


class LinkRequest(BaseModel):
    email: str = Field(max_length=254, pattern=ADDRESS)
    lang: str = Field(
        default=reader_emails.DEFAULT_LANG, pattern=r"^[a-z]{2}(-[A-Z][A-Za-z]{1,3})?$"
    )
    """Which language's page the link should open. The site's own, not the reader's guess."""
    next_path: str | None = Field(default=None, max_length=200, pattern=r"^/[^\s]*$")
    """Where to go after signing in. A path inside the site, never another address."""


class Verify(BaseModel):
    token: str = Field(min_length=10, max_length=200)


class Me(BaseModel):
    reader_id: uuid.UUID
    email: str
    member_until: datetime | None = None
    """When their access runs out. None: they are not a member of this company."""

    @property
    def member(self) -> bool:
        return self.member_until is not None


async def _company_id(session, slug: str | None) -> uuid.UUID | None:
    query = select(Company.id) if slug is None else select(Company.id).where(Company.slug == slug)
    return await session.scalar(query.order_by(Company.created_at).limit(1))


async def _me(session, reader, slug: str | None) -> Me:
    company_id = await _company_id(session, slug)
    until = None
    if company_id is not None:
        until = await memberships.access_until(
            session, company_id=company_id, customer_ref=customer_ref(reader.id)
        )
    return Me(reader_id=reader.id, email=reader.email, member_until=until)


def _set_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(SESSION_VALID_FOR.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=settings.site_base_url.startswith("https://"),
        path="/",
    )


@router.post("/api/auth/link", status_code=status.HTTP_202_ACCEPTED)
async def send_link(
    body: LinkRequest, session: Session, settings: SettingsDep, sender: EmailSender
) -> Response:
    """Email a one-time link. Answers 202 whether or not the address has been seen before."""
    link = await request_link(session, body.email)
    url = reader_emails.login_url(
        settings.site_base_url, link.token, lang=body.lang, next_path=body.next_path
    )
    message = reader_emails.login_email(link.reader.email, url, link.expires_at)
    try:
        await sender.send(message)
    except EmailError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "the link could not be sent") from exc
    await session.commit()
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.post("/api/auth/verify")
async def verify(
    body: Verify,
    session: Session,
    settings: SettingsDep,
    response: Response,
    company: CompanySlug = None,
) -> Me:
    try:
        reader, token = await redeem(session, body.token)
    except AccountError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    me = await _me(session, reader, company)
    await session.commit()
    _set_cookie(response, token, settings)
    return me


@router.get("/api/auth/me")
async def me(
    session: Session, autora_reader: SessionCookie = None, company: CompanySlug = None
) -> Me | None:
    reader = await reader_for(session, autora_reader)
    if reader is None:
        return None
    answer = await _me(session, reader, company)
    await session.commit()  # last_seen_at
    return answer


@router.post("/api/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    session: Session, response: Response, autora_reader: SessionCookie = None
) -> Response:
    await sign_out(session, autora_reader)
    await session.commit()
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response
