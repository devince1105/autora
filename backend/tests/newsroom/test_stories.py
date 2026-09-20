"""T-504: stories — clustering and deduplication, scoring, the story lifecycle.

A scripted embedder gives exact similarities (topic vectors), so each join-or-new decision is
deterministic; the end-to-end scheduler test uses the fake hashing embedder instead.
"""

import json
import math
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select, update

import autora.domains.newsroom as newsroom
from autora.app import build_scheduler
from autora.db.models import EventRecord, Project, Schedule, StateTransition
from autora.domains.newsroom.models import (
    EMBED_DIM,
    Source,
    SourceItem,
    Story,
    StoryItem,
    StoryState,
)
from autora.domains.newsroom.sources import SourcePoller, add_source
from autora.domains.newsroom.stories import CLUSTER_SCHEDULE, STORY_FSM, StoryDesk, StoryError
from autora.infra.http import FixtureFetcher
from autora.infra.search.fixture import FixtureSearchProvider
from autora.runtime.actor import Actor
from autora.runtime.fsm import IllegalTransition
from autora.runtime.models.embeddings import (
    Embedder,
    EmbeddingOutput,
    EmbeddingUnavailable,
)
from tests.conftest import unique_company

FIXTURES = Path(newsroom.__file__).parent / "fixtures"
NEWS_FEED = "https://news.fixtures.autora.test/feed.xml"
CITY_FEED = "https://city.fixtures.autora.test/press/feed.atom"
PILOT = "https://news.fixtures.autora.test/lumen-city-microgrid-pilot"
T0 = datetime(2026, 9, 18, 0, 0, tzinfo=UTC)
HUMAN = Actor.human("editor@example.test")


def basis(*weights: tuple[int, float]) -> list[float]:
    v = [0.0] * EMBED_DIM
    for index, weight in weights:
        v[index] = weight
    norm = math.sqrt(sum(x * x for x in v))
    return [x / norm for x in v]


MICROGRID = basis((0, 1.0))
COFFEE = basis((1, 1.0))
OTHER = basis((2, 1.0))
RESIDENTS = basis((0, 0.6), (3, 0.8))  # cosine 0.6 with MICROGRID: below the 0.65 threshold


class Topics:
    """Vectors by what the text is about."""

    name = "topics"

    def __init__(self, fail: bool = False):
        self.fail = fail

    async def embed(self, texts, *, model_id, purpose):
        if self.fail:
            raise EmbeddingUnavailable("down")
        vectors = []
        for text in texts:
            lowered = text.lower()
            if "coffee" in lowered:
                vectors.append(COFFEE)
            elif "residents" in lowered:
                vectors.append(RESIDENTS)
            elif "microgrid" in lowered or "微電網" in text:
                vectors.append(MICROGRID)
            else:
                vectors.append(OTHER)
        return EmbeddingOutput(vectors=vectors, tokens=len(texts))


def desk(clock=None, fail=False) -> StoryDesk:
    return StoryDesk(
        Embedder(provider=Topics(fail), model_id="topics-v1", dim=EMBED_DIM),
        clock=clock or (lambda: T0 + timedelta(days=1)),
    )


def fetcher() -> FixtureFetcher:
    return FixtureFetcher(FIXTURES, json.loads((FIXTURES / "routes.json").read_text()))


async def feed_source(session, company_id, name, url, trust="0.5"):
    source = Source(
        company_id=company_id,
        name=name,
        kind="rss",
        url=url,
        trust_level=Decimal(trust),
        status="paused",
    )
    session.add(source)
    await session.flush()
    await SourcePoller(fetcher=fetcher(), search=FixtureSearchProvider([]), clock=lambda: T0).poll(
        session, source
    )
    return source


async def stories_of(session, company_id):
    return (
        await session.scalars(
            select(Story)
            .where(Story.company_id == company_id)
            .order_by(Story.first_seen_at, Story.title)
        )
    ).all()


async def items_of(session, story_id):
    return (
        await session.scalars(
            select(SourceItem)
            .join(StoryItem, StoryItem.source_item_id == SourceItem.id)
            .where(StoryItem.story_id == story_id)
        )
    ).all()


# --- clustering -----------------------------------------------------------------------------


async def test_items_cluster_into_stories(db_session):
    company = await unique_company(db_session, "stories")
    news = await feed_source(db_session, company.id, "news", NEWS_FEED, trust="0.6")
    city = await feed_source(db_session, company.id, "city", CITY_FEED, trust="0.9")

    outcome = await desk().cluster_pending(db_session, company.id)
    assert len(outcome.joined) == 5 and len(outcome.new_story_ids) == 3

    by_title = {s.title: s for s in await stories_of(db_session, company.id)}
    coffee = by_title["Coffee bean prices hit a three-year high"]
    residents = by_title["Harbor District residents on living next to the battery park"]
    [microgrid] = [s for s in by_title.values() if s not in (coffee, residents)]
    # the city's Chinese press release was the earliest microgrid item: it started the story
    assert microgrid.title.startswith("流明市港區社區微電網啟用")
    assert (microgrid.items_count, microgrid.sources_count) == (3, 2)
    assert {i.source_id for i in await items_of(db_session, microgrid.id)} == {news.id, city.id}
    assert (residents.items_count, coffee.items_count) == (1, 1)
    assert all(
        s.state == StoryState.DISCOVERED and s.embedding_model == "topics-v1"
        for s in by_title.values()
    )

    links = (
        await db_session.scalars(select(StoryItem).where(StoryItem.story_id == microgrid.id))
    ).all()
    assert sorted(link.similarity is None for link in links) == [False, False, True]  # the starter

    discovered = (
        await db_session.scalars(
            select(EventRecord).where(
                EventRecord.company_id == company.id, EventRecord.event_type == "STORY_DISCOVERED"
            )
        )
    ).all()
    assert {e.payload["story_id"] for e in discovered} == {str(s.id) for s in by_title.values()}
    assert all(e.aggregate_type == "story" for e in discovered)

    assert (await desk().cluster_pending(db_session, company.id)).joined == {}  # nothing pending


async def test_the_same_url_from_another_source_joins_its_story(db_session):
    company = await unique_company(db_session, "sameurl")
    await feed_source(db_session, company.id, "news", NEWS_FEED)
    await desk().cluster_pending(db_session, company.id)
    watcher = Source(
        company_id=company.id,
        name="watch",
        kind="url_list",
        config={"urls": [PILOT + "?utm_source=x"]},
        status="paused",
    )
    db_session.add(watcher)
    await db_session.flush()
    await SourcePoller(fetcher=fetcher(), search=FixtureSearchProvider([])).poll(
        db_session, watcher
    )

    outcome = await desk().cluster_pending(db_session, company.id)  # its title is the URL: OTHER
    assert outcome.new_story_ids == []
    [(item_id, story_id)] = outcome.joined.items()
    story = await db_session.get(Story, story_id)
    assert story.title.startswith("Lumen City switches on") and story.sources_count == 2
    link = await db_session.scalar(select(StoryItem).where(StoryItem.source_item_id == item_id))
    assert link.similarity == 1.0


async def test_known_stories_absorb_new_items_whatever_their_state(db_session):
    """Ignored stays ignored; written stays written: no second story about the same news."""
    company = await unique_company(db_session, "known")
    await feed_source(db_session, company.id, "news", NEWS_FEED)
    await desk().cluster_pending(db_session, company.id)
    coffee = next(s for s in await stories_of(db_session, company.id) if "Coffee" in s.title)
    await desk().ignore(db_session, coffee, actor=HUMAN, reason="off topic")

    more = SourceItem(
        company_id=company.id,
        source_id=(await items_of(db_session, coffee.id))[0].source_id,
        external_id="x",
        url="https://news.fixtures.autora.test/coffee-2",
        title="Coffee prices keep rising",
        content_hash="h2",
    )
    db_session.add(more)
    await db_session.flush()
    outcome = await desk().cluster_pending(db_session, company.id)
    assert outcome.new_story_ids == [] and outcome.joined == {more.id: coffee.id}
    assert coffee.state == StoryState.IGNORED and coffee.items_count == 2


async def test_old_stories_are_not_matched(db_session):
    company = await unique_company(db_session, "old")
    await feed_source(db_session, company.id, "news", NEWS_FEED)
    await desk().cluster_pending(db_session, company.id)
    await db_session.execute(
        update(Story)
        .where(Story.company_id == company.id)
        .values(last_item_at=T0 - timedelta(days=45))
    )
    fresh = SourceItem(
        company_id=company.id,
        source_id=(
            await db_session.scalar(
                select(SourceItem.source_id).where(SourceItem.company_id == company.id)
            )
        ),
        external_id="y",
        url="https://news.fixtures.autora.test/coffee-3",
        title="Coffee again",
        content_hash="h3",
    )
    db_session.add(fresh)
    await db_session.flush()
    assert len((await desk().cluster_pending(db_session, company.id)).new_story_ids) == 1


async def test_score(db_session):
    company = await unique_company(db_session, "score")
    await feed_source(db_session, company.id, "news", NEWS_FEED, trust="0.5")
    now = datetime(2026, 9, 17, 12, tzinfo=UTC)  # the residents post was published at this moment
    await StoryDesk(
        Embedder(provider=Topics(), model_id="topics-v1", dim=EMBED_DIM), clock=lambda: now
    ).cluster_pending(db_session, company.id)
    residents = next(s for s in await stories_of(db_session, company.id) if "residents" in s.title)
    # one source (1/3), just published (freshness 1), trust 0.5
    assert residents.score == Decimal("0.567")
    coffee = next(s for s in await stories_of(db_session, company.id) if "Coffee" in s.title)
    assert coffee.score == Decimal("0.267")  # a week old: no freshness


async def test_a_failed_embedding_writes_nothing(db_session):
    company = await unique_company(db_session, "embedfail")
    await feed_source(db_session, company.id, "news", NEWS_FEED)
    with pytest.raises(EmbeddingUnavailable):
        await desk(fail=True).cluster_pending(db_session, company.id)
    assert await stories_of(db_session, company.id) == []


# --- lifecycle ------------------------------------------------------------------------------


async def test_select_ignore_drop_and_illegal_moves(db_session):
    company = await unique_company(db_session, "life")
    project = Project(company_id=company.id, name="newsroom")
    other_company = await unique_company(db_session, "other")
    foreign = Project(company_id=other_company.id, name="theirs")
    db_session.add_all([project, foreign])
    await db_session.flush()
    stories = []
    for title in ("One", "Two", "Three"):
        stories.append(
            await desk().create(
                db_session, company_id=company.id, title=title, seed={"query": title}, actor=HUMAN
            )
        )
    one, two, three = stories
    assert one.seed == {"query": "One"} and one.state == StoryState.DISCOVERED

    with pytest.raises(StoryError, match="not this company's"):
        await desk().select(db_session, one, project_id=foreign.id, actor=HUMAN)
    await desk().select(db_session, one, project_id=project.id, actor=HUMAN, reason="good lead")
    assert one.state == StoryState.SELECTED and one.project_id == project.id
    await desk().ignore(db_session, two, actor=HUMAN)
    await desk().drop(db_session, three, actor=HUMAN, reason="no sources")
    with pytest.raises(IllegalTransition):
        await desk().select(db_session, two, project_id=project.id, actor=HUMAN)

    events = [
        (e.event_type, e.payload.get("reason"))
        for e in (
            await db_session.scalars(
                select(EventRecord)
                .where(EventRecord.company_id == company.id)
                .order_by(EventRecord.seq)
            )
        ).all()
    ]
    assert events == [
        ("STORY_DISCOVERED", None),
        ("STORY_DISCOVERED", None),
        ("STORY_DISCOVERED", None),
        ("STORY_SELECTED", None),
        ("STORY_DROPPED", "no sources"),
    ]
    audit = (
        await db_session.scalars(
            select(StateTransition)
            .where(StateTransition.company_id == company.id)
            .order_by(StateTransition.id)
        )
    ).all()
    assert [(a.from_state, a.to_state) for a in audit] == [
        ("DISCOVERED", "SELECTED"),
        ("DISCOVERED", "IGNORED"),
        ("DISCOVERED", "DROPPED"),
    ]
    assert STORY_FSM.terminal_states() == {
        StoryState.PUBLISHED,
        StoryState.DROPPED,
        StoryState.IGNORED,
    }


def test_the_threshold_is_the_newsroom_s_own_setting():
    """§9: the core parses the environment; what a similarity of 0.65 means is the newsroom's."""
    from autora.domains.newsroom.settings import NewsroomSettings

    assert NewsroomSettings(_env_file=None).story_match_threshold == 0.65
    # and it is read from a key that says whose knob it is
    assert NewsroomSettings(_env_file=None, story_match_threshold=0.8).story_match_threshold == 0.8
    with pytest.raises(ValidationError):
        NewsroomSettings(_env_file=None, story_match_threshold=1.5)


# --- through the worker's scheduler ---------------------------------------------------------


async def test_poll_then_cluster_through_the_scheduler(committed):
    async with committed() as session:
        company = await unique_company(session, "sched-stories")
        await add_source(
            session,
            company_id=company.id,
            name="news",
            kind="rss",
            url=NEWS_FEED,
            now=T0 - timedelta(minutes=10),
        )
        await session.commit()
    scheduler = build_scheduler(None, committed, f"test-{uuid.uuid4().hex[:6]}")
    clock = [T0]
    scheduler.clock = lambda: clock[0]
    try:
        # at T0 both are due and clustering's slot (T0-8 min) comes first, then the poll (T0-5
        # min); the next clustering slot, T0+2 min, finds the polled items (in production: a
        # poll at :00, clustering at :02)
        fired = await scheduler.tick()
        clock[0] = T0 + timedelta(minutes=3)
        fired += await scheduler.tick()
        assert {"newsroom.poll_sources", CLUSTER_SCHEDULE} <= set(fired)
        async with committed() as session:
            clustered = await session.scalar(
                select(func.count())
                .select_from(StoryItem)
                .where(StoryItem.company_id == company.id)
            )
            stories = await session.scalar(
                select(func.count()).select_from(Story).where(Story.company_id == company.id)
            )
        assert clustered == 3 and stories >= 1
    finally:
        async with committed() as session:
            await session.execute(
                update(Schedule).where(Schedule.company_id == company.id).values(enabled=False)
            )
            await session.execute(
                update(Source).where(Source.company_id == company.id).values(status="paused")
            )
            await session.commit()
