"""``create_distribution`` (T-513): marketing prepares the social copy of a published article.

MVP (Q-channel: the company's own site is the only outlet): the copy is **only written to the
database** as a draft (``status = draft``), never posted anywhere. The site itself is recorded by
the publisher (T-512), so marketing cannot create a ``site`` distribution.

Rules: the article is PUBLISHED (the policy says so too: ``create_distribution`` is limited to
published articles); there is one post per published language; each post links to that
language's page (added here, so the model cannot get it wrong). An article gets one distribution
per channel: a retried call returns it, a different one is refused.
"""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from autora.domains.newsroom.events import DistributionCreated
from autora.domains.newsroom.models import (
    Article,
    ArticleState,
    Distribution,
    DistributionStatus,
)
from autora.domains.newsroom.publisher import article_path
from autora.infra.ids import uuid7
from autora.runtime.events.catalog import ProducedRef
from autora.runtime.events.outbox import emit
from autora.runtime.events.schema import new_event
from autora.runtime.tools import ToolContext, ToolRegistry, ToolResult

MAX_POST = 500


class DistributionError(Exception):
    retryable = False


class CreateDistributionArgs(BaseModel):
    article_id: uuid.UUID
    channel: Literal["social_draft"] = "social_draft"
    posts: dict[str, str] = Field(
        min_length=1,
        max_length=5,
        description=(
            "One post per published language, e.g. {'zh-TW': '...', 'en': '...'}; "
            f"at most {MAX_POST} characters each. The article's link is added for you."
        ),
    )


def _output(row: Distribution, reused: bool) -> dict:
    return {
        "distribution_id": str(row.id),
        "article_id": str(row.article_id),
        "channel": row.channel,
        "status": row.status,
        "copy": row.copy,
        "reused": reused,
    }


async def create_distribution(args: CreateDistributionArgs, ctx: ToolContext) -> ToolResult:
    session = ctx.session
    article = await session.get(Article, args.article_id, with_for_update=True)
    if article is None or article.company_id != ctx.company_id:
        raise DistributionError(f"no article {args.article_id}")
    if article.state != ArticleState.PUBLISHED:
        raise DistributionError(f"the article is {article.state}: only published articles")

    copy = {lang: " ".join(text.split()) for lang, text in args.posts.items()}
    problems = [
        f"no post for {lang} (the article is published in {', '.join(article.published_langs)})"
        for lang in article.published_langs
        if not copy.get(lang)
    ]
    problems += [
        f"{lang}: the article is not published in {lang}"
        for lang in copy
        if lang not in article.published_langs
    ]
    problems += [
        f"{lang}: at most {MAX_POST} characters (got {len(text)})"
        for lang, text in copy.items()
        if len(text) > MAX_POST
    ]
    if problems:
        raise DistributionError("the copy was not saved:\n- " + "\n- ".join(problems))
    stored = {
        lang: {"text": text, "url": article_path(lang, article.slug)} for lang, text in copy.items()
    }

    earlier = await session.scalar(
        select(Distribution).where(
            Distribution.article_id == article.id, Distribution.channel == args.channel
        )
    )
    if earlier is not None:
        if earlier.copy != stored:
            raise DistributionError(
                f"this article already has its {args.channel} copy ({earlier.id})"
            )
        return ToolResult(
            output=_output(earlier, reused=True),
            summary=f"reused the {args.channel} copy of article {article.id}",
            produced=[ProducedRef(type="distribution", id=earlier.id)],
        )

    row = Distribution(
        id=uuid7(),
        company_id=ctx.company_id,
        article_id=article.id,
        channel=args.channel,
        status=DistributionStatus.DRAFT.value,
        copy=stored,
        created_by=ctx.actor.as_json(),
        run_id=ctx.run_id,
    )
    session.add(row)
    await session.flush()
    await emit(
        session,
        new_event(
            DistributionCreated(
                article_id=article.id,
                distribution_id=row.id,
                channel=row.channel,
                status=row.status,
            ),
            company_id=ctx.company_id,
            actor=ctx.actor,
            aggregate_type="article",
            aggregate_id=article.id,
            agent_id=ctx.agent_id,
            run_id=ctx.run_id,
            task_id=ctx.task_id,
            workflow_run_id=ctx.workflow_run_id,
            correlation_id=ctx.workflow_run_id,
        ),
    )
    return ToolResult(
        output=_output(row, reused=False),
        summary=f"drafted the {row.channel} copy of article {article.id} "
        f"({', '.join(stored)}; not posted)",
        produced=[ProducedRef(type="distribution", id=row.id)],
    )


def register(registry: ToolRegistry) -> None:
    registry.tool(
        "create_distribution",
        description=(
            "Save the social copy of a published article as a draft: one post per published "
            "language (the link is added). Nothing is posted (MVP)."
        ),
        side_effect="write",
        timeout_s=10.0,
        retryable=True,
    )(create_distribution)
