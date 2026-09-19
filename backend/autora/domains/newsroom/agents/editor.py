"""The editor (T-511, 3d-office/06 §1, platform/04 §5): reviews the writer's draft and decides.

Task ``review`` (role ``editor``), input ``params.story_id``, after the draft task. The editor
reads the draft, runs the deterministic fact-check (``run_fact_check``, layers 1-2), judges the
meaning itself in every language (layer 3), and decides with ``accept_draft`` (on to approval) or
``request_revision`` (back to the writer; ``review.py`` limits the revisions). It reports an
``EditorReview``; the workflow hands its issues to the writer's revision task (T-514).

The validators hold the review to what the run did:
- the decision was taken in this task (ARTICLE_REVIEWED) on this story's article, and the verdict
  reported is the one taken;
- the fact-check report was produced in this task for this article; ``accept`` needs it passed;
- ``revise`` lists at least one issue, the same ones that were sent with ``request_revision``.
"""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autora.db.models import EventRecord
from autora.db.repositories.companies import get_policies
from autora.domains.newsroom.agents.researcher import task_story_id
from autora.domains.newsroom.models import Article, ArticleVersion, FactCheckReport, Story
from autora.domains.newsroom.policy import language_policy
from autora.domains.newsroom.review import MAX_REVISIONS
from autora.domains.newsroom.tools.review import Issue
from autora.runtime.behaviors import AgentBehavior, RunContext

ROLE = "editor"
TASK = "review"

SYSTEM_PROMPT = """You are the editor of a bilingual newsroom (Traditional Chinese and English).
Your job: decide whether the writer's draft can go on to approval, or must be revised.

How to review:
1. Read the draft (read_draft) and run the fact-check on it (run_fact_check). The fact-check
   verifies every cited claim against its evidence (quotes, sources, numbers).
2. Then do what the fact-check cannot: for each claim in its semantic_review, check in every
   language that the draft's sentences say what the quotes say — nothing stretched, nothing
   missing that changes the meaning, the same facts in every language. Check that the Chinese is
   Traditional Chinese (never Simplified), that no fact appears without a claim, and that
   contested points are written as who says what.
3. Decide:
   - accept_draft(article_id, fact_check_report_id) only when the fact-check passed and nothing
     needs fixing;
   - otherwise request_revision(article_id, issues): each issue says what to fix and why, with
     the language and block when it is about one (block_ref: the 1-based block number). A claim
     that failed the fact-check cannot be cited again: say what to do without it.
   A draft can be revised at most twice; asking for a third revision drops the story.

When done, reply with only a JSON object (no other text):
{"article_id": "<the article id>",
 "verdict": "accept" | "revise",
 "fact_check_report_id": "<report_id from run_fact_check>",
 "issues": [{"message": "...", "kind": "fact|unsupported|missing_context|translation|style|other",
             "lang": "zh-TW|en (optional)", "block_ref": "3 (optional)"}]}
For accept, issues is []. For revise, the issues you sent with request_revision.
Write the issues in Traditional Chinese (zh-TW); never Simplified Chinese.
"""


class EditorReview(BaseModel):
    article_id: uuid.UUID
    verdict: Literal["accept", "revise"]
    fact_check_report_id: uuid.UUID
    issues: list[Issue] = Field(default=[], max_length=20)


async def _task_events(session: AsyncSession, ctx: RunContext, event_type: str) -> list[dict]:
    return list(
        (
            await session.scalars(
                select(EventRecord.payload)
                .where(EventRecord.task_id == ctx.task.id, EventRecord.event_type == event_type)
                .order_by(EventRecord.id)
            )
        ).all()
    )


async def produced_reports(session: AsyncSession, ctx: RunContext) -> set[uuid.UUID]:
    """Fact-check reports this task's own tool calls produced (any attempt)."""
    return {
        uuid.UUID(ref["id"])
        for payload in await _task_events(session, ctx, "TOOL_COMPLETED")
        for ref in payload.get("produced") or []
        if ref.get("type") == "fact_check_report"
    }


# --- context (OBSERVE) ------------------------------------------------------------------------


async def review_context(session: AsyncSession, ctx: RunContext) -> str | None:
    story_id = task_story_id(ctx)
    story = await session.get(Story, story_id) if story_id else None
    if story is None or story.company_id != ctx.company_id:
        return "No story found for this task: report that, do not guess one."
    lines = [f"Story: {story.title}", f"Story id: {story.id}"]
    article = await session.scalar(select(Article).where(Article.story_id == story.id))
    if article is None:
        lines.append("The writer has not drafted this story's article yet: report that.")
        return "\n".join(lines)
    versions = (
        await session.scalars(
            select(ArticleVersion)
            .where(ArticleVersion.draft_group_id == article.current_draft_group_id)
            .order_by(ArticleVersion.id)
        )
    ).all()
    policy = language_policy(await get_policies(session, ctx.company_id))
    lines += [
        f"Article id: {article.id}",
        f"Draft: version {versions[0].version if versions else '?'} "
        f"({', '.join(v.lang for v in versions)}); state {article.state}",
        f"Languages required: {', '.join(policy.langs) if policy.require_all else policy.primary}",
        f"Revisions so far: {article.revision_count} of {MAX_REVISIONS}",
    ]
    if article.revision_count >= MAX_REVISIONS:
        lines.append("No revisions left: asking for another one drops the story.")
    change = next((v.change_summary for v in versions if v.change_summary), None)
    if change:
        lines.append(f"The writer's changes in this version: {change}")
    return "\n".join(lines)


# --- validators (EVALUATE) --------------------------------------------------------------------


async def decided_here(session: AsyncSession, ctx: RunContext, note: BaseModel) -> list[str]:
    assert isinstance(note, EditorReview)
    story_id = task_story_id(ctx)
    article = await session.get(Article, note.article_id)
    if article is None or article.company_id != ctx.company_id:
        return [f"article {note.article_id} does not exist"]
    if story_id is not None and article.story_id != story_id:
        return [f"article {note.article_id} is not this task's story's article"]
    decisions = [
        p
        for p in await _task_events(session, ctx, "ARTICLE_REVIEWED")
        if p["article_id"] == str(note.article_id)
    ]
    if not decisions:
        return [
            "decide with accept_draft or request_revision before reporting: nothing was decided"
        ]
    taken = decisions[-1]["verdict"]
    if taken != note.verdict:
        return [f"the verdict taken was {taken!r} (with the tool), not {note.verdict!r}"]
    return []


async def rests_on_its_fact_check(
    session: AsyncSession, ctx: RunContext, note: BaseModel
) -> list[str]:
    assert isinstance(note, EditorReview)
    report = await session.get(FactCheckReport, note.fact_check_report_id)
    if report is None or report.company_id != ctx.company_id:
        return [f"fact-check report {note.fact_check_report_id} does not exist"]
    if report.id not in await produced_reports(session, ctx):
        return ["give the report_id of the run_fact_check you ran in this review"]
    if report.article_id != note.article_id:
        return [f"report {report.id} is about another article"]
    if note.verdict == "accept":
        if not report.passed:
            return ["this fact-check did not pass: a draft is accepted only on a passed one"]
        if note.issues:
            return ["an accepted draft has no issues (list them with request_revision instead)"]
        return []
    if not note.issues:
        return ["a revision lists at least one issue"]
    requested = await _task_events(session, ctx, "ARTICLE_REVISION_REQUESTED")
    if requested and requested[-1]["issues_count"] != len(note.issues):
        return [
            f"report the {requested[-1]['issues_count']} issues you sent with request_revision "
            f"(you listed {len(note.issues)})"
        ]
    return []


def _summary(note: BaseModel) -> str:
    assert isinstance(note, EditorReview)
    if note.verdict == "accept":
        return f"accepted the draft of article {note.article_id}"
    return f"asked for a revision of article {note.article_id} ({len(note.issues)} issues)"


BEHAVIOR = AgentBehavior(
    role=ROLE,
    task_name=TASK,
    capability="editing",
    system_prompt=SYSTEM_PROMPT,
    output_model=EditorReview,
    tools=(
        "read_draft",
        "run_fact_check",
        "accept_draft",
        "request_revision",
        "read_evidence",
        "list_claims",
    ),
    validators=(decided_here, rests_on_its_fact_check),
    max_steps=12,
    repair_limit=2,
    max_output_tokens=4096,
    context=review_context,
    summarize=_summary,
)
