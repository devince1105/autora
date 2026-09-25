"""The tracked investors' 13F positions, kept for the stock pages (D-049).

The newsroom compares two quarters of a filing when it writes about one (``compare_13f``); the
result is evidence, text. A stock page asks the other way round — who holds NVDA, and what did
they do with it — so the same comparison is also kept here as rows, one per position, for every
13F source the company follows, whether or not an article was written about it.

``refresh_holdings`` runs on a schedule. For each 13F source it asks SEC for the filer's list of
filings (one request) and stops there unless the latest original 13F-HR is one it has not stored;
then it reads that filing and the previous quarter's (its predecessors' too, as ``compare_13f``
does) and replaces the source's rows. The arithmetic is ``thirteenf``'s, already tested.

A stock is found in a filing by CUSIP (13F filings name issuers, not tickers): ``STOCKS`` maps
the site's symbols to theirs. A Taiwan stock's page shows the holders of its US listing — TSMC's
2330 page, those of TSM.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime

from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from autora.db.models import Schedule
from autora.domains.newsroom import thirteenf
from autora.domains.newsroom.models import InvestorPosition, PositionChange, Source
from autora.domains.newsroom.sources import PRIMARY, SECTION, TITLE_PREFIX
from autora.domains.newsroom.tools.filings import PREDECESSORS
from autora.infra.http import FetchError, PageFetcher
from autora.runtime.scheduler import Handler

log = logging.getLogger(__name__)

HOLDINGS_SCHEDULE = "newsroom.refresh_holdings"
HOLDINGS_CRON = "40 */6 * * *"
"""Four times a day: one request per investor when nothing is new, and 13Fs arrive rarely."""

_CIK = re.compile(r"[?&]CIK=0*(\d+)", re.I)


@dataclass(frozen=True)
class Stock:
    symbol: str
    """As the site's URL and strip name it: ``NVDA``, ``2330``."""
    market: str
    """``us`` or ``tw``: the strip's key is ``<market>:<symbol>``."""
    zh: str
    en: str
    cusips: tuple[str, ...] = ()
    """Its US listings' CUSIPs, as 13F filings name them (none: no 13F holders to show)."""
    aliases: tuple[str, ...] = ()
    """Other ways an article may name it."""

    @property
    def key(self) -> str:
        return f"{self.market}:{self.symbol}"

    @property
    def terms(self) -> tuple[str, ...]:
        """What an article that mentions it would say."""
        return (self.symbol, self.zh, self.en, *self.aliases)


_TSM = ("874039100",)

STOCKS: dict[str, Stock] = {
    s.symbol: s
    for s in (
        Stock("NVDA", "us", "輝達", "Nvidia", ("67066G104",), ("英偉達", "NVIDIA")),
        Stock("AAPL", "us", "蘋果", "Apple", ("037833100",)),
        Stock(
            "GOOGL", "us", "Alphabet", "Alphabet", ("02079K305", "02079K107"), ("谷歌", "Google")
        ),
        Stock("MSFT", "us", "微軟", "Microsoft", ("594918104",)),
        Stock("AMZN", "us", "亞馬遜", "Amazon", ("023135106",)),
        Stock("TSM", "us", "台積電 ADR", "TSMC ADR", _TSM, ("台積電", "TSMC")),
        Stock("META", "us", "Meta", "Meta", ("30303M102",), ("臉書", "Facebook")),
        Stock("AVGO", "us", "博通", "Broadcom", ("11135F101",)),
        Stock("TSLA", "us", "特斯拉", "Tesla", ("88160R101",)),
        Stock("MU", "us", "美光", "Micron", ("595112103",)),
        Stock("AMD", "us", "超微", "AMD", ("007903107",)),
        Stock("QQQ", "us", "那斯達克100 ETF", "Invesco QQQ", ("46090E103",)),
        Stock("VOO", "us", "標普500 ETF", "Vanguard S&P 500 ETF", ("922908363",)),
        Stock("2330", "tw", "台積電", "TSMC", _TSM, ("TSMC",)),
        Stock("2317", "tw", "鴻海", "Hon Hai", (), ("Foxconn", "鴻海精密")),
        Stock("2454", "tw", "聯發科", "MediaTek"),
        Stock("2382", "tw", "廣達", "Quanta"),
        Stock("2308", "tw", "台達電", "Delta Electronics"),
        Stock("0050", "tw", "元大台灣50", "Yuanta Taiwan 50"),
        Stock("006208", "tw", "富邦台50", "Fubon Taiwan 50"),
    )
}
"""Every stock with a page: the market strip's (``market_strip.TW_STOCKS``/``US_STOCKS``)."""


def investor_name(source: Source) -> str:
    """Who the investor is, as the source's title prefix says: ``巴菲特（Berkshire Hathaway）``
    → ``巴菲特``."""
    prefix = str(source.config.get(TITLE_PREFIX) or source.name)
    return prefix.split("（")[0].split("(")[0].strip() or source.name


def thirteenf_sources(sources: list[Source]) -> list[tuple[Source, str]]:
    """The company's 13F sources and each one's filer CIK (from its EDGAR feed's address)."""
    out = []
    for source in sources:
        match = _CIK.search(source.url or "")
        if match and source.config.get(PRIMARY) and source.config.get(SECTION) == "holdings":
            out.append((source, match.group(1)))
    return out


Fetch = Callable[[str], Awaitable[bytes]]


async def refresh_source(session: AsyncSession, source: Source, cik: str, fetch: Fetch) -> bool:
    """Store the latest 13F-HR's positions for one source if they are not stored yet. True
    when rows were written."""
    filer = thirteenf.parse_submissions(await fetch(thirteenf.submissions_url(cik)))
    originals = [f for f in filer.filings if f.form == "13F-HR" and f.period is not None]
    if not originals:
        return False
    current = max(originals, key=lambda f: (f.period, f.filed))
    stored = await session.scalar(
        select(InvestorPosition.accession).where(InvestorPosition.source_id == source.id).limit(1)
    )
    if stored == current.accession:
        return False

    filers = {filer.cik: filer}
    for other_cik in source.config.get(PREDECESSORS) or []:
        other = thirteenf.parse_submissions(await fetch(thirteenf.submissions_url(str(other_cik))))
        filers.setdefault(other.cik, other)
    before = thirteenf.previous_quarter(current, list(filers.values()))
    now = thirteenf.parse_filing(await fetch(current.ref.text_url))
    if not now.positions:
        return False
    prior = thirteenf.combine([thirteenf.parse_filing(await fetch(b.ref.text_url)) for b in before])
    diff = thirteenf.compare(now, prior)

    await session.execute(delete(InvestorPosition).where(InvestorPosition.source_id == source.id))
    total = now.total_value
    for change_kind, changes in (
        (PositionChange.NEW, diff.new),
        (PositionChange.INCREASED, diff.increased),
        (PositionChange.DECREASED, diff.decreased),
        (PositionChange.UNCHANGED, diff.unchanged),
        (PositionChange.SOLD_OUT, diff.sold_out),
    ):
        for change in changes:
            held = change.position
            session.add(
                InvestorPosition(
                    company_id=source.company_id,
                    source_id=source.id,
                    filer=filer.name,
                    cik=filer.cik,
                    accession=current.accession,
                    period=current.period,
                    previous_period=before[0].period if before else None,
                    cusip=held.cusip,
                    issuer=held.name,
                    title_of_class=held.title_of_class,
                    put_call=held.put_call,
                    kind=held.kind,
                    amount=change.now.amount if change.now else 0,
                    value_usd=change.now.value if change.now else 0,
                    previous_amount=change.before.amount if change.before else 0,
                    previous_value_usd=change.before.value if change.before else 0,
                    change=change_kind.value,
                    portfolio_value_usd=total,
                )
            )
    await session.flush()
    return True


async def refresh_holdings(session: AsyncSession, company_id: uuid.UUID, fetch: Fetch) -> int:
    """Every 13F source of the company, brought up to date. How many were rewritten. A source
    SEC cannot answer for is skipped until next time; the others go on."""
    sources = (await session.scalars(select(Source).where(Source.company_id == company_id))).all()
    written = 0
    for source, cik in thirteenf_sources(list(sources)):
        try:
            written += await refresh_source(session, source, cik, fetch)
        except (thirteenf.FilingError, FetchError) as error:
            log.warning("holdings: %s not refreshed: %s", source.name, error)
    return written


class HoldingsKeeper:
    def __init__(self, fetcher: PageFetcher) -> None:
        self.fetcher = fetcher

    async def fetch(self, url: str) -> bytes:
        return (await self.fetcher.fetch(url)).body

    def schedule_handler(self) -> Handler:
        """The ``newsroom.refresh_holdings`` handler."""

        async def handler(
            session: AsyncSession, schedule: Schedule, scheduled_for: datetime
        ) -> None:
            await refresh_holdings(session, schedule.company_id, self.fetch)

        return handler


# --- what a stock page reads --------------------------------------------------------------------


class PublicHolder(BaseModel):
    investor: str
    """As the site names them: ``巴菲特``."""
    filer: str
    period: date
    previous_period: date | None
    change: str
    """``new``, ``increased``, ``decreased``, ``unchanged`` or ``sold_out``."""
    title_of_class: str
    """As the filing names the class held: ``CL A``, ``CAP STK CL C`` — Alphabet has two."""
    put_call: str
    """Empty for shares; ``PUT`` or ``CALL`` for options on them."""
    shares: int
    previous_shares: int
    value_usd: int
    portfolio_pct: float | None
    """Of the filing's whole value; None when sold out (nothing held)."""
    filing_url: str


async def holders(
    session: AsyncSession, stock: Stock, *, company_id: uuid.UUID | None = None
) -> list[PublicHolder]:
    """Who, of the tracked investors, holds (or has just sold) the stock, largest first."""
    if not stock.cusips:
        return []
    query = (
        select(InvestorPosition, Source)
        .join(Source, Source.id == InvestorPosition.source_id)
        .where(InvestorPosition.cusip.in_(stock.cusips))
    )
    if company_id is not None:
        query = query.where(InvestorPosition.company_id == company_id)
    out = []
    for row, source in (await session.execute(query)).all():
        total = int(row.portfolio_value_usd)
        value = int(row.value_usd)
        out.append(
            PublicHolder(
                investor=investor_name(source),
                filer=row.filer,
                period=row.period,
                previous_period=row.previous_period,
                change=row.change,
                title_of_class=row.title_of_class,
                put_call=row.put_call,
                shares=int(row.amount),
                previous_shares=int(row.previous_amount),
                value_usd=value,
                portfolio_pct=round(value / total * 100, 2) if total and value else None,
                filing_url=thirteenf.FilingRef(cik=row.cik, accession=row.accession).index_url,
            )
        )
    return sorted(out, key=lambda h: (-h.value_usd, -h.previous_shares, h.investor))
