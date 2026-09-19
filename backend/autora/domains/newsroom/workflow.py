"""The newsroom's workflow: one story to one published article (T-514, platform/05 §2,
3d-office/06 §1).

    research -> analysis -> draft -> review -> approve -> publish -> distribute
                              ^         |
                              +-revise--+   (at most two revisions)

- The five agent steps are the newsroom agents (T-506 .. T-513).
- ``review`` is a loop check: when the editor asks for a revision, the engine adds another
  ``draft`` (with the editor's issues in ``params.issues``) and ``review``, and ``approve`` waits
  for the new review. A third request for a revision has already rejected the article and dropped
  the story (``review.py``); the engine then cancels what was waiting.
- ``approve`` is a service step (role ``human``): the system approves the article itself when the
  company allows it after a passed fact-check (D-001, off by default); otherwise a person decides
  through an approval (``approve_article``), whose decision approves or rejects the article in the
  same transaction (``on_article_decided``).
- ``publish`` is a service step (role ``system``): the publisher (T-512).
- The measurement schedule after publication (+1h, +24h, +7d) arrives with analytics (T-516).

``start_story`` starts the workflow for a selected story (the story goes IN_PRODUCTION).
Starting the template some other way (the generic API) needs ``story_id`` and ``title`` params.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autora.company.agents import hire_agent
from autora.company.workflows import StartWorkflowError, start_workflow
from autora.db.models import Agent, AgentStatus, Approval, ApprovalKind, WorkflowRun
from autora.domains.newsroom.models import Article, ArticleState, Story, StoryState
from autora.domains.newsroom.publisher import (
    NotAllowed,
    PublishError,
    approve_article,
    publish_article,
    reject_article,
)
from autora.domains.newsroom.stories import STORY_FSM
from autora.runtime.actor import Actor
from autora.runtime.dag import Loop, NodeSpec, TemplateRegistry, WorkflowEngine, WorkflowTemplate
from autora.runtime.policy import PolicyEngine
from autora.runtime.services import ServiceContext

TEMPLATE_NAME = "newsroom.story_to_article_v2"
APPROVE = "newsroom.approve"
PUBLISH = "newsroom.publish"
APPROVE_ACTION = "approve_article"
MAX_REVISIONS = 2  # review.MAX_REVISIONS: the editor's tool drops the story after that


def _revise(output: dict[str, Any]) -> bool:
    return output.get("verdict") == "revise"


def _issues(output: dict[str, Any]) -> dict[str, Any]:
    return {"issues": output.get("issues") or []}


TEMPLATE = WorkflowTemplate(
    name=TEMPLATE_NAME,
    nodes=(
        NodeSpec("research", "研究：{title}", "researcher"),
        NodeSpec("analysis", "分析：{title}", "analyst", depends_on=("research",)),
        NodeSpec("draft", "撰稿：{title}", "writer", depends_on=("analysis",)),
        NodeSpec("review", "審稿：{title}", "editor", depends_on=("draft",)),
        NodeSpec("approve", "核准：{title}", "human", depends_on=("review",), service=APPROVE),
        NodeSpec("publish", "發布：{title}", "system", depends_on=("approve",), service=PUBLISH),
        NodeSpec("distribute", "推廣：{title}", "marketing", depends_on=("publish",)),
    ),
    loops=(
        Loop(
            check="review",
            back_to="draft",
            again=_revise,
            carry=_issues,
            max_rounds=MAX_REVISIONS,
            round_label="{name}（第 {round} 輪）",
        ),
    ),
)


def register_templates(templates: TemplateRegistry) -> None:
    templates.register(TEMPLATE)


# --- staffing ---------------------------------------------------------------------------------

DISPLAY_NAMES = {
    "researcher": "Rae",
    "analyst": "Ana",
    "writer": "Wren",
    "editor": "Eli",
    "marketing": "Mika",
}
"""The newsroom's desks (the echo demo's three share the names)."""


async def staff_newsroom(
    session: AsyncSession, company_id: uuid.UUID, *, actor: Actor
) -> list[Agent]:
    """Hire one active agent per newsroom role the company does not have yet."""
    existing = set(
        (
            await session.scalars(
                select(Agent.role).where(
                    Agent.company_id == company_id, Agent.status == AgentStatus.ACTIVE
                )
            )
        ).all()
    )
    return [
        await hire_agent(session, company_id=company_id, role=role, display_name=name, actor=actor)
        for role, name in DISPLAY_NAMES.items()
        if role not in existing
    ]


async def start_story(
    session: AsyncSession,
    *,
    policy: PolicyEngine,
    workflows: WorkflowEngine,
    story: Story,
    project_id: uuid.UUID,
    actor: Actor,
    role: str | None = None,
    demo: dict[str, Any] | None = None,
) -> WorkflowRun:
    """Start the workflow for a SELECTED story (the ``instantiate_workflow`` command: the policy
    decides and the decision is recorded); the story goes IN_PRODUCTION. ``demo``: knobs for the
    simulated model only (``simulation.py``: pace, a revision on the first review)."""
    if story.state != StoryState.SELECTED:
        raise StartWorkflowError(f"the story is {story.state}; only a selected story is started")
    run, _ = await start_workflow(
        session,
        policy=policy,
        workflows=workflows,
        company_id=story.company_id,
        project_id=project_id,
        template=TEMPLATE_NAME,
        params={"story_id": str(story.id), "title": story.title[:80]}
        | ({"demo": demo} if demo else {}),
        actor=actor,
        role=role,
    )
    await STORY_FSM.transition(session, story, StoryState.IN_PRODUCTION, actor=actor)
    return run


async def _story_article(ctx: ServiceContext) -> Article | None:
    story_id = ctx.params.get("story_id")
    if not story_id:
        return None
    return await ctx.session.scalar(
        select(Article).where(
            Article.story_id == uuid.UUID(str(story_id)),
            Article.company_id == ctx.task.company_id,
        )
    )


# --- service steps ----------------------------------------------------------------------------


async def approve_step(ctx: ServiceContext) -> None:
    article = await _story_article(ctx)
    if article is None:
        await ctx.fail("NoArticle", "the story has no article to approve")
        return
    if article.state in (ArticleState.APPROVED, ArticleState.PUBLISHED):
        await ctx.complete({"article_id": str(article.id), "approved_by": "earlier"})
        return
    if article.state != ArticleState.IN_REVIEW:
        await ctx.fail("NotInReview", f"the article is {article.state}, not accepted by the editor")
        return
    try:
        await approve_article(
            ctx.session,
            policy=ctx.policy,
            company_id=article.company_id,
            article_id=article.id,
            actor=ctx.actor,
            reason="automatic approval after a passed fact-check",
        )
    except NotAllowed as refused:
        if refused.outcome != "needs_approval":
            await ctx.fail("NotAllowed", refused.reason)
            return
        await ctx.request_approval(
            kind=ApprovalKind.ARTICLE,
            action=APPROVE_ACTION,
            summary=f"核准發布：{article.title}"[:300],
            payload={
                "article_id": str(article.id),
                "story_id": str(article.story_id),
                "draft_group_id": str(article.current_draft_group_id),
            },
        )
        return
    except PublishError as error:
        await ctx.fail("PublishError", str(error))
        return
    await ctx.complete(
        {"article_id": str(article.id), "approved_by": "system"},
        summary="approved automatically (fact-check passed)",
    )


async def publish_step(ctx: ServiceContext) -> None:
    article = await _story_article(ctx)
    if article is None:
        await ctx.fail("NoArticle", "the story has no article to publish")
        return
    try:
        published = await publish_article(
            ctx.session,
            policy=ctx.policy,
            company_id=article.company_id,
            article_id=article.id,
            actor=ctx.actor,
        )
    except (PublishError, NotAllowed) as error:
        await ctx.fail(type(error).__name__, str(error))
        return
    await ctx.complete(
        {
            "article_id": str(published.article_id),
            "slug": published.slug,
            "langs": published.langs,
            "urls": published.urls,
            "distribution_id": str(published.distribution_id),
        },
        summary=f"published {published.urls.get(published.langs[0], published.slug)}",
    )


def on_article_decided(policy: PolicyEngine):
    """A person's decision on an ``approve_article`` approval approves or rejects the article."""

    async def hook(
        session: AsyncSession, approval: Approval, outcome: str, actor: Actor, reason: str | None
    ) -> None:
        article_id = uuid.UUID(approval.payload["article_id"])
        if outcome == "approve":
            await approve_article(
                session,
                policy=policy,
                company_id=approval.company_id,
                article_id=article_id,
                actor=actor,
                reason=reason,
            )
        else:
            await reject_article(
                session,
                company_id=approval.company_id,
                article_id=article_id,
                actor=actor,
                reason=reason or "rejected at approval",
            )

    return hook
