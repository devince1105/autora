"""``create_claim``, ``link_evidence``, ``list_claims`` (T-505).

A claim is a checkable statement about a story; it is supported (or contradicted, or given
context) by quotes from captured evidence. The tools never trust a quote as written: each one is
located in the evidence text (``quotes.py``) and what is stored is the evidence's own words at
that place. A quote that cannot be found fails the whole call and nothing is written, with a
message telling the model how to fix it; the model can try again in the same run.

``create_claim`` can quote evidence in the same call (the usual case). It is idempotent on the
call: a retried call returns the claim it made the first time. CLAIM_CREATED is emitted for a new
claim; the call ``produced`` it, which is what the analyst's "Claims" count shows (AC-5).

Claims of type fact, number and quote need supporting evidence before fact-check (T-510) passes
them; opinions do not. Creating one without evidence is allowed (it can be linked later), and the
result says what is still missing.
"""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from autora.db.repositories.companies import get_policies
from autora.domains.newsroom.advice import no_advice, opinion_refused
from autora.domains.newsroom.events import ClaimCreated
from autora.domains.newsroom.models import (
    Claim,
    ClaimEvidence,
    ClaimType,
    Evidence,
    Story,
    SupportType,
)
from autora.domains.newsroom.quotes import check_length, locate
from autora.infra.ids import uuid7
from autora.runtime.events.catalog import ProducedRef
from autora.runtime.events.outbox import emit
from autora.runtime.events.schema import new_event
from autora.runtime.tools import ToolContext, ToolRegistry, ToolResult

Support = Literal["supports", "contradicts", "context"]
NEEDS_SUPPORT = {ClaimType.FACT, ClaimType.NUMBER, ClaimType.QUOTE}
LIST_LIMIT = 200


class ClaimError(Exception):
    retryable = False


class EvidenceQuote(BaseModel):
    evidence_id: uuid.UUID
    quote: str = Field(
        min_length=1,
        max_length=2000,
        description="Words copied exactly from the evidence text (one or two sentences).",
    )
    support_type: Support = "supports"


class CreateClaimArgs(BaseModel):
    story_id: uuid.UUID
    text: str = Field(min_length=5, max_length=1000, description="The claim, as one statement.")
    claim_type: Literal["fact", "number", "quote", "attribution", "opinion"]
    evidence: list[EvidenceQuote] = Field(
        default=[], max_length=10, description="Quotes that support (or contradict) it."
    )


class LinkEvidenceArgs(EvidenceQuote):
    claim_id: uuid.UUID


class ListClaimsArgs(BaseModel):
    story_id: uuid.UUID


async def _story(ctx: ToolContext, story_id: uuid.UUID) -> Story:
    story = await ctx.session.get(Story, story_id)
    if story is None or story.company_id != ctx.company_id:
        raise ClaimError(f"no story {story_id}")
    return story


async def _locate(ctx: ToolContext, ref: EvidenceQuote) -> tuple[Evidence, int, int, str]:
    evidence = await ctx.session.get(Evidence, ref.evidence_id)
    if evidence is None or evidence.company_id != ctx.company_id:
        raise ClaimError(f"no evidence {ref.evidence_id}")
    problem = check_length(ref.quote)
    if problem:
        raise ClaimError(f"evidence {ref.evidence_id}: {problem}")
    found = locate(ref.quote, evidence.extracted_text)
    if found is None:
        raise ClaimError(
            f"quote not found in evidence {ref.evidence_id}: {ref.quote[:120]!r}. Copy it exactly "
            "from the evidence text (read_evidence or search_evidence); only whitespace and "
            "quotation marks may differ."
        )
    return evidence, found.start, found.end, found.text


async def _link(
    ctx: ToolContext,
    claim: Claim,
    evidence: Evidence,
    start: int,
    end: int,
    quote: str,
    support: str,
) -> dict:
    await ctx.session.execute(
        insert(ClaimEvidence)
        .values(
            id=uuid7(),  # time-ordered: links read back in the order they were made
            company_id=ctx.company_id,
            claim_id=claim.id,
            evidence_id=evidence.id,
            quote=quote,
            quote_start=start,
            quote_end=end,
            support_type=support,
            run_id=ctx.run_id,
        )
        .on_conflict_do_nothing()
    )
    return {
        "evidence_id": str(evidence.id),
        "quote": quote,
        "start": start,
        "end": end,
        "support_type": support,
    }


async def _links_of(ctx: ToolContext, claim_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[dict]]:
    rows = (
        await ctx.session.execute(
            select(ClaimEvidence, Evidence.url, Evidence.title)
            .join(Evidence, Evidence.id == ClaimEvidence.evidence_id)
            .where(ClaimEvidence.claim_id.in_(claim_ids))
            .order_by(ClaimEvidence.id)
        )
    ).all()
    out: dict[uuid.UUID, list[dict]] = {cid: [] for cid in claim_ids}
    for link, url, title in rows:
        out[link.claim_id].append(
            {
                "evidence_id": str(link.evidence_id),
                "url": url,
                "title": title,
                "quote": link.quote,
                "start": link.quote_start,
                "end": link.quote_end,
                "support_type": link.support_type,
            }
        )
    return out


def _missing(claim_type: str, links: list[dict]) -> str | None:
    if ClaimType(claim_type) not in NEEDS_SUPPORT:
        return None
    if any(link["support_type"] == SupportType.SUPPORTS for link in links):
        return None
    return f"a {claim_type} claim needs at least one supporting quote before fact-check passes it"


async def create_claim(args: CreateClaimArgs, ctx: ToolContext) -> ToolResult:
    existing = await ctx.session.scalar(
        select(Claim).where(Claim.idempotency_key == ctx.idempotency_key)
    )
    if existing is None:
        story = await _story(ctx, args.story_id)
        if args.claim_type == ClaimType.OPINION and no_advice(
            await get_policies(ctx.session, ctx.company_id)
        ):
            raise ClaimError(opinion_refused())
        located = [await _locate(ctx, ref) for ref in args.evidence]  # all found, or nothing
        claim = Claim(
            company_id=ctx.company_id,
            story_id=story.id,
            text=args.text,
            claim_type=args.claim_type,
            idempotency_key=ctx.idempotency_key,
            task_id=ctx.task_id,
            run_id=ctx.run_id,
        )
        ctx.session.add(claim)
        await ctx.session.flush()
        for ref, (evidence, start, end, quote) in zip(args.evidence, located, strict=True):
            await _link(ctx, claim, evidence, start, end, quote, ref.support_type)
        await emit(
            ctx.session,
            new_event(
                ClaimCreated(
                    claim_id=claim.id,
                    story_id=story.id,
                    claim_type=claim.claim_type,
                    evidence_ids=list(dict.fromkeys(ev.id for ev, *_ in located)),
                ),
                company_id=ctx.company_id,
                actor=ctx.actor,
                aggregate_type="claim",
                aggregate_id=claim.id,
                agent_id=ctx.agent_id,
                run_id=ctx.run_id,
                task_id=ctx.task_id,
                workflow_run_id=ctx.workflow_run_id,
                correlation_id=ctx.workflow_run_id,
            ),
        )
    else:
        claim = existing
    links = (await _links_of(ctx, [claim.id]))[claim.id]
    output = {
        "claim_id": str(claim.id),
        "story_id": str(claim.story_id),
        "claim_type": claim.claim_type,
        "text": claim.text,
        "evidence": links,
        "reused": existing is not None,
    }
    if missing := _missing(claim.claim_type, links):
        output["missing"] = missing
    return ToolResult(
        output=output,
        summary=f"{'reused' if existing else 'created'} {claim.claim_type} claim {claim.id} "
        f"({len(links)} quotes)",
        produced=[ProducedRef(type="claim", id=claim.id)],
    )


async def link_evidence(args: LinkEvidenceArgs, ctx: ToolContext) -> ToolResult:
    claim = await ctx.session.get(Claim, args.claim_id)
    if claim is None or claim.company_id != ctx.company_id:
        raise ClaimError(f"no claim {args.claim_id}")
    evidence, start, end, quote = await _locate(ctx, args)
    link = await _link(ctx, claim, evidence, start, end, quote, args.support_type)
    row_id = await ctx.session.scalar(
        select(ClaimEvidence.id).where(
            ClaimEvidence.claim_id == claim.id,
            ClaimEvidence.evidence_id == evidence.id,
            ClaimEvidence.quote_start == start,
            ClaimEvidence.support_type == args.support_type,
        )
    )
    links = (await _links_of(ctx, [claim.id]))[claim.id]
    output = {"claim_id": str(claim.id), "linked": link, "evidence": links}
    if missing := _missing(claim.claim_type, links):
        output["missing"] = missing
    return ToolResult(
        output=output,
        summary=f"linked evidence {evidence.id} to claim {claim.id} ({args.support_type})",
        produced=[ProducedRef(type="claim_evidence", id=row_id)],
    )


async def list_claims(args: ListClaimsArgs, ctx: ToolContext) -> ToolResult:
    story = await _story(ctx, args.story_id)
    claims = (
        await ctx.session.scalars(
            select(Claim)
            .where(Claim.story_id == story.id)
            .order_by(Claim.created_at)
            .limit(LIST_LIMIT)
        )
    ).all()
    links = await _links_of(ctx, [c.id for c in claims]) if claims else {}
    return ToolResult(
        output={
            "story_id": str(story.id),
            "claims": [
                {
                    "claim_id": str(c.id),
                    "claim_type": c.claim_type,
                    "text": c.text,
                    "status": c.status,
                    "evidence": links[c.id],
                }
                for c in claims
            ],
        },
        summary=f"{len(claims)} claims for story {story.id}",
    )


def register(registry: ToolRegistry) -> None:
    registry.tool(
        "create_claim",
        description=(
            "Record a checkable claim about the story, with the evidence quotes that support "
            "(or contradict) it. Quotes must be copied exactly from the evidence text."
        ),
        side_effect="write",
        timeout_s=20.0,
        retryable=True,
    )(create_claim)
    registry.tool(
        "link_evidence",
        description="Add an evidence quote to an existing claim (supports, contradicts, context).",
        side_effect="write",
        timeout_s=20.0,
        retryable=True,
    )(link_evidence)
    registry.tool(
        "list_claims",
        description="List a story's claims with their evidence quotes and status.",
        side_effect="read",
        timeout_s=10.0,
    )(list_claims)
