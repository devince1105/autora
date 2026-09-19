"""T-500 live smoke: one real Tavily search through the web_search tool, with its cost recorded.

Run with ``pytest backend -m integration``. Skipped unless TAVILY_API_KEY is set (in .env or the
environment); it costs one basic search (1 credit). Only the shape is asserted: the web changes.
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from autora.db.models import EventRecord
from autora.domains.newsroom.tools import register_tools
from autora.infra.search.tavily import TavilySearchProvider
from autora.infra.settings import SettingsError, load_settings
from autora.runtime.actor import Actor
from autora.runtime.tools import ToolRegistry
from tests.conftest import unique_company


def _key():
    try:
        return load_settings().tavily_api_key
    except SettingsError:
        return None


KEY = _key()
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(KEY is None, reason="needs TAVILY_API_KEY"),
]


async def test_one_real_search_records_its_cost(committed):
    settings = load_settings()
    provider = TavilySearchProvider(
        KEY,
        depth="basic",
        cost_per_credit=settings.tavily_cost_per_credit,
        timeout_s=settings.tavily_timeout_seconds,
    )
    registry = ToolRegistry(committed)
    register_tools(registry, search_provider=provider)
    async with committed() as session:
        company = await unique_company(session, "tavily")
        await session.commit()
    call_id = f"call_{uuid.uuid4().hex[:8]}"
    result = await registry.invoke(
        "web_search",
        {"query": "solar microgrid pilot city", "k": 3},
        company_id=company.id,
        actor=Actor.system("smoke"),
        tool_call_id=call_id,
        run_id=uuid.uuid4(),
    )
    assert result.ok, result.message
    assert 1 <= len(result.output["results"]) <= 3
    assert all(r["url"].startswith("http") for r in result.output["results"])
    async with committed() as session:
        completed = await session.scalar(
            select(EventRecord).where(
                EventRecord.payload["tool_call_id"].astext == call_id,
                EventRecord.event_type == "TOOL_COMPLETED",
            )
        )
    assert Decimal(str(completed.payload["cost_usd"])) == settings.tavily_cost_per_credit
    assert completed.payload["produced"] == []
