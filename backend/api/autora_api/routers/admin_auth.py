"""Signing in to the back office (D-055): an emailed link, for the addresses on ADMIN_EMAILS.

- POST /api/admin/auth/link    {email, next_path?} -> 202, always
- POST /api/admin/auth/verify  {token} -> who signed in, and the admin cookie
- GET  /api/admin/auth/me      -> who is calling the back office, or 401
- POST /api/admin/auth/logout  -> 204, the session revoked and the cookie cleared

The same links and sessions as a reader's (``autora.accounts``), with three differences: a link
is only sent to an address on the list — and the answer is 202 either way, so the form does not
say who the admins are; the session lasts 14 days, not 60; and it lives in its own cookie.
Being on the list is checked again on every request (``deps.require_operator``), not only here.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from autora.accounts import AccountError, normalise, redeem, request_link, sign_out
from autora.accounts import emails as account_emails
from autora.accounts.service import ADDRESS_PATTERN
from autora.infra.email import EmailError
from autora.infra.settings import Settings
from autora_api.deps import (
    ADMIN_COOKIE,
    ADMIN_SESSION_VALID_FOR,
    EmailSender,
    Session,
    admin_for,
    require_operator,
    settings_dep,
)

router = APIRouter(prefix="/api/admin/auth", tags=["admin-auth"])

SettingsDep = Annotated[Settings, Depends(settings_dep)]
AdminCookie = Annotated[str | None, Cookie(alias=ADMIN_COOKIE)]


class LinkRequest(BaseModel):
    email: str = Field(max_length=254, pattern=ADDRESS_PATTERN)
    next_path: str | None = Field(default=None, max_length=200, pattern=r"^/admin(/[^\s]*)?$")
    """Where to go after signing in: a page of the back office, never another address."""


class Verify(BaseModel):
    token: str = Field(min_length=10, max_length=200)


class AdminMe(BaseModel):
    via: Literal["email", "token"]
    email: str | None
    """The admin's address, to show them who they are signed in as; None with the token."""


def _set_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        ADMIN_COOKIE,
        token,
        max_age=int(ADMIN_SESSION_VALID_FOR.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=settings.site_base_url.startswith("https://"),
        path="/",
    )


@router.post("/link", status_code=status.HTTP_202_ACCEPTED)
async def send_link(
    body: LinkRequest, session: Session, settings: SettingsDep, sender: EmailSender
) -> Response:
    """Email a one-time link to an admin. 202 for any address, on the list or not."""
    try:
        address = normalise(body.email)
    except AccountError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    if address not in settings.admin_emails:
        return Response(status_code=status.HTTP_202_ACCEPTED)
    link = await request_link(session, address)
    url = account_emails.admin_login_url(
        settings.site_base_url, link.token, next_path=body.next_path
    )
    try:
        await sender.send(account_emails.admin_login_email(address, url, link.expires_at))
    except EmailError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "the link could not be sent") from exc
    await session.commit()
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.post("/verify")
async def verify(
    body: Verify, session: Session, settings: SettingsDep, response: Response
) -> AdminMe:
    try:
        reader, token = await redeem(session, body.token, valid_for=ADMIN_SESSION_VALID_FOR)
    except AccountError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    if reader.email not in settings.admin_emails:  # taken off the list since the link was sent
        await sign_out(session, token)
        await session.commit()
        raise HTTPException(status.HTTP_403_FORBIDDEN, "this address may not open the back office")
    await session.commit()
    _set_cookie(response, token, settings)
    return AdminMe(via="email", email=reader.email)


@router.get("/me", dependencies=[Depends(require_operator)])
async def me(
    session: Session,
    settings: SettingsDep,
    autora_admin: AdminCookie = None,
) -> AdminMe:
    """Who is calling. ``require_operator`` has already refused anybody else."""
    reader = await admin_for(session, autora_admin, settings)
    await session.commit()  # last_seen_at
    if reader is not None:
        return AdminMe(via="email", email=reader.email)
    return AdminMe(via="token", email=None)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(session: Session, autora_admin: AdminCookie = None) -> Response:
    await sign_out(session, autora_admin)
    await session.commit()
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(ADMIN_COOKIE, path="/")
    return response
