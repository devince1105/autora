"""Marketing (T-513, 3d-office/06 §1, platform/04 §6, MVP): prepares the social copy of a
published article.

Task ``distribute`` (role ``marketing``), input ``params.story_id``, after publication. The
article is on the company's site already (the publisher recorded it); marketing writes one social
post per published language with ``create_distribution``, which only saves it as a draft
(Q-channel: nothing is posted). The policy lets marketing distribute published articles only;
``marketing_facts`` gives the policy the article's state.

It reports a ``DistributionPlan``: the site and the social draft, with the copy. The validators
hold it to the records: the site entry is the article's site distribution, the social draft was
created in this task, and the copy reported is the copy saved.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autora.db.models import EventRecord
from autora.domains.newsroom.agents.researcher import task_story_id
from autora.domains.newsroom.models import (
    Article,
    ArticleState,
    ArticleVersion,
    Distribution,
    Story,
)
from autora.domains.newsroom.publisher import SITE, article_path
from autora.domains.newsroom.tools.distribution import MAX_POST
from autora.runtime.behaviors import AgentBehavior, RunContext

ROLE = "marketing"
TASK = "distribute"
SOCIAL = "social_draft"
OPENING = 400

SYSTEM_PROMPT = f"""You are the marketing editor of a bilingual newsroom (Traditional Chinese and
English). The article below is published on the company's site. Your job: write its social post,
one per published language, and save it with create_distribution (it is saved as a draft; nothing
is posted).

Each post: what the story is and why it matters, in one or two sentences, from the article only
(no facts, numbers or quotes the article does not state); no clickbait, no hashtags spam, at most
{MAX_POST} characters. The link is added for you. The Chinese post is in Traditional Chinese
(zh-TW), never Simplified Chinese; the posts are faithful to each other.

When done, reply with only a JSON object (no other text):
{{"article_id": "<the article id>",
 "channels": [{{"channel": "site", "distribution_id": "<the site distribution id>"}},
              {{"channel": "social_draft", "distribution_id": "<from create_distribution>",
                "posts": {{"zh-TW": "<the post>", "en": "<the post>"}}}}]}}
"""


class PlannedChannel(BaseModel):
    channel: Literal["site", "social_draft"]
    distribution_id: uuid.UUID
    posts: dict[str, str] = Field(default={}, max_length=5)


class DistributionPlan(BaseModel):
    article_id: uuid.UUID
    channels: list[PlannedChannel] = Field(min_length=1, max_length=5)


async def _story_article(session: AsyncSession, ctx: RunContext) -> Article | None:
    story_id = task_story_id(ctx)
    if story_id is None:
        return None
    article = await session.scalar(select(Article).where(Article.story_id == story_id))
    return article if article is not None and article.company_id == ctx.company_id else None


async def produced_distributions(session: AsyncSession, ctx: RunContext) -> set[uuid.UUID]:
    """Distributions this task's own tool calls produced (any attempt)."""
    payloads = (
        await session.scalars(
            select(EventRecord.payload).where(
                EventRecord.task_id == ctx.task.id, EventRecord.event_type == "TOOL_COMPLETED"
            )
        )
    ).all()
    return {
        uuid.UUID(ref["id"])
        for payload in payloads
        for ref in payload.get("produced") or []
        if ref.get("type") == "distribution"
    }


# --- policy facts -----------------------------------------------------------------------------


async def marketing_facts(
    session: AsyncSession, ctx: RunContext, tool: str, args: Mapping[str, Any]
) -> dict[str, Any]:
    """``create_distribution`` is limited to published articles: the policy needs the state."""
    if tool != "create_distribution":
        return {}
    try:
        article = await session.get(Article, uuid.UUID(str(args.get("article_id"))))
    except ValueError:
        article = None
    if article is None or article.company_id != ctx.company_id:
        return {}
    return {"article_state": article.state}


# --- context (OBSERVE) ------------------------------------------------------------------------


async def distribute_context(session: AsyncSession, ctx: RunContext) -> str | None:
    story_id = task_story_id(ctx)
    story = await session.get(Story, story_id) if story_id else None
    if story is None or story.company_id != ctx.company_id:
        return "No story found for this task: report that, do not guess one."
    article = await _story_article(session, ctx)
    if article is None or article.state != ArticleState.PUBLISHED:
        state = article.state if article else "not written"
        return f"Story: {story.title}\nThe article is {state}, not published: report that."
    site = await session.scalar(
        select(Distribution).where(
            Distribution.article_id == article.id, Distribution.channel == SITE
        )
    )
    versions = (
        await session.scalars(
            select(ArticleVersion)
            .where(ArticleVersion.draft_group_id == article.published_group_id)
            .order_by(ArticleVersion.id)
        )
    ).all()
    lines = [
        f"Story: {story.title}",
        f"Article id: {article.id}",
        f"Published in: {', '.join(article.published_langs)}",
    ]
    if site is not None:
        lines.append(f"Site distribution id: {site.id}")
    for version in versions:
        if version.lang not in article.published_langs:
            continue
        opening = next((b["text"] for b in version.body if b["type"] != "heading"), "")
        lines += [
            f"[{version.lang}] Title: {version.title}",
            f"  Page: {article_path(version.lang, article.slug)}",
        ]
        if version.summary:
            lines.append(f"  Summary: {version.summary}")
        lines.append(f"  Opening: {opening[:OPENING]}")
    return "\n".join(lines)


# --- validators (EVALUATE) --------------------------------------------------------------------


async def plan_matches_the_records(
    session: AsyncSession, ctx: RunContext, note: BaseModel
) -> list[str]:
    assert isinstance(note, DistributionPlan)
    article = await _story_article(session, ctx)
    if article is None or article.id != note.article_id:
        expected = article.id if article else "none (no article for this task's story)"
        return [f"article_id must be this task's story's article: {expected}"]
    produced = await produced_distributions(session, ctx)
    issues = []
    for planned in note.channels:
        row = await session.get(Distribution, planned.distribution_id)
        if row is None or row.article_id != article.id:
            issues.append(f"distribution {planned.distribution_id} is not one of this article's")
        elif row.channel != planned.channel:
            issues.append(f"distribution {row.id} is the {row.channel} one, not {planned.channel}")
        elif planned.channel == SOCIAL:
            if row.id not in produced:
                issues.append(f"distribution {row.id} was not created in this task")
            saved = {lang: v["text"] for lang, v in row.copy.items()}
            reported = {lang: " ".join(t.split()) for lang, t in planned.posts.items()}
            if reported != saved:
                issues.append(f"report the posts you saved in {row.id} (as create_distribution "
                              "returned them)")  # fmt: skip
    return issues


async def site_and_social(session: AsyncSession, ctx: RunContext, note: BaseModel) -> list[str]:
    assert isinstance(note, DistributionPlan)
    channels = [c.channel for c in note.channels]
    issues = []
    if channels.count(SITE) != 1:
        issues.append("list the site distribution once (the publisher's)")
    if channels.count(SOCIAL) != 1:
        issues.append("write the social post once with create_distribution and list it")
    return issues


def _summary(note: BaseModel) -> str:
    assert isinstance(note, DistributionPlan)
    return f"distribution of article {note.article_id}: " + ", ".join(
        c.channel for c in note.channels
    )


BEHAVIOR = AgentBehavior(
    role=ROLE,
    task_name=TASK,
    capability="drafting",
    system_prompt=SYSTEM_PROMPT,
    output_model=DistributionPlan,
    tools=("create_distribution", "read_draft"),
    validators=(plan_matches_the_records, site_and_social),
    max_steps=6,
    repair_limit=2,
    max_output_tokens=2048,
    context=distribute_context,
    policy_facts=marketing_facts,
    summarize=_summary,
)
