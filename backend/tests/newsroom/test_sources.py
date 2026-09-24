"""T-501: sources, feeds and the poller (and the shared page fetcher it uses).

Polls run against the fixture feeds (``FixtureFetcher``) or stubs; the live ``HttpFetcher`` is
tested with a mock transport and a fake DNS resolver. Most tests work in a rolled-back session;
the one that goes through the scheduler commits and disables its schedule afterwards (scheduler
tests tick every due schedule in the database).
"""

import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select, update

import autora.domains.newsroom as newsroom
from autora.app import build_page_fetcher, build_scheduler
from autora.db.models import EventRecord, Schedule
from autora.domains.newsroom import settings as newsroom_settings
from autora.domains.newsroom.feeds import FeedError, parse_feed
from autora.domains.newsroom.models import Source, SourceItem, SourceStatus
from autora.domains.newsroom.sources import (
    POLL_SCHEDULE,
    SourceConfigError,
    SourcePoller,
    add_source,
    canonical_url,
    content_hash,
)
from autora.infra.http import (
    USER_AGENT,
    FetchedPage,
    FetchRefused,
    FetchUnavailable,
    FixtureFetcher,
    HttpFetcher,
)
from autora.infra.search import SearchResponse, SearchResult, SearchUnavailable
from autora.infra.search.fixture import FixtureSearchProvider
from tests.conftest import unique_company

FIXTURES = Path(newsroom.__file__).parent / "fixtures"
FEED = "https://news.fixtures.autora.test/feed.xml"
ATOM = "https://city.fixtures.autora.test/press/feed.atom"
T0 = datetime(2026, 9, 19, 6, 0, tzinfo=UTC)


def fixture_fetcher() -> FixtureFetcher:
    return FixtureFetcher(FIXTURES, json.loads((FIXTURES / "routes.json").read_text()))


class Clock:
    def __init__(self, now: datetime):
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def poller(clock: Clock | None = None, **kw) -> SourcePoller:
    return SourcePoller(
        fetcher=kw.pop("fetcher", fixture_fetcher()),
        search=kw.pop("search", FixtureSearchProvider.from_file(FIXTURES / "search.json")),
        clock=clock or Clock(T0),
        **kw,
    )


async def events(session, source_id: uuid.UUID) -> list[tuple[str, dict]]:
    rows = (
        await session.scalars(
            select(EventRecord)
            .where(EventRecord.aggregate_type == "source", EventRecord.aggregate_id == source_id)
            .order_by(EventRecord.seq)
        )
    ).all()
    return [(r.event_type, r.payload) for r in rows]


# --- feeds ----------------------------------------------------------------------------------


def test_rss_fixture():
    entries = parse_feed((FIXTURES / "feeds" / "lumen-news.xml").read_bytes())
    assert [e.external_id for e in entries] == [
        "lumen-news-1001",
        "lumen-news-1002",
        "lumen-news-0990",
    ]
    first = entries[0]
    assert (
        first.summary
        == "The pilot in Harbor District links 1,200 rooftop panels and a 4 MWh battery."
    )
    assert first.published_at == datetime(2026, 9, 15, 8, tzinfo=UTC)
    assert "utm_source" in first.url  # the parser keeps the link; the poller canonicalizes it


def test_atom_fixture():
    entries = parse_feed((FIXTURES / "feeds" / "city-press.atom").read_bytes())
    press, costs = entries
    assert press.url == "https://city.fixtures.autora.test/press/2026-09-14-microgrid"
    assert press.title.startswith("流明市港區社區微電網啟用")
    assert press.summary == "港區微電網串連 1,200 組屋頂太陽能板與 4 MWh 儲能電池。"
    assert press.published_at == datetime(2026, 9, 14, 2, tzinfo=UTC)
    assert costs.published_at == datetime(2026, 9, 16, 10, 30, tzinfo=UTC)  # from <updated>
    assert costs.external_id == "urn:fixture:press:microgrid-costs"


def test_rss1_rdf_and_permalink_guids():
    rdf = b"""<?xml version="1.0"?>
    <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns="http://purl.org/rss/1.0/"
             xmlns:dc="http://purl.org/dc/elements/1.1/">
      <item rdf:about="https://x.test/a"><title>A</title><dc:date>2026-09-01T00:00:00Z</dc:date></item>
    </rdf:RDF>"""
    [entry] = parse_feed(rdf)
    assert (entry.url, entry.external_id, entry.published_at) == (
        "https://x.test/a",
        "https://x.test/a",
        datetime(2026, 9, 1, tzinfo=UTC),
    )
    rss = (
        b"<rss><channel><item><title>G</title><guid>https://x.test/g</guid></item></channel></rss>"
    )
    assert parse_feed(rss)[0].url == "https://x.test/g"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">]><rss/>', "DTD"),
        (b"<rss><channel><item>", "well-formed"),
        (b"<html><body>not a feed</body></html>", "unknown feed format"),
    ],
)
def test_bad_feeds_are_refused(body, message):
    with pytest.raises(FeedError, match=message):
        parse_feed(body)


# --- item identity --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "HTTPS://News.Example.com:443/a/b/?utm_source=x&id=7#top",
            "https://news.example.com/a/b?id=7",
        ),
        ("http://example.com:8080/", "http://example.com:8080/"),
        ("https://example.com", "https://example.com/"),
        ("https://example.com/p?fbclid=1&gclid=2", "https://example.com/p"),
    ],
)
def test_canonical_url(url, expected):
    assert canonical_url(url) == expected


def test_content_hash_ignores_tracking_and_whitespace():
    a = content_hash("https://x.test/a?utm_medium=rss", "Solar   Grid ")
    assert a == content_hash("https://X.test/a", "solar grid")
    assert a != content_hash("https://x.test/a", "Solar grid v2")


# --- adding sources -------------------------------------------------------------------------


async def test_add_source_validates_and_creates_one_schedule(db_session):
    company = await unique_company(db_session, "src")
    await add_source(db_session, company_id=company.id, name="news", kind="rss", url=FEED, now=T0)
    await add_source(
        db_session,
        company_id=company.id,
        name="list",
        kind="url_list",
        config={"urls": ["https://x.test/a"]},
        now=T0,
    )
    schedules = (
        await db_session.scalars(select(Schedule).where(Schedule.company_id == company.id))
    ).all()
    assert sorted((s.name, s.handler, s.cron) for s in schedules) == [
        ("newsroom.cluster_stories", "newsroom.cluster_stories", "2-59/5 * * * *"),
        (POLL_SCHEDULE, POLL_SCHEDULE, "*/5 * * * *"),
    ]

    bad = [
        {"kind": "rss", "url": "ftp://x.test/feed"},
        {"kind": "url_list", "config": {"urls": []}},
        {"kind": "url_list", "config": {"urls": ["javascript:alert(1)"]}},
        {"kind": "search_query", "config": {"query": " "}},
        {"kind": "search_query", "config": {"query": "q", "k": 50}},
    ]
    for i, args in enumerate(bad):
        with pytest.raises(SourceConfigError):
            await add_source(db_session, company_id=company.id, name=f"bad{i}", **args)
    with pytest.raises(ValueError):
        await add_source(db_session, company_id=company.id, name="odd", kind="carrier_pigeon")


# --- polling --------------------------------------------------------------------------------


async def test_rss_poll_finds_items_once(db_session):
    company = await unique_company(db_session, "rss")
    source = await add_source(
        db_session, company_id=company.id, name="news", kind="rss", url=FEED, now=T0
    )
    clock = Clock(T0)
    outcome = await poller(clock).poll(db_session, source)

    assert outcome.error is None and outcome.seen == 3 and len(outcome.new_item_ids) == 3
    items = (
        await db_session.scalars(select(SourceItem).where(SourceItem.source_id == source.id))
    ).all()
    urls = {i.url for i in items}
    assert "https://news.fixtures.autora.test/lumen-city-microgrid-pilot" in urls  # utm_* removed
    trail = await events(db_session, source.id)
    assert [t for t, _ in trail] == ["SOURCE_ITEM_DISCOVERED"] * 3 + ["SOURCE_POLLED"]
    # newest first
    assert [p["title"] for _, p in trail[:2]] == [
        "Harbor District residents on living next to the battery park",
        "Lumen City switches on its first neighbourhood solar microgrid",
    ]
    assert (
        trail[-1][1]["count"] == 3 and trail[-1][1]["seen"] == 3 and trail[-1][1]["error"] is None
    )
    assert source.last_polled_at == T0 and source.next_poll_at == T0 + timedelta(hours=1)

    clock.now = T0 + timedelta(hours=1)
    again = await poller(clock).poll(db_session, source)
    assert again.new_item_ids == [] and again.seen == 3
    assert (await events(db_session, source.id))[-1][1]["count"] == 0
    assert (
        await db_session.scalar(
            select(func.count()).select_from(SourceItem).where(SourceItem.source_id == source.id)
        )
        == 3
    )


async def test_url_list_and_search_query_sources(db_session):
    company = await unique_company(db_session, "kinds")
    listed = await add_source(
        db_session,
        company_id=company.id,
        name="watch",
        kind="url_list",
        config={"urls": ["https://x.test/a", "https://x.test/a/", "https://x.test/b"]},
        now=T0,
    )
    outcome = await poller().poll(db_session, listed)
    assert len(outcome.new_item_ids) == 2  # /a and /a/ are one page

    searched = await add_source(
        db_session,
        company_id=company.id,
        name="search",
        kind="search_query",
        config={"query": "Lumen City microgrid", "k": 3},
        now=T0,
    )
    outcome = await poller().poll(db_session, searched)
    assert len(outcome.new_item_ids) == 3
    assert (await events(db_session, searched.id))[-1][1]["cost_usd"] is None  # fixtures are free

    class Paid:
        name = "paid"

        async def search(self, query, *, k, recency_days=None):
            return SearchResponse(
                results=[SearchResult(url="https://x.test/c", title="C", snippet="")],
                provider="paid",
                cost_usd=Decimal("0.008"),
            )

    paid = await add_source(
        db_session,
        company_id=company.id,
        name="paid",
        kind="search_query",
        config={"query": "q"},
        now=T0,
    )
    await poller(search=Paid()).poll(db_session, paid)
    assert Decimal(str((await events(db_session, paid.id))[-1][1]["cost_usd"])) == Decimal("0.008")


async def test_max_items_keeps_the_newest(db_session):
    company = await unique_company(db_session, "cap")
    source = await add_source(
        db_session, company_id=company.id, name="news", kind="rss", url=FEED, now=T0
    )
    outcome = await poller(max_items=1).poll(db_session, source)
    [item] = (
        await db_session.scalars(select(SourceItem).where(SourceItem.id.in_(outcome.new_item_ids)))
    ).all()
    assert item.title == "Harbor District residents on living next to the battery park"


async def test_failures_are_recorded_then_the_source_pauses(db_session):
    company = await unique_company(db_session, "fail")
    source = await add_source(
        db_session,
        company_id=company.id,
        name="down",
        kind="rss",
        url="https://down.fixtures.autora.test/feed.xml",
        now=T0,
    )
    clock = Clock(T0)
    p = poller(clock, pause_after=3)
    for _ in range(3):
        outcome = await p.poll(db_session, source)
        clock.now += timedelta(hours=1)
    assert outcome.paused and source.status == SourceStatus.PAUSED.value
    assert source.consecutive_failures == 3 and "404" in source.last_error
    trail = [t for t, _ in await events(db_session, source.id)]
    assert trail == ["SOURCE_POLLED"] * 3 + ["SOURCE_PAUSED"]
    polled = (await events(db_session, source.id))[0][1]
    assert polled["count"] == 0 and polled["error"].startswith("FetchRefused")

    # a success resets the count
    other = await add_source(
        db_session,
        company_id=company.id,
        name="flaky",
        kind="search_query",
        config={"query": "q"},
        now=T0,
    )

    class Flaky:
        name = "flaky"
        fail = True

        async def search(self, query, *, k, recency_days=None):
            if self.fail:
                raise SearchUnavailable("Tavily 503")
            return SearchResponse(results=[], provider="flaky")

    flaky = Flaky()
    await poller(search=flaky).poll(db_session, other)
    assert other.consecutive_failures == 1
    flaky.fail = False
    await poller(search=flaky).poll(db_session, other)
    assert other.consecutive_failures == 0 and other.last_error is None


async def test_poll_due_takes_only_due_active_sources(db_session):
    company = await unique_company(db_session, "due")
    due = await add_source(
        db_session,
        company_id=company.id,
        name="due",
        kind="rss",
        url=FEED,
        now=T0 - timedelta(minutes=1),
    )
    later = await add_source(
        db_session,
        company_id=company.id,
        name="later",
        kind="rss",
        url=ATOM,
        now=T0 + timedelta(minutes=1),
    )
    paused = await add_source(
        db_session,
        company_id=company.id,
        name="paused",
        kind="rss",
        url=ATOM,
        now=T0 - timedelta(minutes=1),
    )
    paused.status = SourceStatus.PAUSED.value
    await db_session.flush()
    outcomes = await poller(Clock(T0)).poll_due(db_session, company.id)
    assert [o.source_id for o in outcomes] == [due.id]
    assert later.last_polled_at is None and paused.last_polled_at is None


async def test_the_scheduler_polls_sources(committed):
    """End to end: the worker's scheduler fires newsroom.poll_sources and items appear."""
    async with committed() as session:
        company = await unique_company(session, "sched")
        source = await add_source(
            session,
            company_id=company.id,
            name="news",
            kind="rss",
            url=FEED,
            now=T0 - timedelta(minutes=10),
        )
        await session.commit()
    scheduler = build_scheduler(None, committed, f"test-{uuid.uuid4().hex[:6]}")
    scheduler.clock = lambda: T0 + timedelta(days=3650)  # everything is due
    try:
        fired = await scheduler.tick()
        assert POLL_SCHEDULE in fired
        async with committed() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(SourceItem)
                .where(SourceItem.source_id == source.id)
            )
            assert count == 3
    finally:
        async with committed() as session:
            await session.execute(
                update(Schedule).where(Schedule.company_id == company.id).values(enabled=False)
            )
            await session.execute(
                update(Source).where(Source.company_id == company.id).values(status="paused")
            )
            await session.commit()


# --- the page fetcher -----------------------------------------------------------------------


def http(handler, addresses: dict[str, list[str]] | None = None, **kw) -> HttpFetcher:
    addresses = addresses or {}

    async def resolve(host):
        return addresses.get(host, ["93.184.216.34"])

    return HttpFetcher(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), resolver=resolve, **kw
    )


async def test_http_fetch_and_redirects():
    def handler(request):
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "/new"})
        return httpx.Response(
            200, content=b"<rss/>", headers={"content-type": "application/rss+xml"}
        )

    page = await http(handler).fetch("https://site.test/old")
    assert (page.url, page.status, page.body) == ("https://site.test/new", 200, b"<rss/>")


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8000/api/companies",
        "http://127.0.0.1/",
        "http://10.0.0.5/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "file:///etc/passwd",
        "ftp://site.test/x",
    ],
)
async def test_http_refuses_private_addresses_and_other_schemes(url):
    reached = []

    def handler(request):
        reached.append(request.url)
        return httpx.Response(200)

    with pytest.raises(FetchRefused):
        await http(handler, {"localhost": ["127.0.0.1"]}).fetch(url)
    assert reached == []


async def test_http_refuses_a_redirect_into_the_private_network():
    def handler(request):
        return httpx.Response(302, headers={"location": "http://internal.test/admin"})

    with pytest.raises(FetchRefused, match="internal.test is not a public address"):
        await http(handler, {"internal.test": ["192.168.1.10"]}).fetch("https://site.test/")


async def test_http_limits_and_statuses():
    big = await _raises(lambda r: httpx.Response(200, content=b"x" * 2000), max_bytes=1000)
    assert isinstance(big, FetchRefused) and "larger than 1000" in str(big)
    assert isinstance(await _raises(lambda r: httpx.Response(404)), FetchRefused)
    assert isinstance(await _raises(lambda r: httpx.Response(503)), FetchUnavailable)
    assert isinstance(await _raises(lambda r: httpx.Response(429)), FetchUnavailable)
    loop = await _raises(
        lambda r: httpx.Response(302, headers={"location": "/again"}), max_redirects=2
    )
    assert "more than 2 redirects" in str(loop)


async def _raises(handler, **kw) -> Exception:
    try:
        await http(handler, **kw).fetch("https://site.test/x")
    except (FetchRefused, FetchUnavailable) as exc:
        return exc
    raise AssertionError("expected a fetch error")


async def test_fixture_fetcher():
    fetcher = fixture_fetcher()
    page = await fetcher.fetch(FEED)
    assert page.content_type == "application/rss+xml" and page.body.startswith(b"<?xml")
    with pytest.raises(FetchRefused, match="404"):
        await fetcher.fetch("https://news.fixtures.autora.test/nothing")
    escaping = FixtureFetcher(FIXTURES, {"https://x.test/": "../__init__.py"})
    with pytest.raises(FetchRefused, match="outside"):
        await escaping.fetch("https://x.test/")
    assert isinstance(build_page_fetcher(None), FixtureFetcher)


def test_fetched_page_is_plain_data():
    page = FetchedPage(url="u", status=200, content_type="t", body=b"")
    assert page.status == 200


async def test_the_fetcher_says_who_is_fetching():
    """T-600: the product's name by default, the caller's when it has one to give.

    It used to be the newsroom's, hard-coded in a core module — the fetcher is shared, and a
    company that publishes software fetches pages too (ARCHITECTURE_V2_1 §9).
    """
    seen: list[str | None] = []

    def handler(request):
        seen.append(request.headers.get("user-agent"))
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<p>ok</p>")

    await http(handler).fetch("https://example.com/a")
    assert seen[-1] == USER_AGENT
    assert seen[-1].startswith("Autora/") and "newsroom" not in seen[-1].lower()

    await http(handler, user_agent=newsroom_settings.USER_AGENT).fetch("https://example.com/a")
    assert seen[-1] == newsroom_settings.USER_AGENT


# --- options for feeds that are not news (D-036) ----------------------------------------------

EDGAR = b"""<?xml version="1.0" encoding="ISO-8859-1" ?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>BERKSHIRE HATHAWAY INC</title>
  <entry>
    <title>13F-HR - Quarterly report filed by institutional managers, Holdings</title>
    <link rel="alternate" type="text/html" href="https://www.sec.gov/Archives/2026-08-index.htm"/>
    <id>urn:tag:sec.gov,2008:accession-number=0001</id>
    <updated>2026-08-14T16:05:04-04:00</updated>
  </entry>
  <entry>
    <title>13F-HR - Quarterly report filed by institutional managers, Holdings</title>
    <link rel="alternate" type="text/html" href="https://www.sec.gov/Archives/2024-02-index.htm"/>
    <id>urn:tag:sec.gov,2008:accession-number=0002</id>
    <updated>2024-02-14T16:05:04-04:00</updated>
  </entry>
</feed>"""


class OneFeed:
    async def fetch(self, url):
        return FetchedPage(url=url, status=200, content_type="application/atom+xml", body=EDGAR)


async def test_a_prefix_says_whose_filing_and_old_history_is_left_out(db_session):
    company = await unique_company(db_session, "edgar")
    source = await add_source(
        db_session,
        company_id=company.id,
        name="SEC 13F：巴菲特",
        kind="rss",
        url="https://www.sec.gov/cgi-bin/browse-edgar?CIK=1067983",
        config={"title_prefix": "巴菲特（Berkshire Hathaway）", "max_age_days": 120},
    )
    outcome = await poller(Clock(datetime(2026, 9, 24, tzinfo=UTC)), fetcher=OneFeed()).poll(
        db_session, source
    )
    [item] = (
        await db_session.scalars(select(SourceItem).where(SourceItem.source_id == source.id))
    ).all()
    assert outcome.seen == 2 and len(outcome.new_item_ids) == 1, (
        "2024's filing is history, not news"
    )
    assert (
        item.title
        == "巴菲特（Berkshire Hathaway） 13F-HR - Quarterly report filed by institutional "
        "managers, Holdings"
    )
    assert item.content_hash == content_hash(item.url, item.title)


@pytest.mark.parametrize("age", [0, -1, "30", 1.5, True])
async def test_max_age_is_whole_days(db_session, age):
    company = await unique_company(db_session, "age")
    with pytest.raises(SourceConfigError):
        await add_source(
            db_session,
            company_id=company.id,
            name="x",
            kind="rss",
            url="https://x",
            config={"max_age_days": age},
        )


def test_the_user_agent_sec_accepts_names_a_contact():
    assert (
        newsroom_settings.user_agent("service@nanguado.com")
        == "Autora Newsroom service@nanguado.com"
    )
    assert newsroom_settings.user_agent(None) == newsroom_settings.USER_AGENT
    assert "devince1105" in newsroom_settings.USER_AGENT
