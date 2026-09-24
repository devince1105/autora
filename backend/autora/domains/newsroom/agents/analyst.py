"""The analyst (T-507, 3d-office/06 §1): turns the researcher's evidence into checkable claims.

Task ``analysis`` (role ``analyst``), input ``params.story_id``, after the research task. The
analyst reads the evidence, records claims with ``create_claim`` (each quoting the evidence
exactly), and reports an ``AnalysisNote``: the claims, the angle, the key numbers and the
contradictions it found.

The validators check the claims as the fact-check will (``factcheck.check_claim``, layers 1-2),
so what reaches the writer can pass: a claim that would fail must be fixed (another supporting
quote, a trusted source) or left out, and a contested point must be written as who said what
(an attribution claim). Also: the claims were made in this task for this story, there are enough
of them (``params.min_claims``, default 3), and key numbers point at number claims.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autora.db.models import Task
from autora.db.repositories.companies import get_policies
from autora.domains.newsroom.advice import NO_ADVICE_BRIEF, no_advice
from autora.domains.newsroom.agents.researcher import task_story_id
from autora.domains.newsroom.factcheck import check_claim
from autora.domains.newsroom.models import Claim, ClaimType, Evidence, Story
from autora.domains.newsroom.policy import trust_policy
from autora.domains.newsroom.tools.factcheck import claim_quotes
from autora.runtime.behaviors import AgentBehavior, RunContext

ROLE = "analyst"
TASK = "analysis"
DEFAULT_MIN_CLAIMS = 3

SYSTEM_PROMPT = """You are the analyst of a bilingual newsroom (Traditional Chinese and English).
Your job: turn the researcher's evidence into claims the writer can build the article on. Every
fact, number and quote in the article will cite one of your claims, and a fact-check will verify
each claim against its quotes, so:

1. Read the evidence (read_evidence, search_evidence). Only the captured evidence counts.
2. Record each checkable statement with create_claim: one statement per claim, typed as fact,
   number, quote (someone's words), attribution (who said or did something) or opinion. Give it
   the evidence quotes that support it, copied exactly from the evidence text (a sentence or
   two; whitespace and quotation marks may differ, nothing else).
3. Numbers: every number in a number claim must appear in a supporting quote. A figure you list
   under key_numbers must be a claim you typed "number" (that is what gets the number check).
4. Low-trust sources cannot support a claim alone: add a trusted or a second independent source.
5. When sources disagree, do not pick a side as fact: record who says what (attribution claims,
   each with its quote), link the disagreeing quote as "contradicts" where it applies, and list
   the disagreement under contradictions.
6. Never invent quotes, numbers or ids.
7. Work in few turns: call create_claim for several claims in the same turn — each call is its own
   claim, and you have a limited number of turns. Record what the article needs, not everything
   the evidence holds: a 13F comparison lists every position, and the story is its largest new
   positions, exits, increases and cuts — about 8 to 12 claims, not one per line.

When done, reply with only a JSON object (no other text):
{"story_id": "<the story id>",
 "claim_ids": ["<claim_id from create_claim>", ...],
 "angle": "<the angle the article should take>",
 "key_numbers": [{"claim_id": "...", "value": "<the number, e.g. 3,000 戶>"}],
 "contradictions": [{"note": "<what disagrees>", "evidence_ids": ["..."], "claim_ids": ["..."]}]}
Write the angle and notes in Traditional Chinese (zh-TW); never Simplified Chinese.
"""


class KeyNumber(BaseModel):
    claim_id: uuid.UUID
    value: str = Field(min_length=1, max_length=60)


class Contradiction(BaseModel):
    note: str = Field(min_length=5, max_length=500)
    evidence_ids: list[uuid.UUID] = Field(min_length=1, max_length=5)
    claim_ids: list[uuid.UUID] = Field(default=[], max_length=5)


class AnalysisNote(BaseModel):
    story_id: uuid.UUID
    claim_ids: list[uuid.UUID] = Field(min_length=1, max_length=40)
    angle: str = Field(min_length=5, max_length=300)
    key_numbers: list[KeyNumber] = Field(default=[], max_length=10)
    contradictions: list[Contradiction] = Field(default=[], max_length=10)


def min_claims(ctx: RunContext) -> int:
    value = (ctx.task.input.get("params") or {}).get("min_claims", DEFAULT_MIN_CLAIMS)
    return max(1, value) if isinstance(value, int) else DEFAULT_MIN_CLAIMS


# --- context (OBSERVE) ------------------------------------------------------------------------


async def analysis_context(session: AsyncSession, ctx: RunContext) -> str | None:
    story_id = task_story_id(ctx)
    story = await session.get(Story, story_id) if story_id else None
    if story is None or story.company_id != ctx.company_id:
        return "No story found for this task: report that, do not guess one."
    lines = [f"Story: {story.title}", f"Story id: {story.id}"]
    if no_advice(await get_policies(session, ctx.company_id)):
        lines.append(NO_ADVICE_BRIEF)
    upstream = (
        (await session.scalars(select(Task).where(Task.id.in_(ctx.task.depends_on)))).all()
        if ctx.task.depends_on
        else []
    )
    notes = [t.output for t in upstream if t.output and "evidence_ids" in t.output]
    evidence_ids = list(dict.fromkeys(e for n in notes for e in n.get("evidence_ids", [])))
    summaries = {
        s["evidence_id"]: s["summary"] for n in notes for s in n.get("source_summaries", [])
    }
    angles = [a for n in notes for a in n.get("suggested_angles", [])]
    if evidence_ids:
        rows = (
            await session.execute(
                select(Evidence.id, Evidence.title, Evidence.url).where(
                    Evidence.id.in_([uuid.UUID(e) for e in evidence_ids]),
                    Evidence.company_id == ctx.company_id,
                )
            )
        ).all()
        found = {str(r.id): r for r in rows}
        lines.append("Evidence (from the researcher):")
        for eid in evidence_ids:
            row = found.get(eid)
            if row is None:
                continue
            summary = summaries.get(eid)
            lines.append(f"- {eid} | {row.title or row.url} | {row.url}")
            if summary:
                lines.append(f"  researcher's summary: {summary}")
    else:
        lines.append("No research note found: use search_evidence to find this story's evidence.")
    if angles:
        lines.append("Suggested angles: " + "; ".join(angles))
    lines.append(f"Record at least {min_claims(ctx)} claims.")
    return "\n".join(lines)


# --- validators (EVALUATE) --------------------------------------------------------------------


async def claims_made_here(session: AsyncSession, ctx: RunContext, note: BaseModel) -> list[str]:
    assert isinstance(note, AnalysisNote)
    expected = task_story_id(ctx)
    issues = []
    if expected is not None and note.story_id != expected:
        issues.append(f"story_id must be this task's story {expected}")
    listed = list(dict.fromkeys(note.claim_ids))
    claims = {
        c.id: c for c in (await session.scalars(select(Claim).where(Claim.id.in_(listed)))).all()
    }
    for claim_id in listed:
        claim = claims.get(claim_id)
        if claim is None or claim.company_id != ctx.company_id:
            issues.append(
                f"claim {claim_id} does not exist: use the claim_id create_claim returned"
            )
        elif claim.task_id != ctx.task.id:
            issues.append(f"claim {claim_id} was not made in this task")
        elif expected is not None and claim.story_id != expected:
            issues.append(f"claim {claim_id} is about another story")
    if not issues and len(listed) < min_claims(ctx):
        issues.append(f"record at least {min_claims(ctx)} claims (you listed {len(listed)})")
    return issues


async def claims_would_pass(session: AsyncSession, ctx: RunContext, note: BaseModel) -> list[str]:
    assert isinstance(note, AnalysisNote)
    listed = list(dict.fromkeys(note.claim_ids))
    claims = (
        await session.scalars(
            select(Claim).where(Claim.id.in_(listed), Claim.company_id == ctx.company_id)
        )
    ).all()
    if not claims:
        return []
    min_trust, default_trust = trust_policy(await get_policies(session, ctx.company_id))
    quotes = await claim_quotes(session, [c.id for c in claims], default_trust)
    issues = []
    for claim in claims:
        verdict = check_claim(
            str(claim.id), claim.claim_type, claim.text, quotes[claim.id], min_trust=min_trust
        )
        if not verdict.passed:
            issues.append(
                f"claim {claim.id} would fail fact-check: {'; '.join(verdict.problems)}. Fix it "
                "(link supporting evidence) or leave it out"
            )
    types = {c.id: c.claim_type for c in claims}
    for number in note.key_numbers:
        if number.claim_id not in types:
            issues.append(f"key number {number.value!r}: claim {number.claim_id} is not listed")
        elif types[number.claim_id] != ClaimType.NUMBER:
            issues.append(
                f"key number {number.value!r}: claim {number.claim_id} is a "
                f"{types[number.claim_id]} claim. A figure the article will feature must be "
                'checkable as a number: record it again with create_claim as claim_type="number" '
                "(same quote) and list that claim here, or leave it out of key_numbers"
            )
    return issues


def _summary(note: BaseModel) -> str:
    assert isinstance(note, AnalysisNote)
    return f"{len(set(note.claim_ids))} claims; {note.angle[:120]}"


BEHAVIOR = AgentBehavior(
    role=ROLE,
    task_name=TASK,
    capability="reasoning",
    system_prompt=SYSTEM_PROMPT,
    output_model=AnalysisNote,
    tools=("read_evidence", "search_evidence", "create_claim", "link_evidence", "list_claims"),
    validators=(claims_made_here, claims_would_pass),
    max_steps=14,
    repair_limit=2,
    # the note carries every claim and key number, and a reasoning model spends part of this
    # budget thinking: 4096 cut a real reply off mid-note (T-519)
    max_output_tokens=8192,
    context=analysis_context,
    summarize=_summary,
)
