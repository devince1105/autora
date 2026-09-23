"""D-025: the reader's side of the API — a link, a cookie, and what it proves.

These are the site's own endpoints: no operator token, and nothing about a reader is returned
to anybody who does not hold their cookie.
"""

import re

import httpx
import pytest

from autora.accounts import SESSION_COOKIE

ADDRESS = "reader@example.com"


@pytest.fixture
async def site(api):
    """The same app without the operator's token: what a reader's browser has."""
    async with httpx.AsyncClient(transport=api._transport, base_url="http://test") as client:
        yield client


def _token(mailbox) -> str:
    (message,) = mailbox.sent
    match = re.search(r"token=([A-Za-z0-9_\-]+)", message.text)
    assert match, message.text
    return match.group(1)


async def _sign_in(site, mailbox, address=ADDRESS):
    assert (await site.post("/api/auth/link", json={"email": address})).status_code == 202
    verified = await site.post("/api/auth/verify", json={"token": _token(mailbox)})
    assert verified.status_code == 200, verified.text
    return verified


async def test_a_link_is_emailed_and_signs_the_reader_in(site, mailbox):
    verified = await _sign_in(site, mailbox)

    (message,) = mailbox.sent
    assert message.to == ADDRESS and "登入" in message.subject
    body = verified.json()
    assert body["email"] == ADDRESS and body["member_until"] is None
    assert verified.cookies.get(SESSION_COOKIE), "the browser leaves with a session"

    me = await site.get("/api/auth/me")
    assert me.json()["email"] == ADDRESS


async def test_asking_for_a_link_says_the_same_thing_for_any_address(site, mailbox):
    """The form must not answer "is this person a reader here?"."""
    known = await site.post("/api/auth/link", json={"email": ADDRESS})
    stranger = await site.post("/api/auth/link", json={"email": "nobody@example.com"})

    assert (known.status_code, known.text) == (stranger.status_code, stranger.text)
    assert {m.to for m in mailbox.sent} == {ADDRESS, "nobody@example.com"}


async def test_an_address_that_cannot_receive_a_link_is_refused(site, mailbox):
    answer = await site.post("/api/auth/link", json={"email": "not-an-address"})
    assert answer.status_code == 422
    assert mailbox.sent == []


async def test_a_link_works_once(site, mailbox):
    await site.post("/api/auth/link", json={"email": ADDRESS})
    token = _token(mailbox)
    assert (await site.post("/api/auth/verify", json={"token": token})).status_code == 200

    again = await site.post("/api/auth/verify", json={"token": token})
    assert again.status_code == 400 and "no longer works" in again.text


async def test_nobody_is_nobody(site):
    assert (await site.get("/api/auth/me")).json() is None


async def test_signing_out_ends_the_session(site, mailbox):
    await _sign_in(site, mailbox)
    assert (await site.post("/api/auth/logout")).status_code == 204
    assert (await site.get("/api/auth/me")).json() is None


async def test_a_made_up_cookie_proves_nothing(site):
    site.cookies.set(SESSION_COOKIE, "not-a-real-session")
    assert (await site.get("/api/auth/me")).json() is None
