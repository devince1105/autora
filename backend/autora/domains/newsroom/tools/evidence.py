"""``fetch_url`` and ``read_evidence`` (T-502): turning a page into Evidence, and reading it.

``fetch_url`` is the only way a page becomes evidence (D-003: search results are candidates).
It fetches the page (live or fixture, the composition root decides), keeps the raw snapshot in
the blob store (private), extracts the readable text (``extract.py``) and stores both as an
``evidence`` row, all in the tool's transaction together with TOOL_COMPLETED and, for a new
snapshot, EVIDENCE_CAPTURED. The same URL with the same text on the same day is reused, not
stored again; either way the call ``produced`` the evidence, so an agent's "Sources" count
(AC-5) counts what it actually used.

What cannot become evidence fails the call without a retry: non-text content (PDFs, images),
pages with no readable text (often ones that need JavaScript), private addresses.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from autora.domains.newsroom.events import EvidenceCaptured
from autora.domains.newsroom.extract import extract
from autora.domains.newsroom.models import Evidence, SourceItem
from autora.domains.newsroom.sources import canonical_url
from autora.infra.blobstore import BlobStore
from autora.infra.http import PageFetcher
from autora.infra.ids import uuid7
from autora.runtime.events.catalog import ProducedRef
from autora.runtime.events.outbox import emit
from autora.runtime.events.schema import new_event
from autora.runtime.tools import ToolContext, ToolFn, ToolRegistry, ToolResult

TEXT_LIMIT = 200_000
"""Characters of extracted text kept per page (longer pages are cut and marked truncated)."""
MIN_TEXT = 40
EXCERPT = 1500
_TEXTUAL = ("text/html", "application/xhtml+xml", "text/plain")
READ_NOTE = (
    "Quote evidence exactly as it appears in its text: quotes are checked against it. "
    "Use read_evidence to read further."
)


class EvidenceError(Exception):
    retryable = False


class FetchUrlArgs(BaseModel):
    url: str = Field(
        min_length=8, max_length=2000, description="The page to capture (http or https)."
    )
    render: bool = Field(
        default=False,
        description="Run the page's JavaScript before reading it (not available yet: ignored).",
    )


class ReadEvidenceArgs(BaseModel):
    evidence_id: uuid.UUID
    offset: int = Field(default=0, ge=0, description="Character offset to start reading at.")
    limit: int = Field(default=4000, ge=200, le=8000, description="How many characters to read.")


def _textual(content_type: str) -> bool:
    kind = content_type.split(";")[0].strip().lower()
    return kind in _TEXTUAL or kind == ""


def fetch_url_tool(
    fetcher: PageFetcher,
    blobs: BlobStore,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> ToolFn:
    async def fetch_url(args: FetchUrlArgs, ctx: ToolContext) -> ToolResult:
        url = canonical_url(args.url)
        page = await fetcher.fetch(args.url)
        if not _textual(page.content_type):
            raise EvidenceError(
                f"{page.content_type or 'unknown'} content cannot be evidence yet (text pages only)"
            )
        extracted = extract(page.body, page.content_type)
        text = extracted.text
        if len(text) < MIN_TEXT:
            raise EvidenceError("the page has no readable text (it may need JavaScript)")
        truncated = len(text) > TEXT_LIMIT
        text = text[:TEXT_LIMIT]
        text_hash = hashlib.sha256(text.encode()).hexdigest()
        now = clock()
        on = now.date()

        existing = await ctx.session.scalar(
            select(Evidence).where(
                Evidence.company_id == ctx.company_id,
                Evidence.url == url,
                Evidence.text_hash == text_hash,
                Evidence.retrieved_on == on,
            )
        )
        reused = existing is not None
        if existing is None:
            listed = await ctx.session.scalar(
                select(SourceItem)
                .where(SourceItem.company_id == ctx.company_id, SourceItem.url == url)
                .order_by(SourceItem.created_at)
                .limit(1)
            )
            evidence_id = uuid7()
            blob_key = f"evidence/{ctx.company_id}/{on:%Y/%m/%d}/{evidence_id}.snapshot"
            await blobs.put(blob_key, page.body)
            inserted = await ctx.session.scalar(
                insert(Evidence)
                .values(
                    id=evidence_id,
                    company_id=ctx.company_id,
                    url=url,
                    final_url=canonical_url(page.url),
                    title=(extracted.title or "")[:500] or None,
                    content_type=page.content_type[:200],
                    language=extracted.language,
                    retrieved_at=now,
                    retrieved_on=on,
                    blob_key=blob_key,
                    extracted_text=text,
                    text_hash=text_hash,
                    truncated=truncated,
                    source_id=listed.source_id if listed else None,
                    source_item_id=listed.id if listed else None,
                    task_id=ctx.task_id,
                    run_id=ctx.run_id,
                )
                .on_conflict_do_nothing()
                .returning(Evidence.id)
            )
            if inserted is None:  # captured by a concurrent call a moment ago
                reused = True
            else:
                await emit(
                    ctx.session,
                    new_event(
                        EvidenceCaptured(
                            evidence_id=evidence_id,
                            url=url,
                            title=(extracted.title or "")[:300] or None,
                            source_id=listed.source_id if listed else None,
                        ),
                        company_id=ctx.company_id,
                        actor=ctx.actor,
                        aggregate_type="evidence",
                        aggregate_id=evidence_id,
                        agent_id=ctx.agent_id,
                        run_id=ctx.run_id,
                        task_id=ctx.task_id,
                        workflow_run_id=ctx.workflow_run_id,
                        correlation_id=ctx.workflow_run_id,
                    ),
                )
        evidence = await ctx.session.scalar(
            select(Evidence).where(
                Evidence.company_id == ctx.company_id,
                Evidence.url == url,
                Evidence.text_hash == text_hash,
                Evidence.retrieved_on == on,
            )
        )
        assert evidence is not None
        output = {
            "evidence_id": str(evidence.id),
            "url": evidence.url,
            "title": evidence.title,
            "chars": len(evidence.extracted_text),
            "truncated": evidence.truncated,
            "reused": reused,
            "excerpt": evidence.extracted_text[:EXCERPT],
            "note": READ_NOTE,
        }
        if args.render:
            output["render"] = "not available: the page was read without running JavaScript"
        return ToolResult(
            output=output,
            summary=("reused" if reused else "captured")
            + f" evidence {evidence.id} ({len(evidence.extracted_text)} chars)",
            produced=[ProducedRef(type="evidence", id=evidence.id)],
        )

    return fetch_url


async def read_evidence(args: ReadEvidenceArgs, ctx: ToolContext) -> ToolResult:
    evidence = await ctx.session.get(Evidence, args.evidence_id)
    if evidence is None or evidence.company_id != ctx.company_id:
        raise EvidenceError(f"no evidence {args.evidence_id}")
    total = len(evidence.extracted_text)
    end = min(total, args.offset + args.limit)
    return ToolResult(
        output={
            "evidence_id": str(evidence.id),
            "url": evidence.url,
            "title": evidence.title,
            "retrieved_at": evidence.retrieved_at.isoformat(),
            "offset": args.offset,
            "text": evidence.extracted_text[args.offset : end],
            "next_offset": end if end < total else None,
            "total_chars": total,
            "note": READ_NOTE,
        },
        summary=f"read {end - min(args.offset, total)} of {total} chars of evidence {evidence.id}",
    )


def register(registry: ToolRegistry, fetcher: PageFetcher, blobs: BlobStore) -> None:
    registry.tool(
        "fetch_url",
        description=(
            "Capture a web page as evidence: fetches it, stores a snapshot and its readable "
            "text, and returns an evidence_id with an excerpt. Only captured pages can support "
            "claims."
        ),
        side_effect="write",
        timeout_s=30.0,
        retryable=True,
    )(fetch_url_tool(fetcher, blobs))
    registry.tool(
        "read_evidence",
        description="Read the text of a captured evidence page, a slice at a time.",
        side_effect="read",
        timeout_s=10.0,
    )(read_evidence)
