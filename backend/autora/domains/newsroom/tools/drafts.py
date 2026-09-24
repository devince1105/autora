"""``write_draft`` and ``read_draft`` (T-508).

``write_draft`` writes one draft of a story's article in every language at once (see
``articles.py`` for the rules it checks). A draft that breaks a rule is not saved and the error
lists every problem. The first draft creates the article (ARTICLE_CREATED); later drafts (after
the editor asks for a revision) are new versions of it. A retried call returns the draft it
wrote the first time. Each language version is ``produced`` (the writer's draft link, AC-7).

``read_draft`` returns a draft (the latest, or a given version) with the claims it cites, for
the editor, marketing and the CEO.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select

from autora.db.repositories.companies import get_policies
from autora.domains.newsroom.advice import advice_problems, no_advice
from autora.domains.newsroom.articles import (
    ArticleState,
    ClaimFacts,
    LanguageVersion,
    check_draft,
    slugify,
)
from autora.domains.newsroom.events import ArticleCreated
from autora.domains.newsroom.models import Article, ArticleVersion, Claim, Story
from autora.domains.newsroom.policy import language_policy
from autora.infra.ids import uuid7
from autora.runtime.events.catalog import ProducedRef
from autora.runtime.events.outbox import emit
from autora.runtime.events.schema import new_event
from autora.runtime.tools import ToolContext, ToolRegistry, ToolResult


class DraftError(Exception):
    retryable = False


class WriteDraftArgs(BaseModel):
    story_id: uuid.UUID
    versions: list[LanguageVersion] = Field(
        min_length=1,
        max_length=5,
        description="One version per language, all citing the same claims.",
    )
    change_summary: str | None = Field(
        default=None, max_length=500, description="For a revision: what changed and why."
    )


class ReadDraftArgs(BaseModel):
    article_id: uuid.UUID | None = None
    story_id: uuid.UUID | None = None
    version: int | None = Field(default=None, ge=1, description="Default: the latest draft.")

    @model_validator(mode="after")
    def _one_way_in(self) -> ReadDraftArgs:
        if (self.article_id is None) == (self.story_id is None):
            raise ValueError("give article_id or story_id (one of them)")
        return self


def _draft_output(article: Article, rows: list[ArticleVersion], reused: bool) -> dict:
    return {
        "article_id": str(article.id),
        "slug": article.slug,
        "state": article.state,
        "version": rows[0].version,
        "draft_group_id": str(rows[0].draft_group_id),
        "versions": {row.lang: str(row.id) for row in rows},
        "reused": reused,
    }


async def write_draft(args: WriteDraftArgs, ctx: ToolContext) -> ToolResult:
    session = ctx.session
    earlier = await session.scalar(
        select(ArticleVersion).where(ArticleVersion.idempotency_key == ctx.idempotency_key)
    )
    if earlier is not None:
        article = await session.get(Article, earlier.article_id)
        rows = (
            await session.scalars(
                select(ArticleVersion)
                .where(ArticleVersion.draft_group_id == earlier.draft_group_id)
                .order_by(ArticleVersion.id)
            )
        ).all()
        return ToolResult(
            output=_draft_output(article, list(rows), reused=True),
            summary=f"reused draft v{earlier.version} of article {article.id}",
            produced=[ProducedRef(type="article_version", id=r.id) for r in rows],
        )

    story = await session.get(Story, args.story_id)
    if story is None or story.company_id != ctx.company_id:
        raise DraftError(f"no story {args.story_id}")
    policies = await get_policies(session, ctx.company_id)
    policy = language_policy(policies)
    article = await session.scalar(
        select(Article).where(Article.story_id == story.id).with_for_update()
    )
    cited = set().union(*(v.cited() for v in args.versions))
    rows = (
        await session.execute(
            select(Claim.id, Claim.story_id, Claim.status, Claim.claim_type).where(
                Claim.id.in_(cited), Claim.company_id == ctx.company_id
            )
        )
    ).all()
    facts = {claim_id: ClaimFacts(story_id=sid, status=status) for claim_id, sid, status, _ in rows}
    issues = check_draft(
        args.versions,
        story_id=story.id,
        story_state=story.state,
        article_state=article.state if article else None,
        policy=policy,
        claims=facts,
    )
    if no_advice(policies):
        issues += advice_problems(args.versions, {row.id: row.claim_type for row in rows})
    if issues:
        raise DraftError("the draft was not saved:\n- " + "\n- ".join(issues))

    versions = sorted(args.versions, key=lambda v: v.lang != policy.primary)  # primary first
    primary = versions[0]
    created = article is None
    if article is None:
        article_id = uuid7()
        english = next((v for v in versions if v.lang.startswith("en")), None)
        article = Article(
            id=article_id,
            company_id=ctx.company_id,
            story_id=story.id,
            slug=slugify(english.title if english else story.title, article_id),
            title=primary.title,
            state=ArticleState.DRAFT.value,
            primary_lang=policy.primary,
        )
        session.add(article)
        await session.flush()
    number = (
        await session.scalar(
            select(func.max(ArticleVersion.version)).where(ArticleVersion.article_id == article.id)
        )
        or 0
    ) + 1
    group = uuid7()
    rows: list[ArticleVersion] = []
    for version in versions:
        row = ArticleVersion(
            id=uuid7(),
            company_id=ctx.company_id,
            article_id=article.id,
            version=number,
            lang=version.lang,
            draft_group_id=group,
            title=version.title,
            summary=version.summary,
            body=[block.model_dump(mode="json") for block in version.blocks],
            claim_ids=sorted(version.cited(), key=str),
            translation_of_version_id=rows[0].id if rows else None,
            change_summary=args.change_summary,
            idempotency_key=ctx.idempotency_key if not rows else None,
            task_id=ctx.task_id,
            author_run_id=ctx.run_id,
        )
        session.add(row)
        await session.flush()
        rows.append(row)
    article.title = primary.title
    article.current_draft_group_id = group
    if created:
        await emit(
            session,
            new_event(
                ArticleCreated(
                    article_id=article.id,
                    story_id=story.id,
                    version_id=rows[0].id,
                    langs=[r.lang for r in rows],
                    slug=article.slug,
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
    await session.flush()
    return ToolResult(
        output=_draft_output(article, rows, reused=False),
        summary=f"wrote draft v{number} ({', '.join(r.lang for r in rows)}) "
        f"of article {article.id}",
        produced=[ProducedRef(type="article_version", id=r.id) for r in rows],
    )


async def read_draft(args: ReadDraftArgs, ctx: ToolContext) -> ToolResult:
    session = ctx.session
    if args.article_id is not None:
        article = await session.get(Article, args.article_id)
    else:
        article = await session.scalar(select(Article).where(Article.story_id == args.story_id))
    if article is None or article.company_id != ctx.company_id:
        raise DraftError("no such article (has the writer drafted it yet?)")
    query = select(ArticleVersion).where(ArticleVersion.article_id == article.id)
    if args.version is not None:
        query = query.where(ArticleVersion.version == args.version)
    else:
        query = query.where(ArticleVersion.draft_group_id == article.current_draft_group_id)
    rows = (await session.scalars(query.order_by(ArticleVersion.id))).all()
    if not rows:
        raise DraftError(f"article {article.id} has no version {args.version}")
    cited = sorted({c for row in rows for c in row.claim_ids}, key=str)
    claims = (
        (await session.scalars(select(Claim).where(Claim.id.in_(cited)))).all() if cited else []
    )
    return ToolResult(
        output={
            "article_id": str(article.id),
            "story_id": str(article.story_id),
            "slug": article.slug,
            "state": article.state,
            "revision_count": article.revision_count,
            "version": rows[0].version,
            "versions": {
                row.lang: {
                    "version_id": str(row.id),
                    "title": row.title,
                    "summary": row.summary,
                    "blocks": row.body,
                    "change_summary": row.change_summary,
                }
                for row in rows
            },
            "claims": [
                {
                    "claim_id": str(c.id),
                    "claim_type": c.claim_type,
                    "text": c.text,
                    "status": c.status,
                }
                for c in claims
            ],
        },
        summary=f"read draft v{rows[0].version} of article {article.id}",
    )


def register(registry: ToolRegistry) -> None:
    registry.tool(
        "write_draft",
        description=(
            "Write the story's article in every required language at once. Each paragraph cites "
            "the claims it states (claim_ids); all languages cite the same claims."
        ),
        side_effect="write",
        timeout_s=20.0,
        retryable=True,
    )(write_draft)
    registry.tool(
        "read_draft",
        description="Read the latest draft of an article (or a given version) with its claims.",
        side_effect="read",
        timeout_s=10.0,
    )(read_draft)
