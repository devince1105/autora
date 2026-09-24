"""The writer (T-509, 3d-office/06 §1, platform/04 §4): drafts the story's article in every
language from the analyst's claims.

Task ``draft`` (role ``writer``), input ``params.story_id``, after the analysis task. A revision
(the editor sent the draft back) is another ``draft`` task whose input also carries
``params.issues`` (``[{message, lang?, block_ref?, kind?}]``): the writer reads the current draft
and writes the next version.

``write_draft`` already refuses a draft that breaks the article rules (languages per policy,
every paragraph cites a claim, every language cites the same claims, short quotes, only the
story's claims that were not rejected). The validators check the reported ``ArticleDraft``
against what the run really did:
- the draft group was written by this task's own ``write_draft`` call (the events say so), it is
  the article's current draft, and the reported ids and languages are that group's;
- the draft cites every claim the analyst marked as a key number;
- a revision says what changed (``change_summary``).
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autora.db.models import EventRecord, Task
from autora.db.repositories.companies import get_policies
from autora.domains.newsroom.advice import NO_ADVICE_BRIEF, no_advice
from autora.domains.newsroom.agents.researcher import task_story_id
from autora.domains.newsroom.models import Article, ArticleVersion, Claim, ClaimStatus, Story
from autora.domains.newsroom.policy import language_policy
from autora.runtime.behaviors import AgentBehavior, RunContext

ROLE = "writer"
TASK = "draft"
MAX_LISTED_CLAIMS = 40

SYSTEM_PROMPT = """You are the writer of a bilingual newsroom (Traditional Chinese and English).
Your job: write the story's article from the analyst's claims, in every language the company
publishes, with one write_draft call.

Rules:
1. Every fact, number and quote in the article comes from a claim. Each paragraph and quote
   block lists the claims it states in claim_ids; headings cite none. Do not add facts that no
   claim states, and do not change a claim's numbers or who said what.
2. Write the primary language first (zh-TW: Traditional Chinese, never Simplified Chinese), then
   the other languages from the same claims: every language cites exactly the same claims. The
   versions are faithful to each other, not word-for-word translations.
3. A contested point is written as who says what, never as settled fact.
4. Quote blocks are short (a sentence or two), taken from quote claims.
5. Lead with the angle; give the key numbers; keep it tight (about 4-8 blocks per language).
6. If write_draft refuses the draft, fix every problem it lists and call it again.
7. For a revision: read the current draft (read_draft), fix every issue the editor raised, and
   say what you changed in change_summary.

When done, reply with only a JSON object (no other text):
{"article_id": "<article_id from write_draft>",
 "draft_group_id": "<draft_group_id from write_draft>",
 "versions": {"zh-TW": "<version id>", "en": "<version id>"}}
"""


class ArticleDraft(BaseModel):
    article_id: uuid.UUID
    draft_group_id: uuid.UUID
    versions: dict[str, uuid.UUID] = Field(min_length=1, max_length=5)


def revision_issues(ctx: RunContext) -> list[dict[str, Any]]:
    """The editor's issues for a revision task (empty: a first draft)."""
    raw = (ctx.task.input.get("params") or {}).get("issues") or []
    return [i if isinstance(i, dict) else {"message": str(i)} for i in raw][:20]


async def upstream_analysis(session: AsyncSession, ctx: RunContext) -> list[dict[str, Any]]:
    """The analysis notes of the tasks this one depends on."""
    if not ctx.task.depends_on:
        return []
    outputs = (
        await session.scalars(select(Task.output).where(Task.id.in_(ctx.task.depends_on)))
    ).all()
    return [o for o in outputs if o and "claim_ids" in o]


async def produced_versions(session: AsyncSession, ctx: RunContext) -> set[uuid.UUID]:
    """Article versions this task's own tool calls produced (any attempt)."""
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
        if ref.get("type") == "article_version"
    }


# --- context (OBSERVE) ------------------------------------------------------------------------


async def draft_context(session: AsyncSession, ctx: RunContext) -> str | None:
    story_id = task_story_id(ctx)
    story = await session.get(Story, story_id) if story_id else None
    if story is None or story.company_id != ctx.company_id:
        return "No story found for this task: report that, do not guess one."
    policies = await get_policies(session, ctx.company_id)
    policy = language_policy(policies)
    others = [lang for lang in policy.langs if lang != policy.primary]
    lines = [
        f"Story: {story.title}",
        f"Story id: {story.id}",
        f"Languages: {policy.primary} (primary)"
        + (f", then {', '.join(others)}" if others else "")
        + ("; all are required." if policy.require_all else "; the others are optional."),
    ]
    if no_advice(policies):
        lines.append(NO_ADVICE_BRIEF)

    notes = await upstream_analysis(session, ctx)
    wanted = list(dict.fromkeys(c for n in notes for c in n.get("claim_ids", [])))
    key_numbers = {k["claim_id"]: k["value"] for n in notes for k in n.get("key_numbers", [])}
    query = select(Claim).where(
        Claim.story_id == story.id,
        Claim.company_id == ctx.company_id,
        Claim.status != ClaimStatus.REJECTED,
    )
    if wanted:
        query = query.where(Claim.id.in_([uuid.UUID(c) for c in wanted]))
    claims = (await session.scalars(query.order_by(Claim.id).limit(MAX_LISTED_CLAIMS))).all()
    for note in notes:
        lines.append(f"Angle (from the analyst): {note.get('angle')}")
    if claims:
        lines.append("Claims (cite these ids):")
        for claim in claims:
            key = key_numbers.get(str(claim.id))
            mark = f" [key number: {key}]" if key else ""
            lines.append(f"- {claim.id} | {claim.claim_type}{mark} | {claim.text}")
    else:
        lines.append("No claims found for this story: report that you cannot write it.")
    contradictions = [c for n in notes for c in n.get("contradictions", [])]
    if contradictions:
        lines.append("Contested (write as who says what):")
        lines += [f"- {c['note']}" for c in contradictions]

    issues = revision_issues(ctx)
    if issues:
        article = await session.scalar(select(Article).where(Article.story_id == story.id))
        lines.append(
            "This is a revision"
            + (f" of article {article.id}" if article else "")
            + ". The editor's issues:"
        )
        for issue in issues:
            where = " ".join(str(issue[k]) for k in ("lang", "block_ref") if issue.get(k))
            lines.append(f"- {f'[{where}] ' if where else ''}{issue.get('message', '')}")
        lines.append("Read the current draft (read_draft), fix every issue, give change_summary.")
    return "\n".join(lines)


# --- validators (EVALUATE) --------------------------------------------------------------------


async def _group(session: AsyncSession, draft: ArticleDraft) -> list[ArticleVersion]:
    return list(
        (
            await session.scalars(
                select(ArticleVersion).where(ArticleVersion.draft_group_id == draft.draft_group_id)
            )
        ).all()
    )


async def draft_written_here(session: AsyncSession, ctx: RunContext, note: BaseModel) -> list[str]:
    assert isinstance(note, ArticleDraft)
    rows = await _group(session, note)
    produced = await produced_versions(session, ctx)
    if not rows or any(r.company_id != ctx.company_id for r in rows):
        return [
            f"draft group {note.draft_group_id} does not exist: use the draft_group_id "
            "write_draft returned"
        ]
    if not {r.id for r in rows} <= produced:
        return [f"draft group {note.draft_group_id} was not written in this task: call write_draft"]
    article = await session.get(Article, rows[0].article_id)
    issues = []
    expected = task_story_id(ctx)
    if expected is not None and article.story_id != expected:
        issues.append(f"the draft is for another story (this task's story is {expected})")
    if note.article_id != article.id:
        issues.append(f"article_id must be {article.id}, the article of the draft")
    if article.current_draft_group_id != note.draft_group_id:
        issues.append("report the latest draft you wrote (a later write_draft replaced this one)")
    actual = {r.lang: r.id for r in rows}
    if note.versions != actual:
        listed = ", ".join(f"{lang}: {vid}" for lang, vid in actual.items())
        issues.append(f"versions must be the draft's own: {listed}")
    return issues


async def cites_key_numbers(session: AsyncSession, ctx: RunContext, note: BaseModel) -> list[str]:
    assert isinstance(note, ArticleDraft)
    keys = {
        k["claim_id"]: k["value"]
        for n in await upstream_analysis(session, ctx)
        for k in n.get("key_numbers", [])
    }
    rows = await _group(session, note)
    if not keys or not rows:
        return []
    cited = {str(c) for c in rows[0].claim_ids}
    rejected = {
        str(c)
        for c in (
            await session.scalars(
                select(Claim.id).where(
                    Claim.id.in_([uuid.UUID(k) for k in keys]),
                    Claim.status == ClaimStatus.REJECTED,
                )
            )
        ).all()
    }  # a claim the fact-check rejected cannot be cited
    return [
        f"the draft leaves out the key number {value!r} (claim {claim_id}): cite it"
        for claim_id, value in keys.items()
        if claim_id not in cited and claim_id not in rejected
    ]


async def revision_says_what_changed(
    session: AsyncSession, ctx: RunContext, note: BaseModel
) -> list[str]:
    assert isinstance(note, ArticleDraft)
    if not revision_issues(ctx):
        return []
    rows = await _group(session, note)
    if rows and not (rows[0].change_summary or "").strip():
        return ["a revision needs change_summary: say what you changed for each issue"]
    return []


def _summary(note: BaseModel) -> str:
    assert isinstance(note, ArticleDraft)
    return f"draft of article {note.article_id} in {', '.join(note.versions)}"


BEHAVIOR = AgentBehavior(
    role=ROLE,
    task_name=TASK,
    capability="drafting",
    system_prompt=SYSTEM_PROMPT,
    output_model=ArticleDraft,
    tools=("write_draft", "read_draft", "list_claims", "read_evidence"),
    validators=(draft_written_here, cites_key_numbers, revision_says_what_changed),
    max_steps=8,
    repair_limit=2,
    max_output_tokens=8192,
    context=draft_context,
    summarize=_summary,
)
