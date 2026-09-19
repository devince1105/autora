"""The researcher (T-506, 3d-office/06 §1): finds and captures the evidence a story rests on.

Task ``research`` (role ``researcher``), input ``params.story_id``. The researcher searches
(``web_search``: candidates only), captures what it will use (``fetch_url``: evidence), reads it
(``read_evidence``, ``search_evidence``) and reports a ``ResearchNote``: the evidence, a summary
of each source, and angles worth taking.

The validators hold the note to what really happened in the run, not to what the model says:
- the story is the task's story;
- every evidence id was produced by this task's own ``fetch_url`` calls (the events say so), so
  a model cannot cite pages it never captured or invent ids;
- enough sources, from more than one site (``params.min_sources``, default 2);
- exactly one summary per listed evidence.
"""

from __future__ import annotations

import uuid
from urllib.parse import urlsplit

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autora.db.models import EventRecord
from autora.domains.newsroom.models import Evidence, SourceItem, Story, StoryItem
from autora.runtime.behaviors import AgentBehavior, RunContext

ROLE = "researcher"
TASK = "research"
DEFAULT_MIN_SOURCES = 2
LEADS = 8

SYSTEM_PROMPT = """You are the researcher of a bilingual newsroom (Traditional Chinese and English).
Your job: find and capture the evidence this story needs. Nothing you do is published directly;
the analyst turns your evidence into claims, and every claim must quote it exactly.

How to work:
1. Start from the leads you are given (pages the company's sources already listed); use
   web_search to find more, especially primary sources (official statements, reports, data).
2. Search results are only candidates. A page becomes evidence only when you capture it with
   fetch_url. Capture the pages you will rely on, from more than one site; prefer primary and
   independent sources over copies of the same report.
3. Read what you captured (read_evidence, search_evidence) before summarising it.
4. Never invent sources, ids, facts or quotes. If a page cannot be captured, move on.

When done, reply with only a JSON object (no other text):
{"story_id": "<the story id>",
 "evidence_ids": ["<evidence_id from fetch_url>", ...],
 "source_summaries": [{"evidence_id": "...", "summary": "<what this source says and who it is>"}],
 "suggested_angles": ["<an angle worth writing>", ...]}
Write summaries and angles in Traditional Chinese (zh-TW); never Simplified Chinese.
"""


class SourceSummary(BaseModel):
    evidence_id: uuid.UUID
    summary: str = Field(min_length=10, max_length=600)


class ResearchNote(BaseModel):
    story_id: uuid.UUID
    evidence_ids: list[uuid.UUID] = Field(min_length=1, max_length=20)
    source_summaries: list[SourceSummary] = Field(min_length=1, max_length=20)
    suggested_angles: list[str] = Field(min_length=1, max_length=5)


def task_story_id(ctx: RunContext) -> uuid.UUID | None:
    """The story a newsroom task is about: ``params.story_id`` (or ``story_id``) of its input."""
    raw = (ctx.task.input.get("params") or {}).get("story_id") or ctx.task.input.get("story_id")
    try:
        return uuid.UUID(str(raw)) if raw else None
    except ValueError:
        return None


def min_sources(ctx: RunContext) -> int:
    value = (ctx.task.input.get("params") or {}).get("min_sources", DEFAULT_MIN_SOURCES)
    return max(1, int(value)) if isinstance(value, int) else DEFAULT_MIN_SOURCES


async def produced_evidence(session: AsyncSession, ctx: RunContext) -> set[uuid.UUID]:
    """Evidence this task's own tool calls produced (any attempt)."""
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
        if ref.get("type") == "evidence"
    }


# --- context (OBSERVE) ------------------------------------------------------------------------


async def research_context(session: AsyncSession, ctx: RunContext) -> str | None:
    story_id = task_story_id(ctx)
    story = await session.get(Story, story_id) if story_id else None
    if story is None or story.company_id != ctx.company_id:
        return "No story found for this task: report that, do not guess one."
    lines = [f"Story: {story.title}", f"Story id: {story.id}"]
    if story.summary:
        lines.append(f"Summary: {story.summary}")
    seed = story.seed or {}
    if seed.get("query"):
        lines.append(f"Suggested search: {seed['query']}")
    urls = list(seed.get("urls") or [])
    leads = (
        await session.execute(
            select(SourceItem.title, SourceItem.url)
            .join(StoryItem, StoryItem.source_item_id == SourceItem.id)
            .where(StoryItem.story_id == story.id)
            .order_by(SourceItem.published_at.desc().nulls_last())
            .limit(LEADS)
        )
    ).all()
    if urls or leads:
        lines.append("Leads:")
        lines += [f"- {url}" for url in urls]
        lines += [f"- {title} — {url}" for title, url in leads]
    lines.append(f"Capture at least {min_sources(ctx)} sources, from more than one site.")
    return "\n".join(lines)


# --- validators (EVALUATE) --------------------------------------------------------------------


async def about_the_task_story(
    session: AsyncSession, ctx: RunContext, note: BaseModel
) -> list[str]:
    assert isinstance(note, ResearchNote)
    expected = task_story_id(ctx)
    if expected is not None and note.story_id != expected:
        return [f"story_id must be this task's story {expected}"]
    return []


async def evidence_captured_here(
    session: AsyncSession, ctx: RunContext, note: BaseModel
) -> list[str]:
    assert isinstance(note, ResearchNote)
    captured = await produced_evidence(session, ctx)
    issues = [
        f"evidence {eid} was not captured in this task: capture it with fetch_url and use the "
        "evidence_id it returns"
        for eid in dict.fromkeys(note.evidence_ids)
        if eid not in captured
    ]
    if issues:
        return issues
    distinct = set(note.evidence_ids)
    needed = min_sources(ctx)
    if len(distinct) < needed:
        return [f"capture at least {needed} sources (you listed {len(distinct)})"]
    if needed >= 2:
        urls = (await session.scalars(select(Evidence.url).where(Evidence.id.in_(distinct)))).all()
        if len({urlsplit(u).hostname for u in urls}) < 2:
            return ["all your sources are from one site: capture an independent one too"]
    return []


async def one_summary_per_source(
    session: AsyncSession, ctx: RunContext, note: BaseModel
) -> list[str]:
    assert isinstance(note, ResearchNote)
    listed = set(note.evidence_ids)
    summarised = [s.evidence_id for s in note.source_summaries]
    issues = [
        f"summary for {e}, which is not in evidence_ids" for e in summarised if e not in listed
    ]
    issues += [f"no summary for evidence {e}" for e in listed if e not in summarised]
    issues += [
        f"more than one summary for evidence {e}"
        for e in set(summarised)
        if summarised.count(e) > 1
    ]
    return issues


def _summary(note: BaseModel) -> str:
    assert isinstance(note, ResearchNote)
    return f"{len(set(note.evidence_ids))} sources; {note.suggested_angles[0][:120]}"


BEHAVIOR = AgentBehavior(
    role=ROLE,
    task_name=TASK,
    capability="research_extraction",
    system_prompt=SYSTEM_PROMPT,
    output_model=ResearchNote,
    tools=("web_search", "fetch_url", "read_evidence", "search_evidence"),
    validators=(about_the_task_story, evidence_captured_here, one_summary_per_source),
    max_steps=12,
    repair_limit=2,
    max_output_tokens=4096,
    context=research_context,
    summarize=_summary,
)
