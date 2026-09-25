"""The market strip under the public site's header (D-048): a handful of numbers, never live.

Every number is one the site may show and says what it is: the Taiwan Stock Exchange's closing
figures (its OpenAPI, government open data), the previous day's close for US indices, the 10-year
yield and oil from FRED (the St. Louis Fed's service; a free key), US stocks' latest price from
Finnhub (a free key), and crypto's 24-hour change from CoinGecko (no key, credited on the page).
No LLM, no search credits.

Finnhub's free plan is for personal use: fine while the site is being built, and to be replaced by
its paid plan (or another licensed feed) before the site is public (D-048).

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

Basis = Literal["close", "prev_close", "last", "24h"]
"""close: the exchange's close that day; prev_close: the last close FRED has (US: the day
before); last: the latest trade, against the previous close (the close itself once the market
has shut); 24h: now, against 24 hours ago (crypto has no close)."""

TW_STOCKS = ("2330", "2317", "2454", "2382", "2308", "0050", "006208")
"""Taiwan stocks on the strip, the five that matter most to AI, by exchange code: TSMC, Hon Hai,
MediaTek, Quanta, Delta — the chips, the servers and their power — then the market as its ETFs,
Yuanta Taiwan 50 (0050) and Fubon Taiwan 50 (006208). Keyed
``tw:<code>``; the site shows the code as ``2330.TW``. The strip shows them in this order."""

US_STOCKS = (
    "NVDA", "AAPL", "GOOGL", "MSFT", "AMZN", "TSM", "META", "AVGO", "TSLA", "MU", "AMD",
    "QQQ", "VOO",
)  # fmt: skip
"""US stocks on the strip: the ones that matter most, largest first — by market value on
2026-09-25 (Finnhub's profiles; TSMC's, in NT$, at 31.8 to the dollar), to be re-ranked by hand
now and then — then the Nasdaq 100 and the S&P 500 as their ETFs. Keyed ``us:<symbol>``. The
Nasdaq Composite itself comes after them, from FRED."""

ORDER = (
    "taiex",
    *(f"tw:{code}" for code in TW_STOCKS),
    *(f"us:{symbol}" for symbol in US_STOCKS),
    "nasdaq",
    "us10y",
    "wti",
    "btc",
    "eth",
)
"""What the strip shows, in this order."""

TWSE_INDEX = "https://openapi.twse.com.tw/v1/exchangeReport/MI_INDEX"
TWSE_STOCKS = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TWSE_COMPANIES = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
"""Listed companies' particulars: the shares issued, for a market value (not ETFs)."""
FINNHUB_PROFILE = "https://finnhub.io/api/v1/stock/profile2"
DAY = 24 * 3600
FRED = "https://api.stlouisfed.org/fred/series/observations"
COINGECKO = "https://api.coingecko.com/api/v3/simple/price"
FINNHUB = "https://finnhub.io/api/v1/quote"

FRED_SERIES = {"nasdaq": "NASDAQCOM", "us10y": "DGS10", "wti": "DCOILWTICO"}
"""No S&P 500 index: VOO, beside the Nasdaq 100's QQQ, stands for it."""
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
    # a stock's details, for its page (D-049): the day's range and its size. None where the
    # service gives none (an index, a coin, an ETF's market value).
    open: float | None = None
    high: float | None = None
    low: float | None = None
    previous_close: float | None = None
    market_cap: float | None = None
    currency: str | None = None
    """Of the prices and the market value: ``TWD``, ``USD`` (Finnhub gives TSMC's ADR in TWD)."""


def _num(text: str) -> float:
    return float(text.replace(",", "").strip())


def _roc_date(text: str) -> date:
    """TWSE dates are Republic of China years: 1150924 is 2026-09-24."""
    return date(int(text[:-4]) + 1911, int(text[-4:-2]), int(text[-2:]))


def _pct(change: float, value: float) -> float | None:
    before = value - change
    return round(change / before * 100, 2) if before else None


# --- the services --------------------------------------------------------------------------


class Daily:
    """Something that changes slowly — shares issued, a market value — asked for at most once a
    day. A failure is not the figures' failure: what it had stays, or nothing."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.value: dict[str, float] = {}
        self.fetched_at: float | None = None
        self._clock = clock

    async def get(self, load: Callable[[], Awaitable[dict[str, float]]]) -> dict[str, float]:
        now = self._clock()
        if self.fetched_at is None or now - self.fetched_at >= DAY:
            self.fetched_at = now
            try:
                self.value = await load()
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
                log.warning("market strip: a daily figure failed: %s", _describe(error))
        return self.value


def _price(text: str | None) -> float | None:
    try:
        return _num(text) if text else None
    except ValueError:  # "--": no trade that day
        return None


def twse(
    shares: Daily | None = None,
) -> Callable[[httpx.AsyncClient], Awaitable[list[PublicQuote]]]:
    """TAIEX and ``TW_STOCKS`` at the exchange's latest close, with each stock's day (open, high,
    low, previous close) and its market value (the close times the shares issued)."""
    issued = shares or Daily()

    async def load_shares(client: httpx.AsyncClient) -> dict[str, float]:
        rows = (await client.get(TWSE_COMPANIES)).raise_for_status().json()
        return {
            row["公司代號"]: _num(row["已發行普通股數或TDR原股發行股數"])
            for row in rows
            if row.get("公司代號") in TW_STOCKS and row.get("已發行普通股數或TDR原股發行股數")
        }

    async def fetch(client: httpx.AsyncClient) -> list[PublicQuote]:
        return await _twse(client, await issued.get(lambda: load_shares(client)))

    return fetch


async def _twse(client: httpx.AsyncClient, shares: dict[str, float]) -> list[PublicQuote]:
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
                    open=_price(row.get("OpeningPrice")),
                    high=_price(row.get("HighestPrice")),
                    low=_price(row.get("LowestPrice")),
                    previous_close=round(value - change, 4),
                    market_cap=value * shares[row["Code"]] if row["Code"] in shares else None,
                    currency="TWD",
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


def finnhub(
    api_key: str, profiles: Daily | None = None
) -> Callable[[httpx.AsyncClient], Awaitable[list[PublicQuote]]]:
    """Each of ``US_STOCKS`` at its latest price (``c``), against the previous close (``d``,
    ``dp``), with its day (``o``, ``h``, ``l``, ``pc``) and — from its profile, once a day — its
    market value. The key goes in a header, not the URL. A symbol Finnhub has nothing for (all
    zeros) is left out."""
    caps = profiles or Daily()
    currencies: dict[str, str] = {}
    headers = {"X-Finnhub-Token": api_key}

    async def load_caps(client: httpx.AsyncClient) -> dict[str, float]:
        out = {}
        for symbol in US_STOCKS:
            row = (
                (await client.get(FINNHUB_PROFILE, params={"symbol": symbol}, headers=headers))
                .raise_for_status()
                .json()
            )
            if row.get("marketCapitalization"):
                out[symbol] = float(row["marketCapitalization"]) * 1_000_000  # given in millions
                currencies[symbol] = str(row.get("currency") or "USD")
        return out

    async def fetch(client: httpx.AsyncClient) -> list[PublicQuote]:
        market_caps = await caps.get(lambda: load_caps(client))
        out = []
        for symbol in US_STOCKS:
            response = await client.get(
                FINNHUB, params={"symbol": symbol}, headers={"X-Finnhub-Token": api_key}
            )
            row = response.raise_for_status().json()
            if not row.get("c") or not row.get("t"):
                continue
            out.append(
                PublicQuote(
                    key=f"us:{symbol}",
                    value=float(row["c"]),
                    change=None if row.get("d") is None else round(float(row["d"]), 4),
                    change_pct=None if row.get("dp") is None else round(float(row["dp"]), 2),
                    as_of=datetime.fromtimestamp(row["t"], UTC).date(),
                    basis="last",
                    source="Finnhub",
                    open=row.get("o") or None,
                    high=row.get("h") or None,
                    low=row.get("l") or None,
                    previous_close=row.get("pc") or None,
                    market_cap=market_caps.get(symbol),
                    currency=currencies.get(symbol, "USD") if symbol in market_caps else "USD",
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


def build_board(*, fred_api_key: str | None, finnhub_api_key: str | None = None) -> QuoteBoard:
    """The site's board. Without a key, that service's figures are left out, not faked."""
    feeds = [
        Feed("twse", twse(), every_seconds=30 * 60),
        Feed("coingecko", coingecko, every_seconds=5 * 60),
    ]
    if fred_api_key:
        feeds.append(Feed("fred", fred(fred_api_key), every_seconds=6 * 3600))
    if finnhub_api_key:
        # 13 symbols every 5 minutes: well inside the free plan's 60 calls a minute
        feeds.append(Feed("finnhub", finnhub(finnhub_api_key), every_seconds=5 * 60))
    return QuoteBoard(feeds)
