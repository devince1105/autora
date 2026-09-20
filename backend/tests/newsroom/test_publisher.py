"""T-512: approving, rejecting and publishing articles (commands, policy, idempotency)."""

import asyncio
import uuid

import pytest
from sqlalchemy import func, select

from autora.app import build_policy_engine
from autora.company.reporting import Reporting
from autora.company.reporting_min import load_kpis
from autora.db.models import Agent, EventRecord, PolicyDecision, StateTransition
from autora.db.repositories.companies import upsert_policy
from autora.domains.newsroom.articles import ARTICLE_FSM
from autora.domains.newsroom.kpis import NAME
from autora.domains.newsroom.kpis import kpis as newsroom_kpis
from autora.domains.newsroom.models import Article, Distribution, Story
from autora.domains.newsroom.publisher import (
    NotAllowed,
    PublishError,
    approve_article,
    publish_article,
    reject_article,
)
from autora.runtime.actor import Actor

HUMAN = Actor.human("editor@example.test")
SYSTEM = Actor.system("publisher")
POLICY = build_policy_engine()


async def reviewed(room, *, fact_check=True, langs=("zh-TW", "en")) -> uuid.UUID:
    """A drafted article (optionally fact-checked) sent to review."""
    written = await room.call("write_draft", room.draft(list(room.claims.values()), langs=langs))
    assert written.ok, written.message
    article_id = uuid.UUID(written.output["article_id"])
    if fact_check:
        checked = await room.call("run_fact_check", {"article_id": str(article_id)})
        assert checked.output["passed"], checked.output
    async with room.committed() as session:
        article = await session.get(Article, article_id)
        await ARTICLE_FSM.transition(session, article, "IN_REVIEW", actor=SYSTEM)
        await session.commit()
    return article_id


async def events(room, *types):
    async with room.committed() as session:
        return (
            await session.scalars(
                select(EventRecord)
                .where(EventRecord.company_id == room.company.id, EventRecord.event_type.in_(types))
                .order_by(EventRecord.seq)
            )
        ).all()


async def approve(room, article_id, actor=HUMAN):
    async with room.committed() as session:
        await approve_article(
            session, policy=POLICY, company_id=room.company.id, article_id=article_id, actor=actor
        )
        await session.commit()


async def publish(room, article_id, actor=SYSTEM):
    async with room.committed() as session:
        result = await publish_article(
            session, policy=POLICY, company_id=room.company.id, article_id=article_id, actor=actor
        )
        await session.commit()
    return result


# --- approval ---------------------------------------------------------------------------------


async def test_a_person_approves_a_checked_article(newsroom_room):
    article_id = await reviewed(newsroom_room)
    await approve(newsroom_room, article_id)
    async with newsroom_room.committed() as session:
        assert (await session.get(Article, article_id)).state == "APPROVED"
        decision = await session.scalar(
            select(PolicyDecision).where(
                PolicyDecision.company_id == newsroom_room.company.id,
                PolicyDecision.action == "approve_article",
            )
        )
    assert decision.outcome == "allow"
    [approved] = await events(newsroom_room, "ARTICLE_APPROVED")
    assert approved.payload["by"] == "human"
    await approve(newsroom_room, article_id)  # already approved: nothing more happens
    assert len(await events(newsroom_room, "ARTICLE_APPROVED")) == 1


async def test_the_system_approves_only_when_the_company_allows_it(newsroom_room):
    article_id = await reviewed(newsroom_room)
    with pytest.raises(NotAllowed) as refused:
        await approve(newsroom_room, article_id, actor=SYSTEM)
    assert refused.value.outcome == "needs_approval"  # D-001: a person decides by default
    assert "is off" in refused.value.reason

    async with newsroom_room.committed() as session:
        await upsert_policy(
            session,
            newsroom_room.company.id,
            "newsroom.auto_approve_if_fact_check_passed",
            True,
            updated_by=HUMAN.as_json(),
        )
        await session.commit()
    await approve(newsroom_room, article_id, actor=SYSTEM)
    [approved] = await events(newsroom_room, "ARTICLE_APPROVED")
    assert approved.payload["by"] == "system"

    # agents do not approve: an editor agent is denied (no rule), an unknown one refused
    async with newsroom_room.committed() as session:
        editor = Agent(company_id=newsroom_room.company.id, role="editor", display_name="Ed")
        session.add(editor)
        await session.commit()
    other = await reviewed_again(newsroom_room)
    for agent, outcome in ((Actor.agent(editor.id), "deny"), (Actor.agent(uuid.uuid4()), "deny")):
        with pytest.raises(NotAllowed) as denied:
            async with newsroom_room.committed() as session:
                await approve_article(
                    session,
                    policy=POLICY,
                    company_id=newsroom_room.company.id,
                    article_id=other,
                    actor=agent,
                )
        assert denied.value.outcome == outcome


async def reviewed_again(room) -> uuid.UUID:
    """Another story's article, in review with a passed fact-check."""
    async with room.committed() as session:
        story = Story(company_id=room.company.id, title="Another story", state="SELECTED")
        session.add(story)
        await session.commit()
    evidence = room.evidence["https://news.fixtures.autora.test/lumen-city-microgrid-pilot"]
    claim = await room.call(
        "create_claim",
        {
            "story_id": str(story.id),
            "text": "The battery holds 4 MWh.",
            "claim_type": "number",
            "evidence": [{"evidence_id": evidence, "quote": "a 4 MWh battery"}],
        },
    )
    draft = room.draft([claim.output["claim_id"]])
    draft["story_id"] = str(story.id)
    written = await room.call("write_draft", draft)
    article_id = uuid.UUID(written.output["article_id"])
    await room.call("run_fact_check", {"article_id": str(article_id)})
    async with room.committed() as session:
        await ARTICLE_FSM.transition(
            session, await session.get(Article, article_id), "IN_REVIEW", actor=SYSTEM
        )
        await session.commit()
    return article_id


async def test_no_approval_without_a_passed_fact_check_or_outside_review(newsroom_room):
    unchecked = await reviewed(newsroom_room, fact_check=False)
    with pytest.raises(PublishError, match="no passed fact-check"):
        await approve(newsroom_room, unchecked)
    async with newsroom_room.committed() as session:
        article = await session.get(Article, unchecked)
        await ARTICLE_FSM.transition(session, article, "DRAFT", actor=SYSTEM)
        await session.commit()
    with pytest.raises(PublishError, match="only articles in review"):
        await approve(newsroom_room, unchecked)


# --- publication ------------------------------------------------------------------------------


async def test_publish_once(newsroom_room):
    room = newsroom_room
    article_id = await reviewed(room)
    await approve(room, article_id)
    result = await publish(room, article_id)
    assert result.newly and result.langs == ["zh-TW", "en"]
    assert result.urls == {
        "zh-TW": f"/news/zh-TW/articles/{result.slug}",
        "en": f"/news/en/articles/{result.slug}",
    }
    async with room.committed() as session:
        article = await session.get(Article, article_id)
        story = await session.get(Story, room.story.id)
        site = await session.get(Distribution, result.distribution_id)
        hops = (
            await session.scalars(
                select(StateTransition)
                .where(StateTransition.entity_id == story.id)
                .order_by(StateTransition.id)
            )
        ).all()
    assert article.state == "PUBLISHED" and article.published_langs == ["zh-TW", "en"]
    assert article.published_group_id == article.current_draft_group_id and article.published_at
    assert story.state == "PUBLISHED"
    assert [(h.from_state, h.to_state) for h in hops] == [
        ("SELECTED", "IN_PRODUCTION"),
        ("IN_PRODUCTION", "PUBLISHED"),
    ]
    assert (site.channel, site.status, site.external_ref) == (
        "site",
        "published",
        result.urls["zh-TW"],
    )
    assert site.copy["en"] == {
        "url": result.urls["en"],
        "title": "Lumen City switches on its first microgrid",
    }
    published, distributed = await events(room, "ARTICLE_PUBLISHED", "DISTRIBUTION_CREATED")
    assert published.payload["url"] == result.urls["zh-TW"] and published.payload["langs"] == [
        "zh-TW",
        "en",
    ]
    assert distributed.payload["channel"] == "site" and distributed.payload[
        "distribution_id"
    ] == str(site.id)

    async with room.committed() as session:  # the dashboard counts it, via the newsroom's own
        reporting = Reporting()  # KPI hook: the company layer stores the number,
        reporting.register(NAME, newsroom_kpis)  # the newsroom is what knows it means articles
        kpis = await load_kpis(session, room.company.id, reporting=reporting)
        assert kpis.domain_metrics["newsroom.published_articles"] == 1

    again = await publish(room, article_id)  # a retried node, a double click
    assert (
        not again.newly
        and again.distribution_id == result.distribution_id
        and again.urls == result.urls
    )
    assert len(await events(room, "ARTICLE_PUBLISHED", "DISTRIBUTION_CREATED")) == 2


async def test_two_publishers_at_once_publish_once(newsroom_room):
    room = newsroom_room
    article_id = await reviewed(room)
    await approve(room, article_id)
    first, second = await asyncio.gather(publish(room, article_id), publish(room, article_id))
    assert sorted([first.newly, second.newly]) == [False, True]
    assert first.distribution_id == second.distribution_id
    async with room.committed() as session:
        sites = await session.scalar(
            select(func.count())
            .select_from(Distribution)
            .where(Distribution.article_id == article_id)
        )
    assert sites == 1 and len(await events(room, "ARTICLE_PUBLISHED")) == 1


async def test_only_approved_articles_in_every_required_language(newsroom_room):
    room = newsroom_room
    async with room.committed() as session:
        await upsert_policy(
            session,
            room.company.id,
            "newsroom.require_all_langs",
            False,
            updated_by=HUMAN.as_json(),
        )
        await session.commit()
    article_id = await reviewed(room, langs=("zh-TW",))
    with pytest.raises(PublishError, match="only approved articles"):
        await publish(room, article_id)
    await approve(room, article_id)
    async with room.committed() as session:
        await upsert_policy(
            session, room.company.id, "newsroom.require_all_langs", True, updated_by=HUMAN.as_json()
        )
        await session.commit()
    with pytest.raises(PublishError, match=r"lacks \['en'\]"):
        await publish(room, article_id)
    async with room.committed() as session:
        assert (await session.get(Article, article_id)).state == "APPROVED"  # nothing half-done


# --- rejection --------------------------------------------------------------------------------


async def test_a_person_rejects_and_the_story_is_dropped(newsroom_room):
    room = newsroom_room
    article_id = await reviewed(room)
    with pytest.raises(NotAllowed):
        async with room.committed() as session:
            await reject_article(
                session,
                company_id=room.company.id,
                article_id=article_id,
                actor=SYSTEM,
                reason="no",
            )
    async with room.committed() as session:
        await reject_article(
            session,
            company_id=room.company.id,
            article_id=article_id,
            actor=HUMAN,
            reason="off brand",
        )
        await session.commit()
    async with room.committed() as session:
        assert (await session.get(Article, article_id)).state == "REJECTED"
        assert (await session.get(Story, room.story.id)).state == "DROPPED"
    rejected, dropped = await events(room, "STORY_DROPPED", "ARTICLE_REJECTED")
    assert (dropped.event_type, dropped.payload["reason"]) == ("ARTICLE_REJECTED", "off brand")
    assert rejected.payload["reason"] == "off brand"
