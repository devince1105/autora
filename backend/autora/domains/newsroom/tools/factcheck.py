"""``run_fact_check`` (T-510): the deterministic fact-check of a draft, for the editor.

It checks every claim the draft cites (``factcheck.py``: layers 1 and 2), sets each claim's
status (VERIFIED / REJECTED, with CLAIM_VERIFIED / CLAIM_REJECTED when it changes), stores a
``fact_check_reports`` row and returns it. A rejected claim cannot be cited again (T-508), so a
revision must drop or replace it.

It also prepares layer 3 for the editor: each passing claim with its supporting quotes and, per
language, the draft's sentences that state it, so the editor can judge in every language that
the words say what the evidence says. The cross-check's related passages (other evidence of the
story close to the claim) come along as advisory notes.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from pydantic import BaseModel, Field
from sqlalchemy import Float, bindparam, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from autora.db.repositories.companies import get_policies
from autora.db.vector import HalfVector
from autora.domains.newsroom.articles import ArticleState
from autora.domains.newsroom.events import ClaimRejected, ClaimVerified
from autora.domains.newsroom.factcheck import CHECKED, QuoteFacts, check_claim, source_key
from autora.domains.newsroom.models import (
    EMBED_DIM,
    Article,
    ArticleVersion,
    Claim,
    ClaimEvidence,
    ClaimStatus,
    ClaimType,
    Evidence,
    EvidenceChunk,
    FactCheckReport,
    Source,
    SupportType,
)
from autora.domains.newsroom.policy import trust_policy
from autora.domains.newsroom.tools.evidence import embed_caller
from autora.infra.ids import uuid7
from autora.runtime.events.catalog import ProducedRef
from autora.runtime.events.outbox import emit
from autora.runtime.events.schema import EventPayload, new_event
from autora.runtime.models.embeddings import Embedder, EmbeddingError
from autora.runtime.tools import ToolContext, ToolFn, ToolRegistry, ToolResult

RELATED_THRESHOLD = 0.55
RELATED_PER_CLAIM = 2
CHECKABLE_STATES = {ArticleState.DRAFT, ArticleState.IN_REVIEW}
EDITOR_NOTE = (
    "Layers 1-2 are done. Layer 3 is yours: for each claim in semantic_review, check in every "
    "language that the draft's sentences say what the quotes say (no stretching, no missing "
    "context). Related passages are other evidence close to the claim: make sure none of them "
    "changes the picture."
)


class FactCheckError(Exception):
    retryable = False


class RunFactCheckArgs(BaseModel):
    article_id: uuid.UUID
    version: int | None = Field(default=None, ge=1, description="Default: the latest draft.")


async def _emit(ctx: ToolContext, payload: EventPayload, claim_id: uuid.UUID) -> None:
    await emit(
        ctx.session,
        new_event(
            payload,
            company_id=ctx.company_id,
            actor=ctx.actor,
            aggregate_type="claim",
            aggregate_id=claim_id,
            agent_id=ctx.agent_id,
            run_id=ctx.run_id,
            task_id=ctx.task_id,
            workflow_run_id=ctx.workflow_run_id,
            correlation_id=ctx.workflow_run_id,
        ),
    )


async def _related(
    session: AsyncSession,
    embedder: Embedder,
    ctx: ToolContext,
    claims: list[Claim],
    pool: set[uuid.UUID],
    linked: dict[uuid.UUID, set[uuid.UUID]],
) -> dict[uuid.UUID, list[dict]] | None:
    """Per claim, the closest passages of the story's other evidence (None: search unavailable)."""
    if not claims or not pool:
        return {c.id: [] for c in claims}
    try:
        vectors = await embedder.embed(
            session, [c.text for c in claims], purpose="query", caller=await embed_caller(ctx)
        )
    except EmbeddingError:
        return None
    # the story's evidence is a handful of pages: an exact scan over their chunks (found by the
    # evidence_id index) is faster than walking the HNSW index of every company's chunks
    out: dict[uuid.UUID, list[dict]] = {}
    for claim, vector in zip(claims, vectors, strict=True):
        others = pool - linked.get(claim.id, set())
        if not others:
            out[claim.id] = []
            continue
        query = bindparam("claim_vector", vector, type_=HalfVector(EMBED_DIM))
        similarity = (
            literal(1.0) - EvidenceChunk.embedding.op("<=>", return_type=Float)(query)
        ).label("similarity")
        rows = (
            await session.execute(
                select(EvidenceChunk.evidence_id, EvidenceChunk.text, similarity)
                .where(
                    EvidenceChunk.evidence_id.in_(others),
                    EvidenceChunk.embedding_model == embedder.model_id,
                )
                .order_by(similarity.desc())  # not the bare distance: no HNSW scan
                .limit(RELATED_PER_CLAIM)
            )
        ).all()
        out[claim.id] = [
            {
                "evidence_id": str(r.evidence_id),
                "passage": r.text[:400],
                "similarity": round(float(r.similarity), 3),
            }
            for r in rows
            if r.similarity >= RELATED_THRESHOLD
        ]
    return out


def _facts(
    link: ClaimEvidence,
    evidence_text: str,
    url: str,
    source_id: uuid.UUID | None,
    trust: Decimal | None,
    default_trust: Decimal,
) -> QuoteFacts:
    return QuoteFacts(
        evidence_id=str(link.evidence_id),
        quote=link.quote,
        support_type=link.support_type,
        intact=evidence_text[link.quote_start : link.quote_end] == link.quote,
        trust=Decimal(trust) if trust is not None else default_trust,
        source_key=source_key(source_id, url),
    )


def _quote_rows(claim_ids):
    return (
        select(
            ClaimEvidence,
            Evidence.extracted_text,
            Evidence.url,
            Evidence.source_id,
            Source.trust_level,
        )
        .join(Evidence, Evidence.id == ClaimEvidence.evidence_id)
        .outerjoin(Source, Source.id == Evidence.source_id)
        .where(ClaimEvidence.claim_id.in_(claim_ids))
        .order_by(ClaimEvidence.id)
    )


async def claim_quotes(
    session: AsyncSession, claim_ids: list[uuid.UUID], default_trust: Decimal
) -> dict[uuid.UUID, list[QuoteFacts]]:
    """What the deterministic check needs about each claim's quotes (also used by the analyst's
    validator, T-507, so claims are checked the same way before they reach a draft)."""
    quotes: dict[uuid.UUID, list[QuoteFacts]] = {c: [] for c in claim_ids}
    for link, text_, url, source_id, trust in (await session.execute(_quote_rows(claim_ids))).all():
        quotes[link.claim_id].append(_facts(link, text_, url, source_id, trust, default_trust))
    return quotes


def fact_check_tool(embedder: Embedder) -> ToolFn:
    async def run_fact_check(args: RunFactCheckArgs, ctx: ToolContext) -> ToolResult:
        session = ctx.session
        earlier = await session.scalar(
            select(FactCheckReport).where(FactCheckReport.idempotency_key == ctx.idempotency_key)
        )
        if earlier is not None:
            return _result(earlier, reused=True)

        article = await session.get(Article, args.article_id)
        if article is None or article.company_id != ctx.company_id:
            raise FactCheckError(f"no article {args.article_id}")
        if ArticleState(article.state) not in CHECKABLE_STATES:
            raise FactCheckError(f"the article is {article.state}: only drafts are fact-checked")
        query = select(ArticleVersion).where(ArticleVersion.article_id == article.id)
        if args.version is not None:
            query = query.where(ArticleVersion.version == args.version)
        else:
            query = query.where(ArticleVersion.draft_group_id == article.current_draft_group_id)
        versions = (await session.scalars(query.order_by(ArticleVersion.id))).all()
        if not versions:
            raise FactCheckError(f"article {article.id} has no draft to check")
        primary = next(v for v in versions if v.translation_of_version_id is None)

        draft_problems = []
        cited_sets = {v.lang: set(v.claim_ids) for v in versions}
        if len({frozenset(s) for s in cited_sets.values()}) > 1:
            draft_problems.append("the languages cite different claims")
        cited = set().union(*cited_sets.values())

        min_trust, default_trust = trust_policy(await get_policies(session, ctx.company_id))
        claims = (
            await session.scalars(select(Claim).where(Claim.id.in_(cited)).order_by(Claim.id))
        ).all()
        story_claims = select(Claim.id).where(Claim.story_id == article.story_id)
        rows = (await session.execute(_quote_rows(story_claims))).all()
        quotes: dict[uuid.UUID, list[QuoteFacts]] = {c.id: [] for c in claims}
        linked: dict[uuid.UUID, set[uuid.UUID]] = {}
        pool: set[uuid.UUID] = set()
        for link, evidence_text, url, source_id, trust in rows:
            pool.add(link.evidence_id)
            linked.setdefault(link.claim_id, set()).add(link.evidence_id)
            if link.claim_id in quotes:
                quotes[link.claim_id].append(
                    _facts(link, evidence_text, url, source_id, trust, default_trust)
                )

        checked = [c for c in claims if ClaimType(c.claim_type) in CHECKED]
        related = await _related(session, embedder, ctx, checked, pool, linked)
        sentences = {
            v.lang: [
                (block["text"], {uuid.UUID(c) for c in block.get("claim_ids", [])})
                for block in v.body
            ]
            for v in versions
        }
        results, review = [], []
        for claim in claims:
            verdict = check_claim(
                str(claim.id), claim.claim_type, claim.text, quotes[claim.id], min_trust=min_trust
            )
            if related is None and claim in checked:
                verdict.notes.append("the cross-check search was unavailable; check by hand")
            results.append(
                {
                    "claim_id": str(claim.id),
                    "claim_type": claim.claim_type,
                    "text": claim.text,
                    "verdict": "pass" if verdict.passed else "fail",
                    "problems": verdict.problems,
                    "notes": verdict.notes,
                    "related": (related or {}).get(claim.id, []),
                }
            )
            evidence_ids = list(dict.fromkeys(uuid.UUID(q.evidence_id) for q in quotes[claim.id]))
            new_status = ClaimStatus.VERIFIED if verdict.passed else ClaimStatus.REJECTED
            if claim.status != new_status:
                claim.status = new_status.value
                if verdict.passed:
                    event = ClaimVerified(
                        claim_id=claim.id,
                        story_id=claim.story_id,
                        claim_type=claim.claim_type,
                        evidence_ids=evidence_ids,
                    )
                else:
                    event = ClaimRejected(
                        claim_id=claim.id,
                        story_id=claim.story_id,
                        claim_type=claim.claim_type,
                        evidence_ids=evidence_ids,
                        problems=[p[:300] for p in verdict.problems],
                    )
                await _emit(ctx, event, claim.id)
            if verdict.passed and ClaimType(claim.claim_type) is not ClaimType.OPINION:
                review.append(
                    {
                        "claim_id": str(claim.id),
                        "claim": claim.text,
                        "quotes": [
                            {"evidence_id": q.evidence_id, "quote": q.quote}
                            for q in quotes[claim.id]
                            if q.support_type == SupportType.SUPPORTS
                        ],
                        "sentences": {
                            lang: [t for t, ids in blocks if claim.id in ids]
                            for lang, blocks in sentences.items()
                        },
                    }
                )

        passed = not draft_problems and all(r["verdict"] == "pass" for r in results)
        report = FactCheckReport(
            id=uuid7(),
            company_id=ctx.company_id,
            article_id=article.id,
            article_version_id=primary.id,
            draft_group_id=primary.draft_group_id,
            passed=passed,
            results=[*results, *({"draft": p} for p in draft_problems)],
            semantic_review=review,
            idempotency_key=ctx.idempotency_key,
            task_id=ctx.task_id,
            run_id=ctx.run_id,
        )
        session.add(report)
        await session.flush()
        return _result(report, reused=False)

    return run_fact_check


def _result(report: FactCheckReport, *, reused: bool) -> ToolResult:
    claims = [r for r in report.results if "claim_id" in r]
    failed = [r for r in claims if r["verdict"] == "fail"]
    return ToolResult(
        output={
            "report_id": str(report.id),
            "article_id": str(report.article_id),
            "passed": report.passed,
            "checked": len(claims),
            "failed": len(failed),
            "results": report.results,
            "semantic_review": report.semantic_review,
            "note": EDITOR_NOTE,
            "reused": reused,
        },
        summary=f"fact-check {'passed' if report.passed else 'failed'}: "
        f"{len(claims) - len(failed)}/{len(claims)} claims pass",
        produced=[ProducedRef(type="fact_check_report", id=report.id)],
    )


def register(registry: ToolRegistry, embedder: Embedder) -> None:
    registry.tool(
        "run_fact_check",
        description=(
            "Fact-check the article's draft deterministically: every cited claim's evidence, "
            "quotes, sources and numbers. Returns pass/fail per claim and what is left for you "
            "to judge (does each quote really support its claim, in every language)."
        ),
        side_effect="write",
        timeout_s=60.0,
        retryable=True,
    )(fact_check_tool(embedder))
