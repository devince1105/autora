"""Approving and publishing articles (T-512, platform/05 §2, D-001, D-002).

These are commands, not tools: the workflow's ``approve`` and ``publish`` nodes (T-514) and people
(through the API) call them. Each one checks the policy engine first and records the decision,
like ``start_workflow``:

- ``approve_article`` (IN_REVIEW -> APPROVED): the current draft's latest fact-check must have
  passed. A person may always approve; the system may only when the company allows automatic
  approval after a passed fact-check (``newsroom.auto_approve_if_fact_check_passed``, D-001:
  off by default), otherwise the policy says ``needs_approval`` and a person must decide.
- ``reject_article`` (IN_REVIEW -> REJECTED): the story is dropped with the reason.
- ``publish_article`` (APPROVED -> PUBLISHED): the draft group that was approved becomes the
  published one, in the languages the policy publishes (with ``require_all_langs``, all of them
  or nothing); the story becomes PUBLISHED; the site distribution is recorded; ARTICLE_PUBLISHED
  and DISTRIBUTION_CREATED are emitted. **Idempotent**: publishing a published article returns
  what was published and emits nothing (a retried workflow node, a double click).

The article row is locked for the whole command, so two publishers cannot both publish.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from autora.db.models import Agent
from autora.db.repositories.companies import get_policies
from autora.domains.newsroom.articles import ARTICLE_FSM
from autora.domains.newsroom.events import (
    ArticleApproved,
    ArticlePublished,
    ArticleRejected,
    DistributionCreated,
    StoryDropped,
)
from autora.domains.newsroom.models import (
    Article,
    ArticleState,
    ArticleVersion,
    Distribution,
    DistributionStatus,
    FactCheckReport,
    Story,
    StoryState,
)
from autora.domains.newsroom.policy import language_policy
from autora.domains.newsroom.stories import STORY_FSM
from autora.infra.ids import uuid7
from autora.runtime.actor import Actor
from autora.runtime.events.outbox import emit
from autora.runtime.events.schema import EventPayload, new_event
from autora.runtime.policy import PolicyEngine

SITE = "site"


class PublishError(Exception):
    """The article cannot take this step (wrong state, no passed fact-check, missing language)."""


class NotAllowed(Exception):
    """The policy did not allow it; ``outcome`` is "deny" or "needs_approval"."""

    def __init__(self, action: str, outcome: str, reason: str):
        self.action = action
        self.outcome = outcome
        self.reason = reason
        super().__init__(f"{action} {outcome}: {reason}")


@dataclass(frozen=True)
class Published:
    article_id: uuid.UUID
    slug: str
    langs: list[str]
    urls: dict[str, str]
    distribution_id: uuid.UUID
    newly: bool
    """False when the article had already been published (nothing happened this time)."""


def article_path(lang: str, slug: str) -> str:
    return f"/{lang}/articles/{slug}"


async def _article(session: AsyncSession, company_id: uuid.UUID, article_id: uuid.UUID) -> Article:
    article = await session.get(Article, article_id, with_for_update=True)
    if article is None or article.company_id != company_id:
        raise PublishError(f"no article {article_id}")
    return article


async def _fact_check_passed(session: AsyncSession, article: Article) -> bool:
    latest = await session.scalar(
        select(FactCheckReport)
        .where(
            FactCheckReport.article_id == article.id,
            FactCheckReport.draft_group_id == article.current_draft_group_id,
        )
        .order_by(FactCheckReport.created_at.desc(), FactCheckReport.id.desc())
        .limit(1)
    )
    return bool(latest and latest.passed)


async def _allowed(
    session: AsyncSession,
    policy: PolicyEngine,
    actor: Actor,
    action: str,
    article: Article,
    facts: dict,
) -> None:
    role = None
    if actor.kind == "agent":
        agent = await session.get(Agent, _uuid(actor.id))
        if agent is None or agent.company_id != article.company_id:
            raise NotAllowed(action, "deny", f"unknown agent {actor.id}")
        role = agent.role
    decision = await policy.decide_and_record(
        session,
        actor,
        action,
        company_id=article.company_id,
        role=role,
        args={"article_id": str(article.id)},
        facts=facts,
        company_policies=await get_policies(session, article.company_id),
    )
    if decision.outcome != "allow":
        raise NotAllowed(action, decision.outcome, decision.reason)


def _uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


async def _emit(
    session: AsyncSession, article: Article, actor: Actor, payload: EventPayload
) -> None:
    await emit(
        session,
        new_event(
            payload,
            company_id=article.company_id,
            actor=actor,
            aggregate_type="article",
            aggregate_id=article.id,
        ),
    )


async def approve_article(
    session: AsyncSession,
    *,
    policy: PolicyEngine,
    company_id: uuid.UUID,
    article_id: uuid.UUID,
    actor: Actor,
    reason: str | None = None,
) -> Article:
    article = await _article(session, company_id, article_id)
    if article.state in (ArticleState.APPROVED, ArticleState.PUBLISHED):
        return article  # already decided
    if article.state != ArticleState.IN_REVIEW:
        raise PublishError(f"the article is {article.state}; only articles in review are approved")
    passed = await _fact_check_passed(session, article)
    if not passed:
        raise PublishError("the current draft has no passed fact-check")
    await _allowed(
        session, policy, actor, "approve_article", article, {"fact_check_passed": passed}
    )
    await ARTICLE_FSM.transition(
        session, article, ArticleState.APPROVED, actor=actor, reason=reason
    )
    await _emit(session, article, actor, ArticleApproved(article_id=article.id, by=actor.kind))
    return article


async def reject_article(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    article_id: uuid.UUID,
    actor: Actor,
    reason: str,
) -> Article:
    """A person turns the article down: it is REJECTED and its story DROPPED."""
    if actor.kind != "human":
        raise NotAllowed("reject_article", "deny", "only a person rejects an article")
    article = await _article(session, company_id, article_id)
    await ARTICLE_FSM.transition(
        session, article, ArticleState.REJECTED, actor=actor, reason=reason
    )
    story = await session.get(Story, article.story_id, with_for_update=True)
    if story is not None and STORY_FSM.can(story.state, StoryState.DROPPED):
        await STORY_FSM.transition(session, story, StoryState.DROPPED, actor=actor, reason=reason)
        await emit(
            session,
            new_event(
                StoryDropped(
                    story_id=story.id,
                    title=story.title[:300],
                    score=story.score,
                    reason=reason[:500],
                ),
                company_id=company_id,
                actor=actor,
                aggregate_type="story",
                aggregate_id=story.id,
            ),
        )
    await _emit(
        session,
        article,
        actor,
        ArticleRejected(article_id=article.id, by=actor.kind, reason=reason[:500]),
    )
    return article


async def publish_article(
    session: AsyncSession,
    *,
    policy: PolicyEngine,
    company_id: uuid.UUID,
    article_id: uuid.UUID,
    actor: Actor,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Published:
    article = await _article(session, company_id, article_id)
    if article.state == ArticleState.PUBLISHED:
        site = await session.scalar(
            select(Distribution).where(
                Distribution.article_id == article.id, Distribution.channel == SITE
            )
        )
        assert site is not None  # written with the publication
        return Published(
            article_id=article.id,
            slug=article.slug,
            langs=list(article.published_langs),
            urls={lang: v["url"] for lang, v in site.copy.items()},
            distribution_id=site.id,
            newly=False,
        )
    if article.state != ArticleState.APPROVED:
        raise PublishError(f"the article is {article.state}; only approved articles are published")
    if not await _fact_check_passed(session, article):
        raise PublishError("the approved draft has no passed fact-check")
    await _allowed(session, policy, actor, "publish_article", article, {})

    lang_policy = language_policy(await get_policies(session, company_id))
    versions = (
        await session.scalars(
            select(ArticleVersion)
            .where(ArticleVersion.draft_group_id == article.current_draft_group_id)
            .order_by(ArticleVersion.id)
        )
    ).all()
    langs = [v.lang for v in versions if v.lang in lang_policy.langs]
    missing = [lang for lang in lang_policy.langs if lang not in langs]
    if lang_policy.primary not in langs or (lang_policy.require_all and missing):
        raise PublishError(f"not published: the approved draft lacks {missing}")
    langs.sort(key=lambda lang: lang_policy.langs.index(lang))

    now = clock()
    await ARTICLE_FSM.transition(session, article, ArticleState.PUBLISHED, actor=actor)
    article.published_langs = langs
    article.published_at = now
    article.published_group_id = article.current_draft_group_id
    story = await session.get(Story, article.story_id, with_for_update=True)
    if story is not None and story.state != StoryState.PUBLISHED:
        await STORY_FSM.transition_via(session, story, StoryState.PUBLISHED, actor=actor)

    titles = {v.lang: v.title for v in versions}
    urls = {lang: article_path(lang, article.slug) for lang in langs}
    distribution_id = uuid7()
    await session.execute(
        insert(Distribution)
        .values(
            id=distribution_id,
            company_id=company_id,
            article_id=article.id,
            channel=SITE,
            status=DistributionStatus.PUBLISHED.value,
            external_ref=urls[lang_policy.primary],
            copy={lang: {"url": urls[lang], "title": titles[lang]} for lang in langs},
            created_by=actor.as_json(),
        )
        .on_conflict_do_nothing()
    )
    await _emit(
        session,
        article,
        actor,
        ArticlePublished(
            article_id=article.id, slug=article.slug, langs=langs, url=urls[lang_policy.primary]
        ),
    )
    await _emit(
        session,
        article,
        actor,
        DistributionCreated(
            article_id=article.id,
            distribution_id=distribution_id,
            channel=SITE,
            status=DistributionStatus.PUBLISHED.value,
        ),
    )
    return Published(
        article_id=article.id,
        slug=article.slug,
        langs=langs,
        urls=urls,
        distribution_id=distribution_id,
        newly=True,
    )
