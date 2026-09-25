"""D-048: the market strip's public endpoint."""

import httpx

from tests.newsroom.test_market_strip import Clock, _board


async def test_the_public_endpoint(api):
    from autora_api.routers.public import market_board

    board = _board([], Clock())
    api._transport.app.dependency_overrides[market_board] = lambda: board
    try:
        async with httpx.AsyncClient(transport=api._transport, base_url="http://test") as public:
            got = (await public.get("/api/public/markets")).json()
    finally:
        api._transport.app.dependency_overrides.pop(market_board)
    assert got[0] == {
        "key": "taiex",
        "value": 48024.6,
        "change": -132.69,
        "change_pct": -0.28,
        "as_of": "2026-09-24",
        "basis": "close",
        "source": "TWSE",
        # an index has no day's range or market value
        "open": None,
        "high": None,
        "low": None,
        "previous_close": None,
        "market_cap": None,
        "currency": None,
    }
