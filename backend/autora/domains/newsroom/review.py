"""The editor's decision on a draft (T-511, platform/05 §3, 3d-office/03 §articles).

The editor reviews the article's current draft once: it runs the fact-check (``run_fact_check``,
layers 1-2), judges the meaning itself (layer 3), then decides with one of these commands. Each
records ARTICLE_REVIEWED (the verdict and whether the fact-check passed):

- ``accept_draft`` (DRAFT -> IN_REVIEW): the draft goes on to approval (a person, or the system
  when the company allows it, D-001). Only with the draft's **latest** fact-check, and only if it
  passed.
- ``request_revision`` (stays DRAFT): the writer gets the editor's issues and
  writes the next version (ARTICLE_REVISION_REQUESTED). A story gets at most ``MAX_REVISIONS``
  revisions; asking for one more rejects the article and drops the story.

A draft is reviewed once: deciding again on the same draft returns the first decision when it is
the same verdict (a retried call) and refuses a different one.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autora.db.models import EventRecord
from autora.domains.newsroom.articles import ARTICLE_FSM
from autora.domains.newsroom.events import (
    ArticleRejected,
    ArticleReviewed,
    ArticleRevisionRequested,
)
from autora.domains.newsroom.models import (
    Article,
    ArticleState,
    ArticleVersion,
    FactCheckReport,
    Story,
)
from autora.domains.newsroom.stories import drop_story
from autora.runtime.actor import Actor
from autora.runtime.events.outbox import emit
from autora.runtime.events.schema import EventPayload, new_event

MAX_REVISIONS = 2
ACCEPT = "accept"
REVISE = "revise"


class ReviewError(Exception):
    """The draft cannot be decided this way (wrong state, fact-check missing or failed)."""

    retryable = False


@dataclass(frozen=True)
class EventRefs:
    """Who and what the decision's events belong to (the editor's run, when it is one)."""

    agent_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None
    workflow_run_id: uuid.UUID | None = None


NO_REFS = EventRefs()


@dataclass(frozen=True)
class Review:
    article_id: uuid.UUID
    version_id: uuid.UUID
    """The primary-language version of the draft reviewed."""
    verdict: str
    fact_check_passed: bool
    revision: int
    """Revisions asked for so far (this one included)."""
    dropped: bool
    """A revision past the limit: the article was rejected and the story dropped."""
    reused: bool
    """The draft had already been decided this way (nothing happened this time)."""


async def _draft(session: AsyncSession, company_id: uuid.UUID, article_id: uuid.UUID):
    article = await session.get(Article, article_id, with_for_update=True)
    if article is None or article.company_id != company_id:
        raise ReviewError(f"no article {article_id}")
    primary = await session.scalar(
        select(ArticleVersion).where(
            ArticleVersion.draft_group_id == article.current_draft_group_id,
            ArticleVersion.translation_of_version_id.is_(None),
        )
    )
    if primary is None:
        raise ReviewError(f"article {article_id} has no draft to review")
    return article, primary


async def _latest_report(session: AsyncSession, article: Article) -> FactCheckReport | None:
    return await session.scalar(
        select(FactCheckReport)
        .where(
            FactCheckReport.article_id == article.id,
            FactCheckReport.draft_group_id == article.current_draft_group_id,
        )
        .order_by(FactCheckReport.created_at.desc(), FactCheckReport.id.desc())
        .limit(1)
    )


async def _earlier(
    session: AsyncSession, article: Article, version: ArticleVersion, verdict: str
) -> Review | None:
    """The decision already taken on this draft, if any (a different verdict is refused)."""
    earlier = await session.scalar(
        select(EventRecord).where(
            EventRecord.aggregate_type == "article",
            EventRecord.aggregate_id == article.id,
            EventRecord.event_type == "ARTICLE_REVIEWED",
            EventRecord.payload["version_id"].astext == str(version.id),
        )
    )
    if earlier is None:
        return None
    if earlier.payload["verdict"] != verdict:
        raise ReviewError(
            f"this draft (v{version.version}) was already reviewed: {earlier.payload['verdict']}"
        )
    return Review(
        article_id=article.id,
        version_id=version.id,
        verdict=verdict,
        fact_check_passed=earlier.payload["fact_check_passed"],
        revision=article.revision_count,
        dropped=article.state == ArticleState.REJECTED,
        reused=True,
    )


async def _emit(
    session: AsyncSession, article: Article, actor: Actor, refs: EventRefs, payload: EventPayload
) -> None:
    await emit(
        session,
        new_event(
            payload,
            company_id=article.company_id,
            actor=actor,
            aggregate_type="article",
            aggregate_id=article.id,
            agent_id=refs.agent_id,
            run_id=refs.run_id,
            task_id=refs.task_id,
            workflow_run_id=refs.workflow_run_id,
            correlation_id=refs.workflow_run_id,
        ),
    )


async def accept_draft(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    article_id: uuid.UUID,
    fact_check_report_id: uuid.UUID,
    actor: Actor,
    by_role: str,
    refs: EventRefs = NO_REFS,
) -> Review:
    article, version = await _draft(session, company_id, article_id)
    earlier = await _earlier(session, article, version, ACCEPT)
    if earlier is not None:
        return earlier
    if article.state != ArticleState.DRAFT:
        raise ReviewError(f"the article is {article.state}: only a draft is accepted")
    latest = await _latest_report(session, article)
    if latest is None:
        raise ReviewError("run the fact-check on this draft first (run_fact_check)")
    if latest.id != fact_check_report_id:
        raise ReviewError(
            f"accept with the draft's latest fact-check ({latest.id}), not {fact_check_report_id}"
        )
    if not latest.passed:
        raise ReviewError("the fact-check did not pass: request a revision instead")
    await ARTICLE_FSM.transition(session, article, ArticleState.IN_REVIEW, actor=actor)
    await _emit(
        session,
        article,
        actor,
        refs,
        ArticleReviewed(
            article_id=article.id,
            version_id=version.id,
            verdict=ACCEPT,
            fact_check_passed=True,
            by_role=by_role,
        ),
    )
    return Review(
        article_id=article.id,
        version_id=version.id,
        verdict=ACCEPT,
        fact_check_passed=True,
        revision=article.revision_count,
        dropped=False,
        reused=False,
    )


async def request_revision(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    article_id: uuid.UUID,
    issues_count: int,
    actor: Actor,
    by_role: str,
    refs: EventRefs = NO_REFS,
) -> Review:
    if issues_count < 1:
        raise ReviewError("a revision needs at least one issue")
    article, version = await _draft(session, company_id, article_id)
    earlier = await _earlier(session, article, version, REVISE)
    if earlier is not None:
        return earlier
    if article.state != ArticleState.DRAFT:
        raise ReviewError(f"the article is {article.state}: only a draft is sent back")
    latest = await _latest_report(session, article)
    passed = bool(latest and latest.passed)
    await _emit(
        session,
        article,
        actor,
        refs,
        ArticleReviewed(
            article_id=article.id,
            version_id=version.id,
            verdict=REVISE,
            fact_check_passed=passed,
            by_role=by_role,
        ),
    )
    if article.revision_count >= MAX_REVISIONS:
        reason = f"still not ready after {MAX_REVISIONS} revisions"
        await ARTICLE_FSM.transition_via(
            session, article, ArticleState.REJECTED, actor=actor, reason=reason
        )
        story = await session.get(Story, article.story_id, with_for_update=True)
        if story is not None:
            await drop_story(session, story, actor=actor, reason=reason)
        await _emit(
            session,
            article,
            actor,
            refs,
            ArticleRejected(article_id=article.id, by=actor.kind, reason=reason),
        )
        return Review(
            article_id=article.id,
            version_id=version.id,
            verdict=REVISE,
            fact_check_passed=passed,
            revision=article.revision_count,
            dropped=True,
            reused=False,
        )
    article.revision_count += 1
    await _emit(
        session,
        article,
        actor,
        refs,
        ArticleRevisionRequested(
            article_id=article.id,
            version_id=version.id,
            issues_count=issues_count,
            by_role=by_role,
            revision=article.revision_count,
        ),
    )
    return Review(
        article_id=article.id,
        version_id=version.id,
        verdict=REVISE,
        fact_check_passed=passed,
        revision=article.revision_count,
        dropped=False,
        reused=False,
    )
