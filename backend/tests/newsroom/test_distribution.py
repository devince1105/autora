"""T-513: create_distribution — the social copy of a published article, saved as a draft only."""

import uuid

from sqlalchemy import select

from autora.db.models import EventRecord
from autora.domains.newsroom.models import Article, Distribution

POSTS = {"zh-TW": "流明市首座社區微電網啟用。", "en": "Lumen City switches on its first microgrid."}


async def _distributions(room, article_id):
    async with room.committed() as session:
        return (
            await session.scalars(
                select(Distribution)
                .where(Distribution.article_id == uuid.UUID(article_id))
                .order_by(Distribution.id)
            )
        ).all()


async def test_only_a_published_article_gets_social_copy(newsroom_room):
    room = newsroom_room
    drafted = await room.call("write_draft", room.draft(list(room.claims.values())))
    early = await room.call(
        "create_distribution", {"article_id": drafted.output["article_id"], "posts": POSTS}
    )
    assert not early.ok and "the article is DRAFT: only published articles" in early.message


async def test_the_copy_is_saved_as_a_draft_with_the_links(newsroom_room):
    room = newsroom_room
    article_id = await room.publish()
    made = await room.call("create_distribution", {"article_id": article_id, "posts": POSTS})
    assert made.ok, made.message
    async with room.committed() as session:
        slug = (await session.get(Article, uuid.UUID(article_id))).slug
    site, social = await _distributions(room, article_id)
    assert site.channel == "site" and social.channel == "social_draft"
    assert social.status == "draft"  # nothing is posted
    assert social.copy == {
        "zh-TW": {"text": POSTS["zh-TW"], "url": f"/zh-TW/articles/{slug}"},
        "en": {"text": POSTS["en"], "url": f"/en/articles/{slug}"},
    }
    assert social.run_id is not None and social.created_by["kind"] == "system"
    assert made.output["distribution_id"] == str(social.id) and made.produced[0].id == social.id
    async with room.committed() as session:
        created = (
            await session.scalars(
                select(EventRecord.payload).where(
                    EventRecord.company_id == room.company.id,
                    EventRecord.event_type == "DISTRIBUTION_CREATED",
                )
            )
        ).all()
    assert {p["channel"]: p["status"] for p in created} == {
        "site": "published",
        "social_draft": "draft",
    }

    # a retried call returns the same draft; different copy is refused
    again = await room.call(
        "create_distribution",
        {"article_id": article_id, "posts": {k: f"  {v} " for k, v in POSTS.items()}},
    )
    assert again.ok and again.output["reused"] is True
    other = await room.call(
        "create_distribution", {"article_id": article_id, "posts": POSTS | {"en": "Other."}}
    )
    assert not other.ok and "already has its social_draft copy" in other.message
    assert len(await _distributions(room, article_id)) == 2


async def test_every_published_language_gets_a_post(newsroom_room):
    room = newsroom_room
    article_id = await room.publish()
    bad = await room.call(
        "create_distribution",
        {"article_id": article_id, "posts": {"en": "x" * 501, "ja": "新しい"}},
    )
    assert not bad.ok
    for problem in (
        "no post for zh-TW",
        "ja: the article is not published in ja",
        "en: at most 500 characters (got 501)",
    ):
        assert problem in bad.message
    site = await room.call(
        "create_distribution", {"article_id": article_id, "channel": "site", "posts": POSTS}
    )
    assert not site.ok and site.error_class == "InvalidToolArguments"
