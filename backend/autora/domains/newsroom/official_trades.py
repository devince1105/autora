"""Public officials' transaction reports, transcribed for the stock pages (D-051).

The President files OGE Form 278-T periodic transaction reports: every purchase and sale, its date
and the range its value falls in. OGE publishes them as **scanned** PDFs, whose embedded OCR is
unreadable (the filer's name comes out as "Don.JdJln.tmp"). So:

1. OGE's index of officials' reports (the JSON its own search page reads, newest first) is asked
   for its latest few hundred entries; the reports of the people in ``FIGURES`` not seen before
   are taken, newest first, as many as the run's page budget allows (the rest wait for the next).
2. Each report is split into single pages and a model transcribes each page's table into rows.
   Nothing is guessed: a cell it cannot read comes back empty, and a row is checked here — its
   amount must be one of the form's ranges, its date a date — before it is kept.
3. The report and its rows are stored **pending**, and a person is asked (an approval) to check
   them against the scan. Only an approved report's trades reach a stock page, each with a link
   to its page in the PDF. A report that could not be read whole is not stored: it is tried again.

Members of Congress file the same kind of report under the STOCK Act, with the Clerk of the House
(D-051): its yearly index lists every filing, and a Periodic Transaction Report is a PDF too —
generated rather than scanned, but read the same way, with the form's own prompt. Most of a
member's trades may be their spouse's (the Owner column: ``SP``), and many are options; both are
kept and shown, so that a call option bought by a spouse is never read as the member buying the
stock.

A stock page finds a trade by its ticker: the one a report puts after the name ("BANK OF AMERICA
CORPORATION - BAC"), or — as other reports name stocks only by issuer ("AMAZON.COM INC", "ALPHABET
INC CL A") — the one ``ISSUERS`` gives the name of a stock the site has a page for. A bond of the
same issuer ("APPLE INC SENIOR NOTES DUE 2029") is not the stock, and is not tagged.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import re
import uuid
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol
from xml.etree import ElementTree

import httpx
from pydantic import BaseModel
from pypdf import PdfReader, PdfWriter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autora.db.models import Approval, Schedule
from autora.domains.newsroom.models import OfficialReport, OfficialReportStatus, OfficialTrade
from autora.runtime.actor import Actor
from autora.runtime.scheduler import Handler

log = logging.getLogger(__name__)

OFFICIAL_SCHEDULE = "newsroom.refresh_official_trades"
OFFICIAL_CRON = "20 */6 * * *"
"""Four times a day, twenty minutes before the 13F refresh."""

OGE_INDEX = "https://extapps2.oge.gov/201/Presiden.nsf/API.xsp/v2/rest"
RECENT = 4000
"""How many of OGE's newest entries are read each run (the index takes no filter; it is read
newest first): about two years of every official's filings — every one of the President's
reports this term — in one request."""
CONCURRENT_PAGES = 4
"""Pages transcribed at once: a 37-page report in a few minutes rather than ten."""
PAGES_PER_RUN = 60
"""The most pages transcribed in one run — at about a cent a page, a bound on what a run costs.
A long backlog takes several runs; a report bigger than this is still read whole, alone."""

FIGURES = {"Trump, Donald": "川普"}
"""Whose OGE reports: the index's filer name (its start) → how the site names them."""

HOUSE_INDEX = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip"
HOUSE_REPORT = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{doc}.pdf"
HOUSE_FIGURES = {("Pelosi", "Nancy"): "佩洛西"}
"""Whose House reports: (last name, first name) as the Clerk's index gives them → the site's."""

APPROVAL_KIND = "official_report"
APPROVAL_ACTION = "approve_official_report"

AMOUNT_RANGES = {
    1_001: 15_000,
    15_001: 50_000,
    50_001: 100_000,
    100_001: 250_000,
    250_001: 500_000,
    500_001: 1_000_000,
    1_000_001: 5_000_000,
    5_000_001: 25_000_000,
    25_000_001: 50_000_000,
}
"""OGE Form 278-T's value ranges, lower bound → upper. Above them: "Over $50,000,000"."""
TOP = 50_000_000

_TICKER = re.compile(r"\s-\s([A-Z]{1,5}(?:\.[A-Z])?)\s*$")
_HOUSE_TICKER = re.compile(r"\(([A-Z]{1,5}(?:\.[A-Z])?)\)\s*\[(ST|OP|EF)\]")
"""A House report's asset: "NVIDIA Corporation - Common Stock (NVDA) [ST]" — a stock (ST), an
option on one (OP) or a fund (EF). Other asset types (bonds, LLCs) have no page."""

ISSUERS: dict[str, tuple[str, ...]] = {
    "NVDA": ("NVIDIA CORP",),
    "AAPL": ("APPLE INC",),
    "GOOGL": ("ALPHABET INC",),
    "MSFT": ("MICROSOFT CORP",),
    "AMZN": ("AMAZON.COM INC", "AMAZON COM INC", "AMAZON INC"),  # all three in one report
    "TSM": ("TAIWAN SEMICONDUCTOR MFG", "TAIWAN SEMICONDUCTOR MANUFACTURING"),
    "META": ("META PLATFORMS",),
    "AVGO": ("BROADCOM INC",),
    "TSLA": ("TESLA INC",),
    "MU": ("MICRON TECHNOLOGY",),
    "AMD": ("ADVANCED MICRO DEVICES",),
    "QQQ": ("INVESCO QQQ",),
    "VOO": ("VANGUARD S&P 500 ETF", "VANGUARD INDEX FDS S&P 500"),
}
"""The stocks with a page on the site, by the name a report gives their issuer (its start)."""
_DEBT = re.compile(r"\bDUE\b|%|\bNOTES?\b|\bBONDS?\b|\bDEB\b|\bB/E\b|\bREG S\b")


def ticker_of(description: str) -> str | None:
    """The ticker a report gives after the name, or the one of a stock the site follows whose
    issuer the description names — unless it describes a debt of that issuer."""
    explicit = _TICKER.search(description) or _HOUSE_TICKER.search(description)
    if explicit:
        return explicit.group(1)
    upper = description.upper()
    if _DEBT.search(upper):
        return None
    for ticker, names in ISSUERS.items():
        if any(upper.startswith(name) for name in names):
            return ticker
    return None


_NUMBER = re.compile(r"\d[\d,]*")
_KINDS = {
    "purchase": "purchase",
    "sale": "sale",
    "sale (partial)": "partial sale",
    "partial sale": "partial sale",
    "exchange": "exchange",
    # the House's codes
    "p": "purchase",
    "s": "sale",
    "s (partial)": "partial sale",
    "e": "exchange",
}
OWNERS = {"SP", "JT", "DC"}
"""A House report's Owner column: spouse, joint, dependent child; empty is the member."""


class TranscribeError(Exception):
    """A page could not be transcribed; the report is tried again next time."""


# --- OGE's index -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Listed:
    filer: str
    person: str
    form: str
    url: str
    received_on: date
    kind: str = "oge"
    """Which form it is, so it is read with the right prompt: ``oge`` or ``house``."""


def parse_index(data: dict[str, Any]) -> list[Listed]:
    """The transaction reports in an index page of the people in ``FIGURES``, as listed. An
    entry without a PDF (OGE's "Request this Document") is not one to read."""
    out = []
    for row in data.get("data", []):
        name = str(row.get("name", ""))
        person = next((p for prefix, p in FIGURES.items() if name.startswith(prefix)), None)
        if person is None:
            continue
        cell = str(row.get("type", ""))
        link = re.search(r"href='([^']+\.pdf)'", cell, re.I)
        form = re.sub(r"<[^>]+>", "", cell).strip()
        if not link or not form.startswith("278 Transaction"):
            continue
        out.append(
            Listed(
                filer=name,
                person=person,
                form=form,
                url=link.group(1),
                received_on=date.fromisoformat(str(row["docDate"])[:10]),
            )
        )
    return out


def parse_house_index(archive: bytes, year: int) -> list[Listed]:
    """The Periodic Transaction Reports (``FilingType`` P) of the people in ``HOUSE_FIGURES`` in
    the Clerk's yearly index (a zip holding ``<year>FD.xml``)."""
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        name = next(n for n in zipped.namelist() if n.lower().endswith(".xml"))
        data = zipped.read(name)
    # untrusted input, as feeds are (feeds.py): a DTD or an entity is refused, not expanded
    if re.search(rb"<!DOCTYPE|<!ENTITY", data, re.I):
        raise ValueError("the House's index declares a DTD or entities")
    root = ElementTree.fromstring(data)
    out = []
    for member in root:
        fields = {child.tag: (child.text or "").strip() for child in member}
        person = HOUSE_FIGURES.get((fields.get("Last", ""), fields.get("First", "")))
        if person is None or fields.get("FilingType") != "P" or not fields.get("DocID"):
            continue
        filed = parse_date(fields.get("FilingDate", ""))
        if filed is None:
            continue
        out.append(
            Listed(
                filer=f"{fields.get('Prefix', '')} {fields['First']} {fields['Last']}".strip(),
                person=person,
                form="Periodic Transaction Report",
                url=HOUSE_REPORT.format(year=year, doc=fields["DocID"]),
                received_on=filed,
                kind="house",
            )
        )
    return out


async def list_reports(fetch: Fetch, today: date) -> list[Listed]:
    """Every report to consider, newest first: the President's from OGE, and the members' from
    the House for this year and last (a report filed in January belongs to last year's list)."""
    listed = parse_index(await fetch.json(OGE_INDEX, {"draw": 1, "start": 0, "length": RECENT}))
    for year in (today.year, today.year - 1):
        try:
            listed += parse_house_index(await fetch.file(HOUSE_INDEX.format(year=year)), year)
        except (
            httpx.HTTPError,
            zipfile.BadZipFile,
            ElementTree.ParseError,
            StopIteration,
            ValueError,
        ) as error:
            log.warning("official trades: the House's %s index not read (%s)", year, error)
    return sorted(listed, key=lambda item: item.received_on, reverse=True)


# --- a row, checked ----------------------------------------------------------------------------


def parse_amount(text: str) -> tuple[int | None, int | None, bool]:
    """``$15,001 - $50,000`` → (15001, 50000, True). ``Over $50,000,000`` → (50000001, None,
    True). Anything that is not one of the form's ranges → (None, None, False)."""
    numbers = [int(n.replace(",", "")) for n in _NUMBER.findall(text)]
    if text.strip().lower().startswith("over") and numbers == [TOP]:
        return TOP + 1, None, True
    if len(numbers) == 2 and AMOUNT_RANGES.get(numbers[0]) == numbers[1]:
        return numbers[0], numbers[1], True
    return None, None, False


def parse_date(text: str) -> date | None:
    try:
        return datetime.strptime(text.strip(), "%m/%d/%Y").date()
    except ValueError:
        return None


@dataclass(frozen=True)
class Row:
    number: int
    description: str
    ticker: str | None
    kind: str
    traded_on: date | None
    late: bool | None
    amount_min: int | None
    amount_max: int | None
    amount_text: str
    owner: str | None = None
    """``SP``, ``JT`` or ``DC`` on a House report; None: the filer themself."""
    note: str | None = None
    """A House report's description under the row: "Purchased 100 call options…"."""

    @property
    def readable(self) -> bool:
        """Everything a stock page shows was read: a date and one of the form's ranges."""
        return self.traded_on is not None and (self.amount_min is not None)


def parse_row(raw: dict[str, Any]) -> Row | None:
    """A transcribed row, checked. None when it is not a transaction at all (no number, no
    description)."""
    try:
        number = int(raw.get("number"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    description = " ".join(str(raw.get("description") or "").split())
    if not description:
        return None
    amount_text = " ".join(str(raw.get("amount") or "").split())
    low, high, _ = parse_amount(amount_text)
    late = str(raw.get("late") or "").strip().lower()
    kind = " ".join(str(raw.get("type") or "").lower().split())
    owner = str(raw.get("owner") or "").strip().upper()
    note = " ".join(str(raw.get("details") or "").split())
    return Row(
        number=number,
        description=description,
        ticker=ticker_of(description),
        kind=_KINDS.get(kind, kind or "unknown"),
        traded_on=parse_date(str(raw.get("date") or "")),
        late=True if late == "yes" else False if late == "no" else None,
        amount_min=low,
        amount_max=high,
        amount_text=amount_text,
        owner=owner if owner in OWNERS else None,
        note=note or None,
    )


# --- transcribing ------------------------------------------------------------------------------


def split_pages(pdf: bytes) -> list[bytes]:
    """The report as single-page PDFs, the pages' images copied as they are (never decoded)."""
    reader = PdfReader(io.BytesIO(pdf))
    pages = []
    for page in reader.pages:
        writer = PdfWriter()
        writer.add_page(page)
        out = io.BytesIO()
        writer.write(out)
        pages.append(out.getvalue())
    return pages


class Transcriber(Protocol):
    model: str

    async def transcribe(self, page_pdf: bytes, form: str = "oge") -> list[dict[str, Any]]: ...


_NEVER_GUESS = (
    " If a cell cannot be read, give an empty string; never guess. A page with no transactions "
    "table gives no rows."
)
PROMPTS = {
    "oge": (
        "This is one page of a U.S. OGE Form 278-T periodic transaction report. Transcribe every "
        "row of its Transactions table exactly as printed: number, description (verbatim), type, "
        "date (M/D/YYYY as printed), late (the 'Notification Received Over 30 Days Ago' column, "
        "yes or no) and amount (the Amount column, verbatim, e.g. '$1,001 - $15,000'); owner and "
        "details are empty strings (this form has neither)." + _NEVER_GUESS
    ),
    "house": (
        "This is one page of a U.S. House of Representatives Periodic Transaction Report. "
        "Transcribe every transaction exactly as printed: number (its position among this page's "
        "transactions, 1 for the first — the report prints none), owner (the Owner column: SP, "
        "JT, DC, or an empty string when blank), description (the Asset cell verbatim, with the "
        "ticker in parentheses and the asset type in brackets, e.g. 'NVIDIA Corporation - Common "
        "Stock (NVDA) [ST]'), type (the Transaction Type code as printed: P, S, S (partial) or "
        "E), date (the transaction Date, MM/DD/YYYY), late (an empty string: this form has no "
        "such column), amount (verbatim, e.g. '$1,000,001 - $5,000,000') and details (the text "
        "after 'Description:' under the transaction, verbatim, or an empty string)." + _NEVER_GUESS
    ),
}
PROMPT = PROMPTS["oge"]
_FIELDS = ("number", "owner", "description", "type", "date", "late", "amount", "details")
SCHEMA = {
    "type": "object",
    "properties": {
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    f: {"type": "integer"} if f == "number" else {"type": "string"} for f in _FIELDS
                },
                "required": list(_FIELDS),
                "propertyOrdering": list(_FIELDS),
            },
        }
    },
    "required": ["rows"],
}


@dataclass
class GeminiTranscriber:
    """Gemini's own API (not its OpenAI-compatible one: it reads a PDF as it is). No thinking —
    this is copying, and it was as accurate without, at a third of the time."""

    api_key: str
    model: str
    timeout: float = 180.0
    base_url: str = "https://generativelanguage.googleapis.com/v1beta"

    async def transcribe(self, page_pdf: bytes, form: str = "oge") -> list[dict[str, Any]]:
        body = {
            "contents": [
                {
                    "parts": [
                        {
                            "inline_data": {
                                "mime_type": "application/pdf",
                                "data": base64.b64encode(page_pdf).decode(),
                            }
                        },
                        {"text": PROMPTS[form]},
                    ]
                }
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": SCHEMA,
                "temperature": 0,
                "thinkingConfig": {"thinkingBudget": 0},
            },
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/models/{self.model}:generateContent",
                    json=body,
                    headers={"x-goog-api-key": self.api_key},
                )
            response.raise_for_status()
            text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
            rows = json.loads(text)["rows"]
        except httpx.HTTPStatusError as error:
            raise TranscribeError(f"HTTP {error.response.status_code} from the model") from error
        except (httpx.HTTPError, KeyError, IndexError, ValueError, TypeError) as error:
            raise TranscribeError(f"{type(error).__name__} transcribing a page") from error
        return rows if isinstance(rows, list) else []


# --- a run -------------------------------------------------------------------------------------


class Fetch(Protocol):
    async def json(self, url: str, params: dict[str, Any]) -> dict[str, Any]: ...

    async def file(self, url: str) -> bytes: ...


class Approvals(Protocol):
    async def request(self, session: AsyncSession, **kwargs: Any) -> Approval: ...


async def read_report(
    listed: Listed, pdf: bytes, transcriber: Transcriber
) -> tuple[int, list[tuple[int, Row]]]:
    """Every page's rows, with the page each is on. Raises if any page cannot be read."""
    pages = split_pages(pdf)
    limit = asyncio.Semaphore(CONCURRENT_PAGES)

    async def read(page: bytes) -> list[dict[str, Any]]:
        async with limit:
            return await transcriber.transcribe(page, listed.kind)

    # all at once, a few at a time; one page failing fails the report (it is read again later)
    transcribed = await asyncio.gather(*(read(page) for page in pages))
    rows: list[tuple[int, Row]] = []
    for index, raws in enumerate(transcribed, start=1):
        for raw in raws:
            row = parse_row(raw)
            if row is not None:
                rows.append((index, row))
    return len(pages), rows


def is_option(description: str) -> bool:
    """An option on the stock, not the stock: a House report's [OP], or an OGE line naming a
    call or a put."""
    return "[OP]" in description or bool(re.search(r"\b(CALL|PUT)S?\b", description.upper()))


def approval_payload(report: OfficialReport, rows: list[tuple[int, Row]]) -> dict[str, Any]:
    """What the person checking it needs: where the scan is, how much was read, and the rows
    that will reach a stock page (the ones with a ticker), each with its page."""
    stocks = [(page, r) for page, r in rows if r.ticker]
    return {
        "report": report.url,
        "person": report.person,
        "received_on": report.received_on.isoformat(),
        "pages": report.pages,
        "rows": len(rows),
        "unreadable": sum(not r.readable for _, r in rows),
        "stock_rows": len(stocks),
        "stocks": [
            f"p.{page} #{r.number} {r.ticker}"
            + (f" [{r.owner}]" if r.owner else "")
            + (" option" if is_option(r.description) else "")
            + f" {r.kind} {r.traded_on or '?'} {r.amount_text or '?'}"
            for page, r in stocks[:60]
        ],
    }


async def refresh_official_trades(
    session: AsyncSession,
    company_id: uuid.UUID,
    fetch: Fetch,
    transcriber: Transcriber,
    approvals: Approvals,
    *,
    pages_per_run: int = PAGES_PER_RUN,
    today: date | None = None,
) -> int:
    """Read the new reports, newest first, within the run's page budget. How many were stored."""
    reports = await list_reports(fetch, today or date.today())
    known = set(
        (
            await session.scalars(
                select(OfficialReport.url).where(OfficialReport.company_id == company_id)
            )
        ).all()
    )
    stored = 0
    budget = pages_per_run
    for listed in reports:
        if listed.url in known:
            continue
        try:
            pdf = await fetch.file(listed.url)
            if stored and len(split_pages(pdf)) > budget:
                break  # the next run starts with it
            pages, rows = await read_report(listed, pdf, transcriber)
        except TranscribeError as error:
            # the model, not the report: no other report would fare better this run (a 402 —
            # credits spent — once went on to download and fail on every report in the list)
            log.warning("official trades: stopped at %s (%s); next time", listed.url, error)
            break
        except (httpx.HTTPError, ValueError) as error:
            log.warning("official trades: %s not read (%s); next time", listed.url, error)
            continue
        report = OfficialReport(
            company_id=company_id,
            person=listed.person,
            filer=listed.filer,
            form=listed.form,
            url=listed.url,
            received_on=listed.received_on,
            pages=pages,
            model=transcriber.model,
        )
        session.add(report)
        await session.flush()
        for page, row in rows:
            session.add(
                OfficialTrade(
                    company_id=company_id,
                    report_id=report.id,
                    page=page,
                    number=row.number,
                    description=row.description,
                    ticker=row.ticker,
                    kind=row.kind,
                    traded_on=row.traded_on,
                    late=row.late,
                    amount_min=row.amount_min,
                    amount_max=row.amount_max,
                    amount_text=row.amount_text,
                    owner=row.owner,
                    note=row.note,
                )
            )
        payload = approval_payload(report, rows)
        approval = await approvals.request(
            session,
            company_id=company_id,
            kind=APPROVAL_KIND,
            ref_type="official_report",
            ref_id=report.id,
            action=APPROVAL_ACTION,
            summary=_summary(listed.person, listed.received_on, payload),
            payload=payload,
            requested_by=Actor.system("newsroom"),
            expires_after=None,
        )
        report.approval_id = approval.id
        await session.flush()
        known.add(listed.url)
        stored += 1
        budget -= pages
        if budget <= 0:
            break
    return stored


def _summary(person: str, received_on: date, payload: dict[str, Any]) -> str:
    return (
        f"核對{person} {received_on} 交易申報的轉錄：{payload['rows']} 筆，"
        f"其中 {payload['stock_rows']} 筆有股票代號、{payload['unreadable']} 筆讀不清"
    )


def _row(trade: OfficialTrade) -> Row:
    return Row(
        number=trade.number,
        description=trade.description,
        ticker=trade.ticker,
        kind=trade.kind,
        traded_on=trade.traded_on,
        late=trade.late,
        amount_min=int(trade.amount_min) if trade.amount_min is not None else None,
        amount_max=int(trade.amount_max) if trade.amount_max is not None else None,
        amount_text=trade.amount_text,
        owner=trade.owner,
        note=trade.note,
    )


async def retag(session: AsyncSession, company_id: uuid.UUID) -> int:
    """Tickers found again for the rows already stored — after ``ISSUERS`` has learnt a name —
    without reading any report again; and a report still waiting for a person gets its card
    brought up to date, so what they check is what will be shown. How many rows changed."""
    changed = 0
    trades = (
        await session.scalars(
            select(OfficialTrade)
            .where(OfficialTrade.company_id == company_id)
            .order_by(OfficialTrade.page, OfficialTrade.number)
        )
    ).all()
    for trade in trades:
        ticker = ticker_of(trade.description)
        if ticker != trade.ticker:
            trade.ticker = ticker
            changed += 1
    pending = (
        await session.scalars(
            select(OfficialReport).where(
                OfficialReport.company_id == company_id,
                OfficialReport.status == OfficialReportStatus.PENDING.value,
            )
        )
    ).all()
    for report in pending:
        approval = await session.get(Approval, report.approval_id) if report.approval_id else None
        if approval is None or approval.state != "PENDING":
            continue
        rows = [(t.page, _row(t)) for t in trades if t.report_id == report.id]
        approval.payload = approval_payload(report, rows)
        approval.summary = _summary(report.person, report.received_on, approval.payload)
    await session.flush()
    return changed


async def on_report_decided(
    session: AsyncSession, approval: Approval, outcome: str, actor: Actor, reason: str | None
) -> None:
    """A person has checked a transcription: its trades go on the stock pages, or never do."""
    report = await session.get(OfficialReport, approval.ref_id)
    if report is None:
        return
    report.status = (
        OfficialReportStatus.APPROVED.value
        if outcome == "approve"
        else OfficialReportStatus.REJECTED.value
    )


# --- what a stock page reads -------------------------------------------------------------------


class PublicTrade(BaseModel):
    person: str
    kind: str
    """``purchase``, ``sale``, ``partial sale``, ``exchange``."""
    traded_on: date | None
    amount_min: int | None
    amount_max: int | None
    amount_text: str
    late: bool | None
    received_on: date
    """When OGE received the report."""
    report_url: str
    """The scan, opened at the trade's page."""
    owner: str | None = None
    """``SP`` (spouse), ``JT`` (joint), ``DC`` (dependent child); None: the person themself."""
    option: bool = False
    """An option on the stock, not the stock."""
    note: str | None = None
    """What the report says of it: "Purchased 100 call options with a strike price of $100…"."""


async def trades_for(
    session: AsyncSession,
    tickers: tuple[str, ...],
    *,
    company_id: uuid.UUID | None = None,
    limit: int = 30,
) -> list[PublicTrade]:
    """Approved reports' trades in these tickers, newest trade first."""
    if not tickers:
        return []
    query = (
        select(OfficialTrade, OfficialReport)
        .join(OfficialReport, OfficialReport.id == OfficialTrade.report_id)
        .where(
            OfficialTrade.ticker.in_(tickers),
            OfficialReport.status == OfficialReportStatus.APPROVED.value,
        )
        .order_by(OfficialTrade.traded_on.desc().nulls_last(), OfficialTrade.number)
        .limit(limit)
    )
    if company_id is not None:
        query = query.where(OfficialTrade.company_id == company_id)
    return [
        PublicTrade(
            person=report.person,
            kind=trade.kind,
            traded_on=trade.traded_on,
            amount_min=int(trade.amount_min) if trade.amount_min is not None else None,
            amount_max=int(trade.amount_max) if trade.amount_max is not None else None,
            amount_text=trade.amount_text,
            late=trade.late,
            received_on=report.received_on,
            report_url=f"{report.url}#page={trade.page}",
            owner=trade.owner,
            option=is_option(trade.description),
            note=trade.note,
        )
        for trade, report in (await session.execute(query)).all()
    ]


# --- the schedule ------------------------------------------------------------------------------


class HttpFetch:
    """OGE, asked as a reader would, naming who asks (as SEC wants too)."""

    def __init__(self, contact: str | None, timeout: float = 120.0) -> None:
        agent = f"Autora Newsroom {contact}" if contact else "Autora Newsroom"
        self.headers = {"User-Agent": agent}
        self.timeout = timeout

    async def json(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout, headers=self.headers) as client:
            return (await client.get(url, params=params)).raise_for_status().json()

    async def file(self, url: str) -> bytes:
        async with httpx.AsyncClient(
            timeout=self.timeout, headers=self.headers, follow_redirects=True
        ) as client:
            return (await client.get(url)).raise_for_status().content


class OfficialTradesKeeper:
    def __init__(
        self, fetch: Fetch, transcriber: Transcriber | None, approvals: Approvals | None
    ) -> None:
        self.fetch = fetch
        self.transcriber = transcriber
        self.approvals = approvals

    def schedule_handler(self) -> Handler:
        """The ``newsroom.refresh_official_trades`` handler. Without a model or a way to ask
        a person, it does nothing: no report is shown unchecked."""

        async def handler(
            session: AsyncSession, schedule: Schedule, scheduled_for: datetime
        ) -> None:
            if self.transcriber is None or self.approvals is None:
                return
            await refresh_official_trades(
                session, schedule.company_id, self.fetch, self.transcriber, self.approvals
            )

        return handler
