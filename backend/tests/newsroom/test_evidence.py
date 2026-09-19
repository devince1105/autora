"""T-502: text extraction, fetch_url (page -> Evidence + snapshot) and read_evidence.

The tools run through a real ToolRegistry (their own transactions, like in a worker), against
the fixture pages. Evidence rows are per company, so each test uses its own company.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

import autora.domains.newsroom as newsroom
from autora.db.models import Company, EventRecord
from autora.domains.newsroom.extract import decode, extract
from autora.domains.newsroom.models import Evidence, Source
from autora.domains.newsroom.sources import SourcePoller
from autora.domains.newsroom.tools import evidence as evidence_tools
from autora.infra.blobstore import LocalFSBlobStore
from autora.infra.http import FixtureFetcher
from autora.infra.search.fixture import FixtureSearchProvider
from autora.runtime.actor import Actor
from autora.runtime.tools import ToolRegistry
from tests.conftest import running_agent_run, unique_company

FIXTURES = Path(newsroom.__file__).parent / "fixtures"
PAGES = FIXTURES / "pages"
PILOT = "https://news.fixtures.autora.test/lumen-city-microgrid-pilot"
PRESS = "https://city.fixtures.autora.test/press/2026-09-14-microgrid"
DAY1 = datetime(2026, 9, 19, 9, 0, tzinfo=UTC)


# --- extraction -----------------------------------------------------------------------------


def test_article_text_without_the_page_around_it():
    page = extract(
        (PAGES / "lumen-city-microgrid-pilot.html").read_bytes(), "text/html; charset=utf-8"
    )
    assert (
        page.title
        == "Lumen City switches on its first neighbourhood solar microgrid | Lumen City News"
    )
    assert page.language == "en"
    for noise in ("window.analytics", "font-family", "Energy", "Most read", "All rights reserved"):
        assert noise not in page.text
    # source line breaks are layout: one paragraph per <p>, blank lines between
    assert (
        "The pilot in Harbor District links 1,200 rooftop solar panels with a 4 MWh battery."
        in page.text
    )
    paragraphs = page.text.split("\n\n")
    assert paragraphs[0] == "Lumen City switches on its first neighbourhood solar microgrid"
    assert (
        paragraphs[-1]
        == "The project cost NT$420 million to build, according to the city's budget office."
    )
    assert "\n" not in paragraphs[2]


def test_chinese_page_and_title_fallbacks():
    page = extract((PAGES / "city-press-microgrid.html").read_bytes(), "text/html")
    assert page.language == "zh-TW"
    assert "本計畫總經費新台幣 4.2 億元，由市府能源處執行。" in page.text
    assert "首頁" not in page.text and "版權所有" not in page.text

    og = extract(
        b'<html><head><meta property="og:title" content="OG"></head><body><p>x</p></body></html>',
        "text/html",
    )
    assert og.title == "OG"
    h1 = extract(b"<html><body><h1>Only a heading</h1><p>text</p></body></html>", "text/html")
    assert h1.title == "Only a heading"


def test_without_article_or_main_the_body_is_used():
    page = extract((PAGES / "harbor-residents.html").read_bytes(), "text/html")
    assert page.text.startswith("Harbor District residents on living next to the battery park")
    assert "Harbor Blog" not in page.text  # header and footer still dropped


def test_charsets_and_plain_text():
    big5 = "<html><head><meta charset='big5'></head><body><p>微電網</p></body></html>".encode(
        "big5"
    )
    assert extract(big5, "text/html").text == "微電網"
    assert decode("微電網".encode("big5"), "text/html; charset=big5") == "微電網"
    assert decode(b"caf\xc3\xa9", "text/html; charset=no-such-charset") == "café"
    plain = extract(b"First line.\n\n  Second   line.\n", "text/plain")
    assert plain.text == "First line.\n\nSecond line." and plain.title is None


# --- fetch_url / read_evidence --------------------------------------------------------------


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
async def kit(committed, tmp_path):
    async with committed() as session:
        run = await running_agent_run(session, "evidence")  # a real researcher run and task
        company = await session.get(Company, run.company_id)
        await session.commit()
    blobs = LocalFSBlobStore(tmp_path / "blobs")
    fetcher = FixtureFetcher(FIXTURES, json.loads((FIXTURES / "routes.json").read_text()))
    clock = Clock(DAY1)
    registry = ToolRegistry(committed)
    registry.tool("fetch_url", description="", side_effect="write", retryable=True)(
        evidence_tools.fetch_url_tool(fetcher, blobs, clock)
    )
    registry.tool("read_evidence", description="", side_effect="read")(evidence_tools.read_evidence)
    run_id, task_id, agent_id = run.id, run.task_id, run.agent_id

    async def call(tool, args, company_id=None):
        return await registry.invoke(
            tool,
            args,
            company_id=company_id or company.id,
            actor=Actor.system("test"),
            tool_call_id=f"call_{uuid.uuid4().hex[:8]}",
            run_id=run_id,
            task_id=task_id,
            agent_id=agent_id,
            step_seq=int(clock.now.timestamp()),
        )

    return {
        "company": company,
        "blobs": blobs,
        "clock": clock,
        "call": call,
        "committed": committed,
    }


async def _captured(committed, company_id):
    async with committed() as session:
        return (
            await session.scalars(
                select(EventRecord)
                .where(
                    EventRecord.company_id == company_id,
                    EventRecord.event_type == "EVIDENCE_CAPTURED",
                )
                .order_by(EventRecord.seq)
            )
        ).all()


async def test_fetch_url_captures_evidence_with_its_snapshot(kit):
    committed, company = kit["committed"], kit["company"]
    # a source that listed the page (added directly: add_source would start a real schedule)
    async with committed() as session:
        source = Source(
            company_id=company.id,
            name="news",
            kind="rss",
            url="https://news.fixtures.autora.test/feed.xml",
            status="paused",
        )
        session.add(source)
        await session.flush()
        fetcher = FixtureFetcher(FIXTURES, json.loads((FIXTURES / "routes.json").read_text()))
        await SourcePoller(fetcher=fetcher, search=FixtureSearchProvider([])).poll(session, source)
        await session.commit()

    result = await kit["call"]("fetch_url", {"url": PILOT + "?utm_source=newsletter"})
    assert result.ok, result.message
    out = result.output
    assert out["url"] == PILOT and out["reused"] is False
    assert out["excerpt"].startswith(
        "Lumen City switches on its first neighbourhood solar microgrid"
    )
    assert "Quote evidence exactly" in out["note"]
    evidence_id = uuid.UUID(out["evidence_id"])
    assert [(p.type, p.id) for p in result.produced] == [("evidence", evidence_id)]

    async with committed() as session:
        evidence = await session.get(Evidence, evidence_id)
    assert evidence.source_id == source.id  # the feed listed this URL: its trust level applies
    assert evidence.retrieved_on == DAY1.date() and evidence.language == "en"
    assert evidence.text_hash and len(evidence.extracted_text) == out["chars"]
    assert (
        await kit["blobs"].get(evidence.blob_key)
        == (PAGES / "lumen-city-microgrid-pilot.html").read_bytes()
    )
    assert evidence.blob_key.startswith(f"evidence/{company.id}/2026/09/19/")

    [captured] = await _captured(committed, company.id)
    assert captured.payload["evidence_id"] == str(evidence_id)
    assert captured.payload["source_id"] == str(source.id)
    assert captured.run_id == evidence.run_id and captured.agent_id is not None
    assert evidence.task_id is not None


async def test_the_same_page_the_same_day_is_reused(kit):
    first = await kit["call"]("fetch_url", {"url": PRESS})
    again = await kit["call"]("fetch_url", {"url": PRESS + "#top"})
    assert again.ok and again.output["reused"] is True
    assert again.output["evidence_id"] == first.output["evidence_id"]
    assert [p.id for p in again.produced] == [uuid.UUID(first.output["evidence_id"])]  # still used
    assert len(await _captured(kit["committed"], kit["company"].id)) == 1

    kit["clock"].now = DAY1 + timedelta(days=1)
    next_day = await kit["call"]("fetch_url", {"url": PRESS})
    assert next_day.output["reused"] is False
    assert next_day.output["evidence_id"] != first.output["evidence_id"]
    async with kit["committed"]() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(Evidence)
            .where(Evidence.company_id == kit["company"].id)
        )
    assert count == 2


@pytest.mark.parametrize(
    ("url", "error_class", "message"),
    [
        (
            "https://city.fixtures.autora.test/reports/microgrid.pdf",
            "EvidenceError",
            "application/pdf content cannot be evidence",
        ),
        ("https://app.fixtures.autora.test/dashboard", "EvidenceError", "no readable text"),
        ("https://news.fixtures.autora.test/missing", "FetchRefused", "404"),
    ],
)
async def test_what_cannot_become_evidence_fails_without_retry(kit, url, error_class, message):
    result = await kit["call"]("fetch_url", {"url": url})
    assert not result.ok and result.error_class == error_class and not result.will_retry
    assert message in result.message
    assert await _captured(kit["committed"], kit["company"].id) == []


async def test_long_pages_are_cut_and_marked(kit, monkeypatch):
    monkeypatch.setattr(evidence_tools, "TEXT_LIMIT", 100)
    result = await kit["call"]("fetch_url", {"url": PILOT, "render": True})
    assert result.output["chars"] == 100 and result.output["truncated"] is True
    assert "without running JavaScript" in result.output["render"]


async def test_read_evidence_in_slices_and_only_your_own(kit):
    fetched = await kit["call"]("fetch_url", {"url": PILOT})
    evidence_id = fetched.output["evidence_id"]
    total = fetched.output["chars"]

    first = await kit["call"]("read_evidence", {"evidence_id": evidence_id, "limit": 200})
    assert first.ok and len(first.output["text"]) == 200 and first.output["next_offset"] == 200
    assert first.output["total_chars"] == total
    rest = await kit["call"](
        "read_evidence", {"evidence_id": evidence_id, "offset": 200, "limit": 8000}
    )
    assert first.output["text"] + rest.output["text"] == fetched.output["excerpt"][:total]
    assert rest.output["next_offset"] is None

    async with kit["committed"]() as session:
        other = await unique_company(session, "other")
        await session.commit()
    stranger = await kit["call"]("read_evidence", {"evidence_id": evidence_id}, company_id=other.id)
    assert not stranger.ok and "no evidence" in stranger.message
    missing = await kit["call"]("read_evidence", {"evidence_id": str(uuid.uuid4())})
    assert not missing.ok


def test_worker_registers_the_evidence_tools(committed, tmp_path):
    from autora.app import build_tools

    names = build_tools(committed, blobs=LocalFSBlobStore(tmp_path)).names()
    assert {"web_search", "fetch_url", "read_evidence"} <= set(names)
