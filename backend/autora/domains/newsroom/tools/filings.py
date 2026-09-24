"""``compare_13f`` (D-037): a 13F filing and the one before it, compared, captured as evidence.

The researcher is given a filing's address (a story from a 13F source is one). This tool asks SEC
for the filer's list of filings — and for its predecessors', when the story's source names them
in ``predecessor_ciks`` — finds the previous quarter's original 13F-HR (or several, added up),
reads the holdings and stores the comparison ``thirteenf.render`` writes as one evidence record,
under the filing's index URL (what a reader clicks, and the URL the story's item has).

The analyst then quotes its lines like any page's, and fact-check checks the numbers against them.
What the tool will not do is compare what it cannot compare correctly: an amendment, a notice, a
filing with no earlier quarter. It says so, and the researcher can still ``fetch_url`` the index.

A few requests to SEC (the filers' lists and the filings), through the
same fetcher as every page — so in live mode with ``FETCH_CONTACT_EMAIL``'s User-Agent, which
SEC requires, and in fixture mode from the fixture files.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import BaseModel, Field
from sqlalchemy import select

from autora.domains.newsroom import thirteenf
from autora.domains.newsroom.models import Source, SourceItem
from autora.domains.newsroom.sources import canonical_url
from autora.domains.newsroom.tools.evidence import EXCERPT, READ_NOTE, TEXT_LIMIT, capture_text
from autora.infra.blobstore import BlobStore
from autora.infra.http import PageFetcher
from autora.runtime.events.catalog import ProducedRef
from autora.runtime.models.embeddings import Embedder
from autora.runtime.tools import ToolContext, ToolFn, ToolRegistry, ToolResult

FilingError = thirteenf.FilingError

PREDECESSORS = "predecessor_ciks"
"""``config.predecessor_ciks`` of a 13F source: filers whose holdings this filer reports from now
on, so that their filings for the previous quarter are part of it (Pershing Square Capital
Management → Pershing Square Inc., 2026)."""


class Compare13FArgs(BaseModel):
    filing_url: str = Field(
        min_length=20,
        max_length=500,
        description="The 13F-HR filing's address on sec.gov (its index page, as the lead gives).",
    )


async def _predecessors(ctx: ToolContext, urls: list[str]) -> list[str]:
    config = await ctx.session.scalar(
        select(Source.config)
        .join(SourceItem, SourceItem.source_id == Source.id)
        .where(SourceItem.company_id == ctx.company_id, SourceItem.url.in_(urls))
        .limit(1)
    )
    ciks = (config or {}).get(PREDECESSORS) or []
    return [str(c).strip() for c in ciks if str(c).strip().isdigit()]


def compare_13f_tool(
    fetcher: PageFetcher,
    blobs: BlobStore,
    embedder: Embedder,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> ToolFn:
    async def fetch(url: str) -> bytes:
        return (await fetcher.fetch(url)).body

    async def compare_13f(args: Compare13FArgs, ctx: ToolContext) -> ToolResult:
        ref = thirteenf.filing_ref(args.filing_url)
        filer = thirteenf.parse_submissions(await fetch(thirteenf.submissions_url(ref.cik)))
        current = next((f for f in filer.filings if f.accession == ref.accession), None)
        if current is None:
            raise FilingError(
                f"{ref.accession} is not in SEC's list of {filer.name}'s recent filings"
            )
        if current.form != "13F-HR":
            raise FilingError(
                f"this filing is a {current.form}, not an original 13F-HR: amendments and notices "
                "are not compared. Capture its index page with fetch_url if it is the story."
            )

        filers = {filer.cik: filer}
        for cik in await _predecessors(
            ctx, [canonical_url(args.filing_url), canonical_url(ref.index_url)]
        ):
            other = thirteenf.parse_submissions(await fetch(thirteenf.submissions_url(cik)))
            filers.setdefault(other.cik, other)
        before = thirteenf.previous_quarter(current, list(filers.values()))
        if not before:
            raise FilingError(
                f"no earlier 13F-HR from {filer.name} to compare with. Capture the filing's index "
                "page with fetch_url instead."
            )

        body_now = await fetch(current.ref.text_url)
        bodies_before = [await fetch(listed.ref.text_url) for listed in before]
        holdings_now = thirteenf.parse_filing(body_now)
        holdings_before = thirteenf.combine([thirteenf.parse_filing(b) for b in bodies_before])
        if not holdings_now.positions:
            raise FilingError(
                f"{current.accession} lists no holdings ({holdings_now.report_type or 'no table'})"
            )
        text = thirteenf.render(
            filer=filer.name,
            cik=filer.cik,
            now=current,
            before=[(filers[listed.cik].name, listed) for listed in before],
            holdings_now=holdings_now,
            holdings_before=holdings_before,
        )
        period_before = before[0].period
        title = f"{filer.name} 13F: {current.period} vs {period_before}"
        evidence, reused = await capture_text(
            ctx,
            url=canonical_url(ref.index_url),
            final_url=current.ref.text_url,
            title=title,
            content_type="text/plain; charset=utf-8",
            language="en",
            text=text[:TEXT_LIMIT],
            truncated=len(text) > TEXT_LIMIT,
            # both filings as SEC sent them, kept privately with the evidence they became
            snapshot=b"\n\n<!-- previous quarter -->\n\n".join([body_now, *bodies_before]),
            blobs=blobs,
            embedder=embedder,
            now=clock(),
        )
        diff = thirteenf.compare(holdings_now, holdings_before)
        counts = {
            "new": len(diff.new),
            "sold_out": len(diff.sold_out),
            "increased": len(diff.increased),
            "decreased": len(diff.decreased),
            "unchanged": len(diff.unchanged),
        }
        return ToolResult(
            output={
                "evidence_id": str(evidence.id),
                "url": evidence.url,
                "title": evidence.title,
                "filer": filer.name,
                "period": str(current.period),
                "previous_period": str(period_before),
                "previous_filers": [filers[listed.cik].name for listed in before],
                "counts": counts,
                "reused": reused,
                "excerpt": evidence.extracted_text[:EXCERPT],
                "note": READ_NOTE
                + " Every position is one line; read the whole comparison with read_evidence.",
            },
            summary=f"{'reused' if reused else 'captured'} 13F comparison {evidence.id} "
            f"({filer.name}, {current.period} vs {period_before})",
            produced=[ProducedRef(type="evidence", id=evidence.id)],
        )

    return compare_13f


def register(
    registry: ToolRegistry, fetcher: PageFetcher, blobs: BlobStore, embedder: Embedder
) -> None:
    registry.tool(
        "compare_13f",
        description=(
            "For a story about an SEC 13F-HR filing: compare the filing with the filer's previous "
            "quarter (new positions, sold out, increased, decreased, unchanged; shares and "
            "values) and capture the comparison as evidence. Give the filing's sec.gov address."
        ),
        side_effect="write",
        timeout_s=60.0,
        retryable=True,
    )(compare_13f_tool(fetcher, blobs, embedder))
