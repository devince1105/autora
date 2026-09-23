"""D-025: signing in with a link, and what the database is allowed to remember about it.

The reader tables are the only place a person's address exists, so the tests that matter here
are about what is *not* stored, and about a link being usable exactly once.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import inspect, select

from autora.accounts import models, service
from autora.accounts.service import AccountError

NOW = datetime(2026, 9, 23, 9, 0, tzinfo=UTC)


async def _link(session, email="reader@example.com", *, now=NOW):
    return await service.request_link(session, email, now=now)


def test_a_reader_is_an_address_and_when_they_were_last_here():
    columns = {column.name for column in inspect(models.Reader).columns}
    assert columns == {"id", "email", "last_seen_at", "created_at", "updated_at"}
    assert "password" not in columns and "name" not in columns


async def test_the_link_is_emailed_and_only_its_hash_is_kept(db_session):
    link = await _link(db_session)

    stored = (await db_session.scalars(select(models.LoginToken))).all()
    assert len(stored) == 1
    assert stored[0].token_hash != link.token, "a database dump must not be a set of live links"
    assert stored[0].token_hash == service.hash_token(link.token)
    assert stored[0].expires_at == NOW + service.LINK_VALID_FOR


async def test_asking_twice_for_the_same_address_is_one_reader(db_session):
    first = await _link(db_session, "Reader@Example.com ")
    again = await _link(db_session, "reader@example.com")

    assert again.reader.id == first.reader.id
    assert again.reader.email == "reader@example.com", "one person, however they typed it"
    assert first.token != again.token


@pytest.mark.parametrize("address", ["", "nobody", "no body@example.com", "a@b"])
async def test_what_is_not_an_address(db_session, address):
    with pytest.raises(AccountError):
        await _link(db_session, address)


async def test_a_link_signs_you_in_once(db_session):
    link = await _link(db_session)

    reader, token = await service.redeem(db_session, link.token, now=NOW + timedelta(minutes=1))

    assert reader.id == link.reader.id
    assert reader.last_seen_at == NOW + timedelta(minutes=1)
    signed_in = await service.reader_for(db_session, token, now=NOW + timedelta(minutes=2))
    assert signed_in is not None and signed_in.id == reader.id

    with pytest.raises(AccountError, match="no longer works"):
        await service.redeem(db_session, link.token, now=NOW + timedelta(minutes=2))


async def test_a_link_that_waited_too_long_is_no_good(db_session):
    link = await _link(db_session)
    with pytest.raises(AccountError, match="no longer works"):
        await service.redeem(db_session, link.token, now=NOW + service.LINK_VALID_FOR)


async def test_a_token_nobody_issued_is_no_good(db_session):
    with pytest.raises(AccountError, match="no longer works"):
        await service.redeem(db_session, "made-up-token", now=NOW)


async def test_only_the_session_s_hash_is_stored(db_session):
    link = await _link(db_session)
    _, token = await service.redeem(db_session, link.token, now=NOW)

    row = await db_session.scalar(select(models.ReaderSession))
    assert row.token_hash == service.hash_token(token) != token


async def test_a_session_ends_when_it_expires_or_when_they_sign_out(db_session):
    link = await _link(db_session)
    _, token = await service.redeem(db_session, link.token, now=NOW)

    stale = NOW + service.SESSION_VALID_FOR
    assert await service.reader_for(db_session, token, now=stale) is None

    assert await service.sign_out(db_session, token, now=NOW + timedelta(days=1))
    assert await service.reader_for(db_session, token, now=NOW + timedelta(days=2)) is None
    assert not await service.sign_out(db_session, token), "signing out twice changes nothing"


async def test_no_cookie_is_nobody(db_session):
    assert await service.reader_for(db_session, None) is None
    assert await service.reader_for(db_session, "") is None
    assert not await service.sign_out(db_session, None)


async def test_the_company_knows_a_reader_by_reference_never_by_address(db_session):
    link = await _link(db_session)
    ref = service.customer_ref(link.reader.id)
    assert ref == f"reader:{link.reader.id}"
    assert "@" not in ref


async def test_a_link_and_a_session_keep_one_clock(db_session):
    """Whatever time the caller says it is, the row agrees with itself.

    The table checks ``expires_at > created_at``. When ``created_at`` came from the database's own
    clock and ``expires_at`` from the caller's, every test here with a fixed time broke fifteen
    minutes after that time — on the day it was written. A time years ago must work as well as
    now does.
    """
    long_ago = datetime(2020, 1, 1, tzinfo=UTC)
    link = await service.request_link(db_session, "then@example.com", now=long_ago)
    token_row = await db_session.scalar(
        select(models.LoginToken).where(models.LoginToken.reader_id == link.reader.id)
    )
    assert token_row.created_at == long_ago
    assert token_row.expires_at == long_ago + service.LINK_VALID_FOR

    _, session_token = await service.redeem(db_session, link.token, now=long_ago)
    session_row = await db_session.scalar(
        select(models.ReaderSession).where(
            models.ReaderSession.token_hash == service.hash_token(session_token)
        )
    )
    assert session_row.created_at == long_ago
