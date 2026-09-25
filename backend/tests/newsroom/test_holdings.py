"""D-049: the tracked investors' 13F positions, kept for the stock pages — from Berkshire's real
filings for 2026-06-30 and 2026-03-31 (``fixtures/sec``)."""

import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select

from autora.domains.newsroom import thirteenf
from autora.domains.newsroom.holdings import STOCKS, holders, refresh_holdings
from autora.domains.newsroom.markets import edgar_13f
from autora.domains.newsroom.models import InvestorPosition, Source
from autora.domains.newsroom.site import published_articles_mentioning
from autora.infra.http import FetchError
from tests.conftest import unique_company

SEC = Path(__file__).parent / "fixtures" / "sec"
Q2, Q1 = "0001193125-26-352200", "0001193125-26-226661"
PAGES = {
    thirteenf.submissions_url("1067983"): "CIK0001067983.json",
    thirteenf.FilingRef(cik="1067983", accession=Q2).text_url: f"{Q2}.txt",
    thirteenf.FilingRef(cik="1067983", accession=Q1).text_url: f"{Q1}.txt",
}


class Sec:
    def __init__(self, down: bool = False):
        self.asked: list[str] = []
        self.down = down

    async def __call__(self, url: str) -> bytes:
        self.asked.append(url)
        if self.down or url not in PAGES:
            raise FetchError(f"no page {url}")
        return (SEC / PAGES[url]).read_bytes()


@pytest.fixture
async def berkshire(db_session):
    company = await unique_company(db_session)
    source = Source(
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
    press = Source(
        company_id=company.id,
        name="press",
        kind="rss",
        url="https://example.test/feed.xml",
        config={"section": "ai"},
    )
    db_session.add_all([source, press])
    await db_session.flush()
    return company, source


async def test_the_latest_filing_against_the_one_before(db_session, berkshire):
    company, source = berkshire
    sec = Sec()
    assert await refresh_holdings(db_session, company.id, sec) == 1
    rows = {
        (r.cusip, r.put_call): r
        for r in (
            await db_session.scalars(
                select(InvestorPosition).where(InvestorPosition.source_id == source.id)
            )
        ).all()
    }
    apple = rows[("037833100", "")]
    assert (apple.change, int(apple.amount), apple.period.isoformat()) == (
        "unchanged",
        227917808,
        "2026-06-30",
    )
    alphabet = rows[("02079K305", "")]
    assert (alphabet.change, int(alphabet.amount), int(alphabet.previous_amount)) == (
        "increased",
        78791167,
        54249798,
    )
    constellation = rows[("21036P108", "")]
    assert (constellation.change, int(constellation.amount)) == ("sold_out", 0)
    assert int(constellation.previous_amount) == 632890
    assert apple.accession == Q2 and apple.previous_period.isoformat() == "2026-03-31"

    # nothing new at SEC: one request for the list, nothing rewritten
    sec.asked.clear()
    assert await refresh_holdings(db_session, company.id, sec) == 0
    assert sec.asked == [thirteenf.submissions_url("1067983")]


async def test_sec_down_is_not_an_error_and_keeps_what_there_was(db_session, berkshire):
    company, source = berkshire
    await refresh_holdings(db_session, company.id, Sec())
    before = await db_session.scalar(
        select(func.count())
        .select_from(InvestorPosition)
        .where(InvestorPosition.source_id == source.id)
    )
    assert await refresh_holdings(db_session, company.id, Sec(down=True)) == 0
    after = await db_session.scalar(
        select(func.count())
        .select_from(InvestorPosition)
        .where(InvestorPosition.source_id == source.id)
    )
    assert before == after > 0


async def test_who_holds_a_stock(db_session, berkshire):
    company, _ = berkshire
    await refresh_holdings(db_session, company.id, Sec())
    apple = await holders(db_session, STOCKS["AAPL"], company_id=company.id)
    assert [(h.investor, h.change, h.shares) for h in apple] == [("巴菲特", "unchanged", 227917808)]
    assert apple[0].portfolio_pct == pytest.approx(22.0, abs=0.1)
    assert apple[0].filing_url.endswith(f"{Q2}-index.htm")
    alphabet = await holders(db_session, STOCKS["GOOGL"], company_id=company.id)
    assert {h.title_of_class for h in alphabet} == {"CAP STK CL A", "CAP STK CL C"}
    # a Taiwan stock with no US listing has no 13F holders; another company sees none of these
    assert await holders(db_session, STOCKS["2454"], company_id=company.id) == []
    assert await holders(db_session, STOCKS["AAPL"], company_id=uuid.uuid4()) == []


async def test_our_articles_that_name_it(newsroom_room):
    room = newsroom_room
    await room.publish()
    async with room.committed() as session:
        by = lambda lang, *terms: published_articles_mentioning(  # noqa: E731
            session, lang, terms, company_slug=room.company.slug
        )
        assert len(await by("zh-TW", "微電網")) == 1
        assert len(await by("en", "Lumen")) == 1
        assert await by("en", "Lume") == []  # a Latin name is a whole word
        assert await by("en", "lumen") == []  # in its own case
        assert await by("en", "微電網") == []  # only the language asked for
