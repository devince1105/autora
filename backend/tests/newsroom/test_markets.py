"""D-036: the investing newsroom — its sources, its no-advice policy, and seeding it twice."""

import uuid

from sqlalchemy import select

from autora.db.repositories.companies import get_policies
from autora.domains.newsroom import markets
from autora.domains.newsroom.advice import NO_ADVICE_KEY
from autora.domains.newsroom.models import Source
from autora.domains.newsroom.sources import SECTIONS, SourceConfigError, add_source
from autora.runtime.actor import Actor

ACTOR = Actor.human("test")


async def test_seeded_once_with_advice_off_limits_and_twice_without_duplicates(db_session):
    slug = f"markets-{uuid.uuid4().hex[:8]}"
    first = await markets.seed_markets(db_session, actor=ACTOR, slug=slug)
    assert first.company.name == markets.NAME
    assert (await get_policies(db_session, first.company.id))[NO_ADVICE_KEY] is True
    assert len(first.sources) == len(markets.SOURCES) == len(first.added)

    again = await markets.seed_markets(db_session, actor=ACTOR, slug=slug, name="新的名字")
    assert again.added == [] and len(again.sources) == len(markets.SOURCES)
    assert again.company.id == first.company.id and again.company.name == "新的名字"

    first.company.mission = "an old mission"
    await markets.seed_markets(db_session, actor=ACTOR, slug=slug)
    assert first.company.mission == markets.MISSION, "the desk chooses by today's mission"


async def test_every_filing_source_says_whose_it_is_and_stands_alone(db_session):
    newsroom = await markets.seed_markets(
        db_session, actor=ACTOR, slug=f"markets-{uuid.uuid4().hex[:8]}"
    )
    filings = (
        await db_session.scalars(
            select(Source).where(
                Source.company_id == newsroom.company.id, Source.url.contains("13F-HR")
            )
        )
    ).all()
    assert len(filings) == 6  # Buffett, Ackman, Burry, Druckenmiller, Duan, Cathie Wood
    # and a public figure's own filings as an owner (D-050)
    filings += (
        await db_session.scalars(
            select(Source).where(
                Source.company_id == newsroom.company.id, Source.url.contains("CIK=0000947033")
            )
        )
    ).all()
    assert len(filings) == 7 and filings[-1].config["section"] == "figures"
    for source in filings:
        assert source.config["own_story"] is True and source.config["max_age_days"] == 120
        assert source.config["title_prefix"] and source.trust_level >= 0.9


def test_the_13f_feed_is_sec_s_own():
    url = markets.edgar_13f("0001067983")
    assert url.startswith("https://www.sec.gov/cgi-bin/browse-edgar?")
    assert "CIK=0001067983" in url and "type=13F-HR" in url and "output=atom" in url


async def test_seeding_again_brings_old_sources_up_to_date(db_session):
    slug = f"markets-{uuid.uuid4().hex[:8]}"
    newsroom = await markets.seed_markets(db_session, actor=ACTOR, slug=slug)
    buffett = next(s for s in newsroom.sources if "巴菲特" in s.name)
    buffett.config = {"title_prefix": "old"}  # a source made before a setting existed
    await db_session.flush()

    await markets.seed_markets(db_session, actor=ACTOR, slug=slug)
    await db_session.refresh(buffett)
    assert buffett.config["primary"] is True and buffett.config["title_prefix"].startswith("巴菲特")


async def test_a_filer_that_moved_is_the_same_source_with_a_new_address(db_session):
    """Ackman's holdings moved to Pershing Square Inc.: the source follows, its history stays."""
    slug = f"markets-{uuid.uuid4().hex[:8]}"
    newsroom = await markets.seed_markets(db_session, actor=ACTOR, slug=slug)
    ackman = next(s for s in newsroom.sources if "艾克曼" in s.name)
    assert "CIK=0002026053" in ackman.url and ackman.config["predecessor_ciks"] == ["1336528"]
    ackman.url = markets.edgar_13f("0001336528")  # as seeded before the move
    await db_session.flush()

    again = await markets.seed_markets(db_session, actor=ACTOR, slug=slug)
    assert again.added == [] and "CIK=0002026053" in ackman.url


def test_searches_run_twice_a_day_to_stay_inside_the_free_plan():
    """D-038: 6 searches x 2 a day x 30 days = 360 of Tavily's 1,000 free credits a month."""
    searches = [s for s in markets.SOURCES if s.kind == "search_query"]
    assert len(searches) == 6
    assert {s.poll_interval_seconds for s in searches} == {12 * 3600}


def test_every_source_says_which_section_of_the_site_it_feeds():
    """D-047: the site's sections come from the sources; one without would feed only the front."""
    assert {s.config["section"] for s in markets.SOURCES} == set(SECTIONS)


async def test_a_section_the_site_does_not_have_is_refused(db_session):
    company = (await markets.seed_markets(db_session, actor=ACTOR, slug="sect-check")).company
    try:
        await add_source(
            db_session,
            company_id=company.id,
            name="nft",
            kind="rss",
            url="https://example.test/feed",
            config={"section": "nft"},
        )
    except SourceConfigError as error:
        assert "config.section" in str(error)
    else:
        raise AssertionError("an unknown section was accepted")
