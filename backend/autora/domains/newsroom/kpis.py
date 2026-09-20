"""What the newsroom counts, and how much of it a dollar bought (T-603).

The company layer measures money and time; it does not know what an article is. So the numbers
that need that word are computed here and handed over under the ``newsroom.`` prefix.

Two of them are ratios that mix a newsroom unit with the company's money —
``cost_per_published_article`` and ``views_per_usd``. They belong here rather than in the core
for the same reason: only the newsroom knows what its unit is. The hook is handed the core's
own figures for the same window, so the two sides of a ratio always come from one measurement.

``cost_per_published_article`` is what AI Media's kill criteria are written against
(ARCHITECTURE_V2 §16), so it has to mean exactly one thing: the scope's cost for the window
divided by the articles published in it. A window that published nothing has no cost per
article, and the key is left out — reporting zero or infinity would both read as news.

Scope: an article belongs to a project through its story, and to a business through that
project. A scope the newsroom did no work in gets an empty mapping, not the company's numbers,
so a second business never sees newsroom metrics on its report.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import AsyncSession

from autora.company.reporting import Window
from autora.db.models import EventRecord, KpiScope, Project
from autora.domains.newsroom.models import (
    AnalyticsDaily,
    Article,
    ArticleState,
    Story,
    StoryState,
)

NAME = "newsroom"
RATIO = Decimal("0.0001")
REVISION_REQUESTED = "ARTICLE_REVISION_REQUESTED"
TOP_CANDIDATES = 8


async def kpis(
    session: AsyncSession, window: Window, core: Mapping[str, object]
) -> dict[str, object]:
    """The newsroom's numbers for one scope and window."""
    stories = _stories_in_scope(window)
    published = await session.scalar(
        select(func.count())
        .select_from(Article)
        .where(
            Article.company_id == window.company_id,
            Article.state == ArticleState.PUBLISHED.value,
            Article.published_at >= window.since,
            Article.published_at <= window.until,
            *([Article.story_id.in_(stories)] if stories is not None else []),
        )
    )
    revisions = await _revisions(session, window, stories)
    views, read_complete = await _readers(session, window, stories)

    metrics: dict[str, object] = {
        "published_articles": int(published or 0),
        "revisions_requested": int(revisions or 0),
        "views": int(views or 0),
        "read_complete": int(read_complete or 0),
    }
    cost = _money(core.get("cost_usd"))
    if published and cost is not None:
        metrics["cost_per_published_article"] = str((cost / Decimal(published)).quantize(RATIO))
    if cost and views:
        metrics["views_per_usd"] = str((Decimal(int(views)) / cost).quantize(RATIO))
    return metrics


async def _revisions(session: AsyncSession, window: Window, stories) -> int:
    """How many times a draft was sent back *in this window*.

    Counted from ``ARTICLE_REVISION_REQUESTED`` events, not from ``articles.revision_count``:
    the counter says how many revisions an article has ever had, and the row's ``updated_at``
    moves for unrelated reasons, so neither can say when a revision happened. The event can.
    """
    articles = select(Article.id).where(Article.company_id == window.company_id)
    if stories is not None:
        articles = articles.where(Article.story_id.in_(stories))
    count = await session.scalar(
        select(func.count())
        .select_from(EventRecord)
        .where(
            EventRecord.company_id == window.company_id,
            EventRecord.event_type == REVISION_REQUESTED,
            EventRecord.occurred_at >= window.since,
            EventRecord.occurred_at <= window.until,
            EventRecord.payload["article_id"].astext.cast(UUID).in_(articles),
        )
    )
    return int(count or 0)


def _stories_in_scope(window: Window):
    """The stories whose work belongs to this scope, or None for the whole company."""
    if window.scope is KpiScope.PROJECT:
        return select(Story.id).where(Story.project_id == window.project_id)
    if window.scope is KpiScope.BUSINESS_UNIT:
        return select(Story.id).where(
            Story.project_id.in_(
                select(Project.id).where(Project.business_unit_id == window.business_unit_id)
            )
        )
    return None


async def _readers(session: AsyncSession, window: Window, stories) -> tuple[int | None, int | None]:
    """Readers are counted per day, so a window is read as the days it covers."""
    articles = select(Article.id).where(Article.company_id == window.company_id)
    if stories is not None:
        articles = articles.where(Article.story_id.in_(stories))
    row = (
        await session.execute(
            select(
                func.coalesce(func.sum(AnalyticsDaily.views), 0),
                func.coalesce(func.sum(AnalyticsDaily.read_complete), 0),
            ).where(
                AnalyticsDaily.company_id == window.company_id,
                AnalyticsDaily.day >= window.since.date(),
                AnalyticsDaily.day <= window.until.date(),
                AnalyticsDaily.article_id.in_(articles),
            )
        )
    ).one()
    return int(row[0]), int(row[1])


def _money(value: object) -> Decimal | None:
    """The core stores money as a string so nothing rounds it; read it back exactly."""
    if value is None:
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError):
        return None
    return amount if amount > 0 else None


async def candidates(session: AsyncSession, company_id: uuid.UUID) -> dict[str, object]:
    """What the newsroom could cover next, for whoever is planning the day.

    Put in front of the decision, not taken: choosing among these is the editor-in-chief's job
    (T-605b), and until that agent exists a person chooses. The list is short on purpose — a
    planner that has to read fifty candidates is being given work, not information.
    """
    stories = (
        await session.scalars(
            select(Story)
            .where(
                Story.company_id == company_id,
                Story.state.in_([StoryState.DISCOVERED.value, StoryState.SELECTED.value]),
            )
            .order_by(Story.score.desc(), Story.updated_at.desc())
            .limit(TOP_CANDIDATES)
        )
    ).all()
    return {
        "candidates": [
            {
                "story_id": str(story.id),
                "title": story.title,
                "score": float(story.score or 0),
                "sources": story.items_count,
                "state": story.state,
            }
            for story in stories
        ]
    }
