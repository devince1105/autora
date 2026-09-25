"""D-047: the public site in sections, a page at a time, with the next article along."""

import uuid
from datetime import timedelta

import httpx
import pytest

from autora.domains.newsroom.models import (
    Article,
    ArticleVersion,
    Source,
    SourceItem,
    Story,
    StoryItem,
)
from autora.domains.newsroom.sources import SECTION, SECTIONS
from autora_api.routers.public import Section


@pytest.fixture
async def public(api):
    async with httpx.AsyncClient(transport=api._transport, base_url="http://test") as client:
        yield client


async def _from_sources(session, company_id, story_id, sections):
    """Give the story one item from a source for each section named (None: a source without)."""
    for n, section in enumerate(sections):
        source = Source(
            company_id=company_id,
            name=f"src-{uuid.uuid4().hex[:8]}",
            kind="rss",
            url="https://example.test/feed.xml",
            config={SECTION: section} if section else {},
        )
        session.add(source)
        await session.flush()
        item = SourceItem(
            company_id=company_id,
            source_id=source.id,
            external_id=f"x{n}",
            url=f"https://example.test/{uuid.uuid4().hex}",
            title="t",
            content_hash=uuid.uuid4().hex,
        )
        session.add(item)
        await session.flush()
        session.add(StoryItem(story_id=story_id, source_item_id=item.id, company_id=company_id))


async def _another(session, first: Article, *, later: timedelta) -> Article:
    """A second article on the site, published ``later`` than the first (a copy of its text)."""
    story = Story(company_id=first.company_id, title="another", state="PUBLISHED")
    session.add(story)
    await session.flush()
    group = uuid.uuid4()
    article = Article(
        company_id=first.company_id,
        story_id=story.id,
        slug=f"another-{uuid.uuid4().hex[:6]}",
        title="another",
        state="PUBLISHED",
        primary_lang="zh-TW",
        published_langs=["zh-TW"],
        published_at=first.published_at + later,
        published_group_id=group,
    )
    session.add(article)
    await session.flush()
    session.add(
        ArticleVersion(
            company_id=first.company_id,
            article_id=article.id,
            version=1,
            lang="zh-TW",
            draft_group_id=group,
            title=f"另一篇 {later}",
            summary=None,
            body=[{"type": "paragraph", "text": "x", "claim_ids": []}],
            claim_ids=[],
        )
    )
    return article


def test_the_api_spells_out_the_same_sections():
    assert Section.__args__ == SECTIONS


async def test_an_article_is_in_the_section_most_of_its_sources_name(public, newsroom_room):
    room = newsroom_room
    await room.publish()
    list_ = {"lang": "zh-TW", "company": room.company.slug}
    only = (await public.get("/api/public/articles", params=list_)).json()
    assert [a["section"] for a in only] == [None]  # no source says: front page only
    assert (await public.get("/api/public/articles", params=list_ | {"section": "ai"})).json() == []

    async with room.committed() as session:
        await _from_sources(session, room.company.id, room.story.id, ["ai", "tw", "ai", None])
        await session.commit()
    ai = (await public.get("/api/public/articles", params=list_ | {"section": "ai"})).json()
    assert [a["section"] for a in ai] == ["ai"]
    assert (await public.get("/api/public/articles", params=list_ | {"section": "tw"})).json() == []
    one = await public.get(f"/api/public/articles/zh-TW/{ai[0]['slug']}")
    assert one.json()["section"] == "ai"
    bad = await public.get("/api/public/articles", params=list_ | {"section": "nft"})
    assert bad.status_code == 422


async def test_pages_and_the_next_article_along(public, newsroom_room):
    room = newsroom_room
    first_id = uuid.UUID(await room.publish())
    async with room.committed() as session:
        first = await session.get(Article, first_id)
        newer = await _another(session, first, later=timedelta(hours=1))
        older = await _another(session, first, later=-timedelta(hours=1))
        await session.commit()
        slugs = [newer.slug, first.slug, older.slug]

    list_ = {"lang": "zh-TW", "company": room.company.slug, "limit": 2}
    page1 = (await public.get("/api/public/articles", params=list_)).json()
    page2 = (await public.get("/api/public/articles", params=list_ | {"offset": 2})).json()
    assert [a["slug"] for a in page1 + page2] == slugs

    middle = (await public.get(f"/api/public/articles/zh-TW/{first.slug}")).json()
    assert middle["newer"] == {
        "title": "另一篇 1:00:00",
        "path": f"/news/zh-TW/articles/{newer.slug}",
    }
    assert middle["older"]["path"] == f"/news/zh-TW/articles/{older.slug}"
    top = (await public.get(f"/api/public/articles/zh-TW/{newer.slug}")).json()
    assert top["newer"] is None and top["older"]["path"] == f"/news/zh-TW/articles/{first.slug}"
