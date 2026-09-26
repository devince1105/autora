"""D-055: the back office signs in with an emailed link, for the addresses on ADMIN_EMAILS."""

import re
import uuid

from sqlalchemy import select

from autora.accounts import Reader
from autora.db.models import CommandRecord
from autora.infra.settings import load_settings
from autora_api.deps import ADMIN_COOKIE, settings_dep
from tests.api.conftest import ADMIN
from tests.conftest import unique_company

ANON = {"Authorization": ""}
LINK, VERIFY, ME = "/api/admin/auth/link", "/api/admin/auth/verify", "/api/admin/auth/me"


def _post(api, path, body=None):
    """As a browser would: no operator token, only whatever cookie it holds."""
    return api.post(path, json=body, headers=ANON)


def _get(api, path):
    return api.get(path, headers=ANON)


def _token(mailbox) -> str:
    (message,) = mailbox.sent
    assert "/admin/login/verify?token=" in message.text and message.subject.startswith("艾矽鯨後台")
    return re.search(r"token=([A-Za-z0-9_\-]+)", message.text).group(1)


async def _sign_in(api, mailbox) -> str:
    assert (await _post(api, LINK, {"email": ADMIN})).status_code == 202
    response = await api.post(
        "/api/admin/auth/verify", json={"token": _token(mailbox)}, headers=ANON
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"via": "email", "email": ADMIN}
    cookie = response.cookies[ADMIN_COOKIE]
    api.cookies.set(ADMIN_COOKIE, cookie)
    return cookie


async def test_a_link_goes_only_to_an_admin_and_the_answer_does_not_say_so(
    api, db_session, mailbox
):
    stranger = "reader@example.com"
    response = await _post(api, LINK, {"email": stranger})
    assert response.status_code == 202 and mailbox.sent == []
    assert await db_session.scalar(select(Reader).where(Reader.email == stranger)) is None
    response = await api.post(
        "/api/admin/auth/link", json={"email": "Admin@AiSiWhale.test"}, headers=ANON
    )
    assert response.status_code == 202 and _token(mailbox)


async def test_an_admin_signed_in_by_link_uses_the_back_office_without_the_token(
    api, db_session, mailbox
):
    company = await unique_company(db_session, "adm")
    assert (await _get(api, f"/api/companies/{company.id}/finance")).status_code == 401
    await _sign_in(api, mailbox)
    assert (await _get(api, f"/api/companies/{company.id}/finance")).status_code == 200
    assert (await _get(api, ME)).json() == {
        "via": "email",
        "email": ADMIN,
    }

    # what an admin does is recorded as theirs, by id — never by address (D-024)
    await api.post(
        f"/api/companies/{company.id}/finance/budgets", json={"amount": "10"}, headers=ANON
    )
    reader = await db_session.scalar(select(Reader).where(Reader.email == ADMIN))
    log = await db_session.scalar(
        select(CommandRecord).where(CommandRecord.company_id == company.id)
    )
    assert log.actor == {"kind": "human", "id": f"admin:{reader.id}"}


async def test_the_token_still_opens_the_back_office(api):
    assert (await api.get("/api/admin/auth/me")).json() == {"via": "token", "email": None}
    assert (await _get(api, ME)).status_code == 401


async def test_off_the_list_means_out_at_once(api, db_settings, mailbox):
    await _sign_in(api, mailbox)
    app = api._transport.app
    app.dependency_overrides[settings_dep] = lambda: load_settings(
        database_url=db_settings.database_url, api_bearer_token="x", admin_emails=[]
    )
    assert (await _get(api, ME)).status_code == 401


async def test_a_reader_s_cookie_does_not_open_it(api, mailbox):
    """The same person signed in to the site is not thereby in the back office."""
    await api.post("/api/auth/link", json={"email": ADMIN}, headers=ANON)
    (message,) = mailbox.sent
    token = re.search(r"token=([A-Za-z0-9_\-]+)", message.text).group(1)
    assert (await _post(api, "/api/auth/verify", {"token": token})).status_code == 200
    assert (await _get(api, ME)).status_code == 401


async def test_signing_out_ends_the_session(api, mailbox):
    cookie = await _sign_in(api, mailbox)
    assert (await _post(api, "/api/admin/auth/logout")).status_code == 204
    api.cookies.set(ADMIN_COOKIE, cookie)  # even kept, it no longer works
    assert (await _get(api, ME)).status_code == 401


async def test_a_used_or_unknown_link_does_not_sign_in(api, mailbox):
    await _post(api, LINK, {"email": ADMIN})
    token = _token(mailbox)
    assert (await _post(api, VERIFY, {"token": token})).status_code == 200
    again = await _post(api, VERIFY, {"token": token})
    assert again.status_code == 400
    unknown = await api.post(
        "/api/admin/auth/verify", json={"token": uuid.uuid4().hex}, headers=ANON
    )
    assert unknown.status_code == 400
