"""The market strip under the public site's header (D-048): a handful of numbers, never live.

Every number is one the site may show and says what it is: the Taiwan Stock Exchange's closing
figures (its OpenAPI, government open data), the previous day's close for US indices, the 10-year
yield and oil from FRED (the St. Louis Fed's service; a free key), and crypto's 24-hour change from
CoinGecko (no key, credited on the page). No LLM, no search credits.

A reader's page never waits on these services. The board keeps the last good figures in the
process and asks each service again only when that service's figures are old enough to have
changed: the exchange publishes once a day, FRED once a day, crypto trades all the time. A
service that fails keeps showing what it gave last; one never answered is simply left out.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Literal

import httpx
from pydantic import BaseModel

log = logging.getLogger(__name__)

Basis = Literal["close", "prev_close", "24h"]
"""close: the exchange's close that day; prev_close: the last close FRED has (US: the day
before); 24h: now, against 24 hours ago (crypto has no close)."""

TW_STOCKS = ("2330", "2317", "2454", "2382", "3231", "6669", "2308", "3711", "2376")
"""Taiwan stocks on the strip, AI first, by exchange code: TSMC, Hon Hai, MediaTek, Quanta,
Wistron, Wiwynn, Delta, ASE, Gigabyte — the chips and the AI server supply chain. Keyed
``tw:<code>``; the site shows the code as ``2330.TW``. The strip shows them in this order."""

ORDER = (
    "taiex",
    *(f"tw:{code}" for code in TW_STOCKS),
    "spx",
    "nasdaq",
    "us10y",
    "wti",
    "btc",
    "eth",
)
"""What the strip shows, in this order."""

TWSE_INDEX = "https://openapi.twse.com.tw/v1/exchangeReport/MI_INDEX"
TWSE_STOCKS = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
FRED = "https://api.stlouisfed.org/fred/series/observations"
COINGECKO = "https://api.coingecko.com/api/v3/simple/price"

FRED_SERIES = {"spx": "SP500", "nasdaq": "NASDAQCOM", "us10y": "DGS10", "wti": "DCOILWTICO"}
COINS = {"btc": "bitcoin", "eth": "ethereum"}


class PublicQuote(BaseModel):
    key: str
    """One of ``ORDER``; the site names it in the reader's language."""
    value: float
    change: float | None
    """Against the previous figure, in the value's own unit (for the yield: percentage points)."""
    change_pct: float | None
    """None for the yield: a change in a percentage is given in points, not as a percentage."""
    as_of: date
    basis: Basis
    source: str
    """Who the figure is from, for the credit on the page."""


def _num(text: str) -> float:
    return float(text.replace(",", "").strip())


def _roc_date(text: str) -> date:
    """TWSE dates are Republic of China years: 1150924 is 2026-09-24."""
    return date(int(text[:-4]) + 1911, int(text[-4:-2]), int(text[-2:]))


def _pct(change: float, value: float) -> float | None:
    before = value - change
    return round(change / before * 100, 2) if before else None


# --- the services --------------------------------------------------------------------------


async def twse(client: httpx.AsyncClient) -> list[PublicQuote]:
    """TAIEX and ``TW_STOCKS`` at the exchange's latest close."""
    out: list[PublicQuote] = []
    index = (await client.get(TWSE_INDEX)).raise_for_status().json()
    for row in index:
        if row.get("指數") == "發行量加權股價指數":
            sign = -1 if row.get("漲跌") == "-" else 1
            out.append(
                PublicQuote(
                    key="taiex",
                    value=_num(row["收盤指數"]),
                    change=sign * _num(row["漲跌點數"]),
                    change_pct=_num(row["漲跌百分比"]),
                    as_of=_roc_date(row["日期"]),
                    basis="close",
                    source="TWSE",
                )
            )
            break
    stocks = (await client.get(TWSE_STOCKS)).raise_for_status().json()
    for row in stocks:
        if row.get("Code") in TW_STOCKS:
            value, change = _num(row["ClosingPrice"]), _num(row["Change"])
            out.append(
                PublicQuote(
                    key=f"tw:{row['Code']}",
                    value=value,
                    change=change,
                    change_pct=_pct(change, value),
                    as_of=_roc_date(row["Date"]),
                    basis="close",
                    source="TWSE",
                )
            )
    return out


def fred(api_key: str) -> Callable[[httpx.AsyncClient], Awaitable[list[PublicQuote]]]:
    async def fetch(client: httpx.AsyncClient) -> list[PublicQuote]:
        out = []
        for key, series in FRED_SERIES.items():
            response = await client.get(
                FRED,
                params={
                    "series_id": series,
                    "api_key": api_key,
                    "file_type": "json",
                    "sort_order": "desc",
                    "limit": 10,
                },
            )
            rows = response.raise_for_status().json()["observations"]
            # "." is a day with no figure (a holiday): the last two days that have one
            known = [(r["date"], float(r["value"])) for r in rows if r["value"] not in (".", "")]
            if len(known) < 2:
                continue
            (day, value), (_, before) = known[0], known[1]
            change = round(value - before, 4)
            out.append(
                PublicQuote(
                    key=key,
                    value=value,
                    change=change,
                    change_pct=None if key == "us10y" else _pct(change, value),
                    as_of=date.fromisoformat(day),
                    basis="prev_close",
                    source="FRED",
                )
            )
        return out

    return fetch


async def coingecko(client: httpx.AsyncClient) -> list[PublicQuote]:
    response = await client.get(
        COINGECKO,
        params={
            "ids": ",".join(COINS.values()),
            "vs_currencies": "usd",
            "include_24hr_change": "true",
            "include_last_updated_at": "true",
        },
    )
    prices = response.raise_for_status().json()
    out = []
    for key, coin in COINS.items():
        row = prices.get(coin)
        if not row or row.get("usd") is None:
            continue
        value = float(row["usd"])
        pct = row.get("usd_24h_change")
        change = None if pct is None else round(value - value / (1 + pct / 100), 2)
        at = row.get("last_updated_at")
        out.append(
            PublicQuote(
                key=key,
                value=value,
                change=change,
                change_pct=None if pct is None else round(pct, 2),
                as_of=(datetime.fromtimestamp(at, UTC) if at else datetime.now(UTC)).date(),
                basis="24h",
                source="CoinGecko",
            )
        )
    return out


# --- the board -----------------------------------------------------------------------------


@dataclass
class Feed:
    name: str
    fetch: Callable[[httpx.AsyncClient], Awaitable[list[PublicQuote]]]
    every_seconds: float
    """How long its figures are good for: asked again after that, not before."""
    quotes: dict[str, PublicQuote] = field(default_factory=dict)
    fetched_at: float | None = None
    """When it was last asked (monotonic), answered or not: a failing service is not asked on
    every page view."""


def _describe(error: Exception) -> str:
    """What went wrong, without the query string: FRED's key travels in it (``api_key=``), and
    httpx puts the whole URL in its messages."""
    if isinstance(error, httpx.HTTPStatusError):
        url = error.request.url
        return f"HTTP {error.response.status_code} from {url.host}{url.path}"
    if isinstance(error, httpx.RequestError):
        url = error.request.url
        return f"{type(error).__name__} for {url.host}{url.path}"
    return type(error).__name__


class QuoteBoard:
    def __init__(
        self,
        feeds: list[Feed],
        *,
        client: Callable[[], httpx.AsyncClient] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.feeds = feeds
        self._client = client or (
            lambda: httpx.AsyncClient(timeout=10, headers={"User-Agent": "AiSiWhale market strip"})
        )
        self._clock = clock
        self._lock = asyncio.Lock()

    async def quotes(self) -> list[PublicQuote]:
        now = self._clock()
        due = [
            f for f in self.feeds if f.fetched_at is None or now - f.fetched_at >= f.every_seconds
        ]
        if due:
            async with self._lock:
                # another request may have refreshed them while this one waited
                due = [
                    f
                    for f in due
                    if f.fetched_at is None or self._clock() - f.fetched_at >= f.every_seconds
                ]
                if due:
                    async with self._client() as client:
                        await asyncio.gather(*(self._refresh(f, client) for f in due))
        shown = {key: q for f in self.feeds for key, q in f.quotes.items()}
        return [shown[key] for key in ORDER if key in shown]

    async def _refresh(self, feed: Feed, client: httpx.AsyncClient) -> None:
        feed.fetched_at = self._clock()
        try:
            fresh = await feed.fetch(client)
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
            log.warning(
                "market strip: %s failed, keeping its last figures: %s", feed.name, _describe(error)
            )
            return
        feed.quotes.update({q.key: q for q in fresh})


def build_board(*, fred_api_key: str | None) -> QuoteBoard:
    """The site's board. Without a FRED key the US figures are left out, not faked."""
    feeds = [
        Feed("twse", twse, every_seconds=30 * 60),
        Feed("coingecko", coingecko, every_seconds=5 * 60),
    ]
    if fred_api_key:
        feeds.append(Feed("fred", fred(fred_api_key), every_seconds=6 * 3600))
    return QuoteBoard(feeds)
