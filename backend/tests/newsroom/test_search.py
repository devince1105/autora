"""T-500: web_search — the Tavily adapter, the fixture provider and the tool.

The Tavily adapter is tested against a mock transport (no network); the live smoke is
``test_tavily.py`` (integration, needs TAVILY_API_KEY).
"""

import json
import tempfile
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select

import autora.domains.newsroom as newsroom
from autora.app import build_embedder, build_page_fetcher, build_search_provider, build_tools
from autora.db.models import EventRecord
from autora.domains.newsroom.tools.search import CANDIDATES_NOTE
from autora.infra.blobstore import LocalFSBlobStore
from autora.infra.search import SearchRejected, SearchResponse, SearchUnavailable
from autora.infra.search.fixture import FixtureDocument, FixtureSearchProvider
from autora.infra.search.tavily import ENDPOINT, RateLimiter, TavilySearchProvider
from autora.infra.settings import Settings
from autora.runtime.actor import Actor
from autora.runtime.tools import ToolRegistry
from tests.conftest import unique_company

KEY = SecretStr("tvly-test-secret-key")
DB = "postgresql+asyncpg://u:p@localhost/db"
CORPUS = Path(newsroom.__file__).parent / "fixtures" / "search.json"


def tavily(handler, **kwargs) -> TavilySearchProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return TavilySearchProvider(KEY, client=client, **kwargs)


# --- Tavily adapter --------------------------------------------------------------------------


async def test_tavily_request_and_results():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "query": "microgrid",
                "results": [
                    {
                        "title": "A",
                        "url": "https://a.example/1",
                        "content": "x" * 5000,
                        "score": 0.9,
                        "published_date": "Tue, 15 Sep 2026 08:00:00 GMT",
                    },
                    {
                        "title": "",
                        "url": "https://b.example/2",
                        "content": "b",
                        "score": 0.5,
                        "published_date": "2026-09-14",
                    },
                    {
                        "title": "C",
                        "url": "https://c.example/3",
                        "content": "c",
                        "published_date": "soon",
                    },
                ],
                "response_time": 0.4,
                "usage": {"credits": 1},
            },
        )

    response = await tavily(handler).search("microgrid", k=3, recency_days=5)

    assert seen["url"] == ENDPOINT
    assert seen["auth"] == "Bearer tvly-test-secret-key"
    body = seen["body"]
    assert "api_key" not in body and KEY.get_secret_value() not in json.dumps(body)
    assert body["query"] == "microgrid" and body["max_results"] == 3
    assert body["time_range"] == "week" and body["include_usage"] is True
    assert body["search_depth"] == "basic" and body["include_raw_content"] is False

    assert response.provider == "tavily"
    assert response.cost_usd == Decimal("0.008")
    a, b, c = response.results
    assert len(a.snippet) == 1000  # long page text is cut: a snippet, not the page
    assert a.published_at == datetime(2026, 9, 15, 8, tzinfo=UTC)
    assert b.title == "https://b.example/2" and b.published_at == datetime(2026, 9, 14, tzinfo=UTC)
    assert c.published_at is None


@pytest.mark.parametrize(
    ("days", "expected"), [(None, None), (1, "day"), (7, "week"), (30, "month"), (200, "year")]
)
async def test_tavily_recency(days, expected):
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"results": []})

    await tavily(handler).search("q", k=1, recency_days=days)
    assert bodies[0].get("time_range") == expected


async def test_tavily_cost_without_usage_falls_back_to_the_depths_price():
    def handler(request):
        return httpx.Response(200, json={"results": []})

    basic = await tavily(handler).search("q", k=1)
    advanced = await tavily(handler, depth="advanced", cost_per_credit=Decimal("0.01")).search(
        "q", k=1
    )
    assert basic.cost_usd == Decimal("0.008")
    assert advanced.cost_usd == Decimal("0.02")


@pytest.mark.parametrize(
    ("status", "kind", "retryable"),
    [
        (401, SearchRejected, False),
        (432, SearchRejected, False),
        (400, SearchRejected, False),
        (429, SearchUnavailable, True),
        (503, SearchUnavailable, True),
    ],
)
async def test_tavily_errors(status, kind, retryable):
    def handler(request):
        return httpx.Response(
            status, json={"detail": {"error": "nope"}}, headers={"retry-after": "7"}
        )

    with pytest.raises(kind) as caught:
        await tavily(handler).search("q", k=1)
    assert caught.value.retryable is retryable
    assert KEY.get_secret_value() not in str(caught.value)
    assert f"Tavily {status}: nope" in str(caught.value)
    if status == 429:
        assert "retry after 7s" in str(caught.value)


async def test_tavily_timeout_is_retryable():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(SearchUnavailable, match="did not answer within 15.0s"):
        await tavily(handler).search("q", k=1)


async def test_rate_limiter_waits_for_a_slot_or_gives_up():
    now = [0.0]
    slept = []

    async def sleep(seconds):
        slept.append(seconds)
        now[0] += seconds

    patient = RateLimiter(2, max_wait_s=30, clock=lambda: now[0], sleep=sleep)
    await patient.acquire()  # t=0
    now[0] = 20
    await patient.acquire()  # t=20: the window is full
    now[0] = 45
    await patient.acquire()  # the t=0 slot frees at t=60: waits 15 s
    assert slept == [15] and now[0] == 60

    now[0] = 0.0
    impatient = RateLimiter(1, max_wait_s=5, clock=lambda: now[0], sleep=sleep)
    await impatient.acquire()
    now[0] = 10
    with pytest.raises(SearchUnavailable, match=r"rate limit \(1/min\) reached; try again in 50s"):
        await impatient.acquire()


# --- fixture provider ------------------------------------------------------------------------


async def test_fixture_ranks_by_shared_terms_and_never_invents():
    provider = FixtureSearchProvider.from_file(CORPUS)
    response = await provider.search("Lumen City microgrid battery cost", k=3)
    assert response.provider == "fixture" and response.cost_usd == 0
    urls = [r.url for r in response.results]
    assert len(urls) == 3 and all("fixtures.autora.test" in u for u in urls)
    assert "https://news.fixtures.autora.test/coffee-prices" not in urls
    assert response.results[0].score >= response.results[-1].score

    chinese = await provider.search("流明市 微電網", k=5)
    assert chinese.results[0].url == "https://city.fixtures.autora.test/press/2026-09-14-microgrid"

    assert (await provider.search("volcano eruption", k=5)).results == []


async def test_fixture_recency_uses_published_dates():
    docs = [
        FixtureDocument(
            url="https://x.test/old",
            title="grid old",
            snippet="",
            published_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
        FixtureDocument(
            url="https://x.test/new",
            title="grid new",
            snippet="",
            published_at=datetime(2026, 9, 18, tzinfo=UTC),
        ),
        FixtureDocument(url="https://x.test/undated", title="grid undated", snippet=""),
    ]
    provider = FixtureSearchProvider(docs, now=datetime(2026, 9, 19, tzinfo=UTC))
    assert [r.url for r in (await provider.search("grid", k=5, recency_days=7)).results] == [
        "https://x.test/new"
    ]
    assert len((await provider.search("grid", k=5)).results) == 3


# --- the tool --------------------------------------------------------------------------------


class _Stub:
    name = "stub"

    def __init__(self, outcome):
        self.outcome = outcome

    async def search(self, query, *, k, recency_days=None):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


async def _invoke(committed, provider, args):
    from autora.domains.newsroom.tools import register_tools

    async with committed() as session:
        company = await unique_company(session, "search")
        await session.commit()
    registry = ToolRegistry(committed)
    register_tools(
        registry,
        search_provider=provider,
        fetcher=build_page_fetcher(None),
        blobs=LocalFSBlobStore(tempfile.mkdtemp()),
        embedder=build_embedder(None),
    )
    call_id = f"call_{uuid.uuid4().hex[:8]}"
    result = await registry.invoke(
        "web_search",
        args,
        company_id=company.id,
        actor=Actor.system("test"),
        tool_call_id=call_id,
        run_id=uuid.uuid4(),
    )
    async with committed() as session:
        events = (
            await session.scalars(
                select(EventRecord)
                .where(EventRecord.payload["tool_call_id"].astext == call_id)
                .order_by(EventRecord.seq)
            )
        ).all()
    return result, [(e.event_type, e.payload) for e in events]


async def test_tool_returns_candidates_records_cost_and_produces_nothing(committed):
    provider = FixtureSearchProvider.from_file(CORPUS)
    result, events = await _invoke(committed, provider, {"query": "microgrid", "k": 2})
    assert result.ok
    assert len(result.output["results"]) == 2
    assert result.output["note"] == CANDIDATES_NOTE
    assert result.produced == []  # candidates are not evidence (D-003)
    (called, _), (completed, payload) = events
    assert (called, completed) == ("TOOL_CALLED", "TOOL_COMPLETED")
    assert payload["produced"] == []
    assert Decimal(str(payload["cost_usd"])) == 0
    assert "2 results for 'microgrid' (fixture)" == payload["result_summary"]


async def test_tool_records_a_live_price(committed):
    paid = SearchResponse(results=[], provider="stub", cost_usd=Decimal("0.016"))
    _, events = await _invoke(committed, _Stub(paid), {"query": "q"})
    assert Decimal(str(events[-1][1]["cost_usd"])) == Decimal("0.016")


@pytest.mark.parametrize(
    ("error", "will_retry"),
    [(SearchRejected("Tavily 401"), False), (SearchUnavailable("Tavily 503"), True)],
)
async def test_tool_failures_say_whether_to_retry(committed, error, will_retry):
    result, events = await _invoke(committed, _Stub(error), {"query": "q"})
    assert not result.ok and result.will_retry is will_retry
    assert events[-1][0] == "TOOL_FAILED"
    assert events[-1][1]["will_retry"] is will_retry


async def test_tool_arguments_are_bounded(committed):
    result, _ = await _invoke(
        committed, _Stub(SearchResponse(results=[], provider="stub")), {"query": "q", "k": 50}
    )
    assert not result.ok and result.error_class == "InvalidToolArguments"


# --- wiring ----------------------------------------------------------------------------------


def test_profile_chooses_the_provider(committed):
    assert build_search_provider(None).name == "fixture"
    assert (
        build_search_provider(
            Settings(_env_file=None, database_url=DB, tools_profile="fixture")
        ).name
        == "fixture"
    )
    live = build_search_provider(
        Settings(_env_file=None, database_url=DB, tools_profile="live", tavily_api_key="tvly-x")
    )
    assert isinstance(live, TavilySearchProvider)
    with pytest.raises(ValueError, match="TAVILY_API_KEY is required"):
        Settings(_env_file=None, database_url=DB, tools_profile="live")
    assert "web_search" in build_tools(committed).names()
