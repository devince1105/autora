"""T-515: the public site's API — published articles and the reader beacon (no auth, no PII)."""

import uuid

import httpx
import pytest
from sqlalchemy import func, select

from autora.domains.newsroom.models import AnalyticsEvent, Article

SESSION = "0123456789abcdef0123456789abcdef"


@pytest.fixture
async def public(api):
    """The same app, without the operator's token: what a reader's browser has."""
    async with httpx.AsyncClient(transport=api._transport, base_url="http://test") as client:
        yield client


@pytest.fixture
async def published(newsroom_room):
    article_id = await newsroom_room.publish()
    async with newsroom_room.committed() as session:
        return await session.get(Article, uuid.UUID(article_id))


async def _events(db_session, article_id):
    return await db_session.scalar(
        select(func.count())
        .select_from(AnalyticsEvent)
        .where(AnalyticsEvent.article_id == article_id)
    )


async def test_a_published_article_is_public_in_each_language(public, published):
    zh = await public.get(f"/api/public/articles/zh-TW/{published.slug}")
    assert zh.status_code == 200, zh.text
    body = zh.json()
    assert body["article_id"] == str(published.id) and body["lang"] == "zh-TW"
    assert body["title"] == "流明市首座社區微電網啟用"
    assert body["path"] == f"/zh-TW/articles/{published.slug}"
    assert body["langs"] == {
        "zh-TW": f"/zh-TW/articles/{published.slug}",
        "en": f"/en/articles/{published.slug}",
    }
    assert [b["type"] for b in body["blocks"]] == ["paragraph", "paragraph"]
    assert all("claim_ids" not in b for b in body["blocks"])  # readers see text, not internals
    # the sources behind the claims, once each: the pilot story and the city's press release
    assert [s["site"] for s in body["sources"]] == [
        "news.fixtures.autora.test",
        "city.fixtures.autora.test",
    ]
    en = await public.get(f"/api/public/articles/en/{published.slug}")
    assert en.json()["title"] == "Lumen City switches on its first microgrid"

    listed = await public.get("/api/public/articles", params={"lang": "en"})
    assert published.slug in [a["slug"] for a in listed.json()]
    mine = await public.get("/api/public/articles", params={"lang": "en", "company": "nobody"})
    assert mine.json() == []


async def test_unpublished_or_unknown_is_not_found(public, newsroom_room):
    room = newsroom_room
    drafted = await room.call("write_draft", room.draft(list(room.claims.values())))
    async with room.committed() as session:
        draft = await session.get(Article, uuid.UUID(drafted.output["article_id"]))
    assert (await public.get(f"/api/public/articles/zh-TW/{draft.slug}")).status_code == 404
    assert (await public.get("/api/public/articles/ja/whatever")).status_code == 404
    bad = await public.get("/api/public/articles", params={"lang": "<script>"})
    assert bad.status_code == 422


async def test_the_beacon_counts_each_session_once_a_day(public, published, db_session):
    beacon = {
        "article_id": str(published.id),
        "lang": "en",
        "event_type": "view",
        "session_hash": SESSION,
    }
    for _ in range(3):  # a reload, a double send
        sent = await public.post("/api/analytics/beacon", json=beacon)
        assert sent.status_code == 204, sent.text
    assert await _events(db_session, published.id) == 1
    # another kind, another language, another session: each counts
    for extra in (
        {"event_type": "read_complete"},
        {"lang": "zh-TW"},
        {"session_hash": "f" * 32},
    ):
        assert (await public.post("/api/analytics/beacon", json=beacon | extra)).status_code == 204
    assert await _events(db_session, published.id) == 4
    row = await db_session.scalar(
        select(AnalyticsEvent).where(AnalyticsEvent.article_id == published.id).limit(1)
    )
    assert row.company_id == published.company_id and row.day is not None


async def test_the_beacon_takes_nothing_about_the_reader(public, published):
    base = {"article_id": str(published.id), "lang": "en", "event_type": "view"}
    for session_hash in ("short", "NOT-HEX-" * 4, "a" * 65, "someone@example.com"):
        sent = await public.post(
            "/api/analytics/beacon", json=base | {"session_hash": session_hash}
        )
        assert sent.status_code == 422, session_hash
    kind = await public.post(
        "/api/analytics/beacon", json=base | {"session_hash": SESSION, "event_type": "click"}
    )
    assert kind.status_code == 422
    unknown = await public.post(
        "/api/analytics/beacon",
        json=base | {"session_hash": SESSION, "article_id": str(uuid.uuid4())},
    )
    assert unknown.status_code == 404
    elsewhere = await public.post(
        "/api/analytics/beacon", json=base | {"session_hash": SESSION, "lang": "ja"}
    )
    assert elsewhere.status_code == 404
