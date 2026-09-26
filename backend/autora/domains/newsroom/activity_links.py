"""``activity_links(task, run)`` (T-514, 3d-office/06 §4, platform/05 §7): where an agent's
work can be seen, for the 3D office and the agent panel.

research / analysis -> the story (its sources, evidence, claims); draft / review -> the article's
current version (and, for review, its fact-check); distribute -> the article's distribution.
The pages are the newsroom admin pages (T-517).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autora.db.models import AgentRun, Task
from autora.domains.newsroom.models import Article, ArticleVersion

STORY_TASKS = {"research": ("題材與來源", ""), "analysis": ("題材的主張", "#claims")}
ARTICLE_TASKS = {"draft", "review", "distribute"}


def _story_id(task: Task) -> uuid.UUID | None:
    raw = (task.input.get("params") or {}).get("story_id")
    try:
        return uuid.UUID(str(raw)) if raw else None
    except ValueError:
        return None


async def activity_links(
    session: AsyncSession, task: Task, run: AgentRun | None
) -> list[dict[str, str]]:
    story_id = _story_id(task)
    if story_id is None or (task.name not in STORY_TASKS and task.name not in ARTICLE_TASKS):
        return []
    story_href = f"/admin/newsroom/stories/{story_id}"
    if task.name in STORY_TASKS:
        label, anchor = STORY_TASKS[task.name]
        return [{"label": label, "href": story_href + anchor}]

    article = await session.scalar(
        select(Article).where(Article.story_id == story_id, Article.company_id == task.company_id)
    )
    if article is None:  # the writer has not saved a draft yet
        return [{"label": "題材的主張", "href": f"{story_href}#claims"}]
    href = f"/admin/newsroom/articles/{article.id}"
    if task.name == "distribute":
        return [{"label": "發布紀錄", "href": f"{href}#distribution"}]
    version = await session.scalar(
        select(ArticleVersion.version)
        .where(ArticleVersion.draft_group_id == article.current_draft_group_id)
        .limit(1)
    )
    links = [{"label": f"文章草稿 v{version}", "href": f"{href}?version={version}"}]
    if task.name == "review":
        links.append({"label": "事實查核", "href": f"{href}?version={version}#fact-check"})
    return links
