"""D-049: a stock's page — its figure, the tracked investors' positions in it, our articles."""

import httpx
import pytest

from autora.domains.newsroom.holdings import refresh_holdings
from autora.domains.newsroom.markets import edgar_13f
from autora.domains.newsroom.models import Source
from tests.conftest import unique_company
from tests.newsroom.test_holdings import Sec


@pytest.fixture
async def public(api):
    async with httpx.AsyncClient(transport=api._transport, base_url="http://test") as client:
        yield client


async def test_the_stock_page_api(public, committed):
    from autora_api.routers.public import market_board

    async with committed() as session:
        company = await unique_company(session)
        session.add(
            Source(
                company_id=company.id,
                name="SEC 13F：巴菲特",
                kind="rss",
                url=edgar_13f("0001067983"),
                config={
                    "title_prefix": "巴菲特（Berkshire Hathaway）",
                    "primary": True,
                    "section": "holdings",
                },
            )
        )
        await session.flush()
        await refresh_holdings(session, company.id, Sec())
        await session.commit()
        slug = company.slug

    class NoQuotes:
        async def quotes(self):
            return []

    public._transport.app.dependency_overrides[market_board] = lambda: NoQuotes()
    try:
        page = await public.get(
            "/api/public/stocks/aapl", params={"lang": "zh-TW", "company": slug}
        )
        assert page.status_code == 200, page.text
        body = page.json()
        assert (body["symbol"], body["market"], body["name"], body["quote"]) == (
            "AAPL",
            "us",
            "蘋果",
            None,
        )
        assert [h["investor"] for h in body["holders"]] == ["巴菲特"]
        assert body["articles"] == []
        en = await public.get("/api/public/stocks/2330", params={"lang": "en", "company": slug})
        assert (en.json()["name"], en.json()["holders"]) == ("TSMC", [])
        assert (
            await public.get("/api/public/stocks/XYZ", params={"lang": "en"})
        ).status_code == 404
        nobody = await public.get(
            "/api/public/stocks/AAPL", params={"lang": "en", "company": "nobody"}
        )
        assert nobody.status_code == 404
    finally:
        public._transport.app.dependency_overrides.pop(market_board)
