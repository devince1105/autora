"""D-048: the market strip — each service read right, asked no more often than it changes, and a
failure shows the last figures rather than none."""

from datetime import date

import httpx
import pytest

from autora.domains.newsroom import market_strip as quotes
from autora.domains.newsroom.market_strip import Feed, QuoteBoard, build_board

TWSE_INDEX = [
    {"日期": "1150924", "指數": "寶島股價指數", "收盤指數": "53232.40", "漲跌": "-",
     "漲跌點數": "143.86", "漲跌百分比": "-0.27"},
    {"日期": "1150924", "指數": "發行量加權股價指數", "收盤指數": "48,024.60", "漲跌": "-",
     "漲跌點數": "132.69", "漲跌百分比": "-0.28"},
]  # fmt: skip
TWSE_STOCKS = [
    {"Date": "1150924", "Code": "2317", "ClosingPrice": "250.00", "Change": "1.0000"},
    {"Date": "1150924", "Code": "2330", "ClosingPrice": "2475.00", "Change": "-25.0000"},
    {"Date": "1150924", "Code": "2454", "ClosingPrice": "1,650.00", "Change": "15.0000"},
    {"Date": "1150924", "Code": "0050", "ClosingPrice": "112.40", "Change": "-0.0500"},
]


def _fred(values):
    return {"observations": [{"date": d, "value": v} for d, v in values]}


FRED = {
    "NASDAQCOM": _fred(
        [("2026-09-24", "27054.06"), ("2026-09-23", "."), ("2026-09-22", "26938.23")]
    ),
    "DGS10": _fred([("2026-09-24", "4.12"), ("2026-09-23", "4.15")]),
    "DCOILWTICO": _fred([("2026-09-24", ".")]),  # only a holiday: left out
}
FINNHUB = {
    "NVDA": {"c": 224.53, "d": 2.25, "dp": 1.0122, "pc": 222.28, "t": 1790280000},
    "TSM": {"c": 351.45, "d": -1.1, "dp": -0.312, "pc": 352.55, "t": 1790280000},
}  # any other symbol: all zeros, as Finnhub answers for one it has nothing for
COINS = {
    "bitcoin": {"usd": 83932, "usd_24h_change": -0.3368, "last_updated_at": 1790300000},
    "ethereum": {"usd": 2695.31, "usd_24h_change": 1.0004},
}


def _transport(calls: list[str], *, broken: set[str] = frozenset()):
    def handle(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append(request.url.host)
        if request.url.host in broken:
            return httpx.Response(503)
        if url.startswith(quotes.TWSE_INDEX):
            return httpx.Response(200, json=TWSE_INDEX)
        if url.startswith(quotes.TWSE_STOCKS):
            return httpx.Response(200, json=TWSE_STOCKS)
        if url.startswith(quotes.FRED):
            assert request.url.params["api_key"] == "k"
            return httpx.Response(200, json=FRED[request.url.params["series_id"]])
        if url.startswith(quotes.FINNHUB):
            assert request.headers["X-Finnhub-Token"] == "fk" and "fk" not in url
            empty = {"c": 0, "d": None, "dp": None, "pc": 0, "t": 0}
            return httpx.Response(200, json=FINNHUB.get(request.url.params["symbol"], empty))
        if url.startswith(quotes.COINGECKO):
            return httpx.Response(200, json=COINS)
        return httpx.Response(404)

    return httpx.MockTransport(handle)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _board(calls, clock, **kw):
    board = build_board(fred_api_key="k", finnhub_api_key="fk")
    board._client = lambda: httpx.AsyncClient(transport=_transport(calls, **kw))
    board._clock = clock
    return board


async def test_each_service_read_right_and_shown_in_order():
    shown = await _board([], Clock()).quotes()
    assert [q.key for q in shown] == [
        *("taiex", "tw:2330", "tw:2317", "tw:2454", "tw:0050"),
        *("us:NVDA", "us:TSM", "nasdaq", "us10y", "btc", "eth"),
    ]
    by = {q.key: q for q in shown}
    assert (by["taiex"].value, by["taiex"].change, by["taiex"].change_pct) == (
        48024.6,
        -132.69,
        -0.28,
    )
    assert by["taiex"].as_of == date(2026, 9, 24) and by["taiex"].basis == "close"
    tsmc = by["tw:2330"]
    assert (tsmc.value, tsmc.change, tsmc.change_pct) == (2475.0, -25.0, -1.0)
    assert by["tw:2454"].value == 1650.0  # thousands separators read
    assert (by["tw:0050"].value, by["tw:0050"].change) == (112.4, -0.05)  # an ETF, like a stock
    # the day FRED has no figure for is skipped: the change is against the day before that
    assert by["nasdaq"].change == pytest.approx(115.83) and by["nasdaq"].basis == "prev_close"
    assert by["us10y"].change == pytest.approx(-0.03) and by["us10y"].change_pct is None
    assert by["btc"].basis == "24h" and by["btc"].change_pct == -0.34 and by["btc"].change < 0
    nvda = by["us:NVDA"]
    assert (nvda.value, nvda.change, nvda.change_pct, nvda.basis) == (224.53, 2.25, 1.01, "last")
    assert by["us:TSM"].change_pct == -0.31
    assert {q.source for q in shown} == {"TWSE", "FRED", "Finnhub", "CoinGecko"}


async def test_asked_again_only_when_the_figures_are_old():
    calls, clock = [], Clock()
    board = _board(calls, clock)
    await board.quotes()
    first = len(calls)
    await board.quotes()
    assert len(calls) == first  # a second reader: served from the board
    clock.now += 5 * 60
    await board.quotes()
    # crypto and the US stocks are due, the exchange and FRED are not
    assert set(calls[first:]) == {"api.coingecko.com", "finnhub.io"}


async def test_a_failing_service_keeps_its_last_figures_and_is_not_hammered():
    calls, clock = [], Clock()
    board = _board(calls, clock)
    before = {q.key: q.value for q in await board.quotes()}
    clock.now += 31 * 60
    board._client = lambda: httpx.AsyncClient(
        transport=_transport(calls, broken={"openapi.twse.com.tw", "api.coingecko.com"})
    )
    after = {q.key: q.value for q in await board.quotes()}
    assert after == before
    n = len(calls)
    await board.quotes()
    assert len(calls) == n  # the failure counts as asking


async def test_without_a_fred_key_the_us_figures_are_left_out_not_faked():
    assert [f.name for f in build_board(fred_api_key=None).feeds] == ["twse", "coingecko"]
    names = [f.name for f in build_board(fred_api_key=None, finnhub_api_key="fk").feeds]
    assert names == ["twse", "coingecko", "finnhub"]
    never = QuoteBoard(
        [Feed("down", quotes.coingecko, every_seconds=60)],
        client=lambda: httpx.AsyncClient(transport=_transport([], broken={"api.coingecko.com"})),
    )
    assert await never.quotes() == []


async def test_a_failure_is_logged_without_the_key(caplog):
    board = QuoteBoard(
        [Feed("fred", quotes.fred("secret-key-123"), every_seconds=60)],
        client=lambda: httpx.AsyncClient(transport=_transport([], broken={"api.stlouisfed.org"})),
    )
    await board.quotes()
    assert "HTTP 503 from api.stlouisfed.org/fred/series/observations" in caplog.text
    assert "secret-key-123" not in caplog.text
