"""T-603: the newsroom's own numbers, and the ratios only it can define."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from autora.company.organization import add_business_unit
from autora.company.reporting import Reporting, Window
from autora.db.models import (
    BusinessUnitState,
    EventRecord,
    KpiScope,
    ModelCall,
    Project,
    ProjectState,
)
from autora.domains.newsroom.kpis import NAME, kpis
from autora.domains.newsroom.models import AnalyticsDaily, Article, ArticleState, Story
from autora.runtime.actor import Actor
from tests.conftest import unique_company

ACTOR = Actor.human("founder")
START = datetime(2026, 9, 21, 6, 0, tzinfo=UTC)
END = START + timedelta(hours=14)


async def _newsroom(session):
    company = await unique_company(session, "kpi-news")
    unit = await add_business_unit(
        session, company_id=company.id, key="ai_media", name="AI Media",
        actor=ACTOR, state=BusinessUnitState.ACTIVE,
    )  # fmt: skip
    project = Project(
        company_id=company.id, business_unit_id=unit.id, name="p",
        state=ProjectState.ACTIVE.value, kill_criteria={},
    )  # fmt: skip
    session.add(project)
    await session.flush()
    return company, unit, project


async def _sent_back(session, company, article, *, at):
    """An editor asked for a revision — the event, which is the only thing that knows when."""
    session.add(
        EventRecord(
            id=uuid.uuid4(),
            event_type="ARTICLE_REVISION_REQUESTED",
            schema_version=1,
            company_id=company.id,
            occurred_at=at,
            aggregate_type="article",
            aggregate_id=article.id,
            actor={"kind": "agent", "id": "editor"},
            payload={
                "article_id": str(article.id),
                "version_id": str(uuid.uuid4()),
                "issues_count": 1,
                "by_role": "editor",
                "revision": 1,
            },
        )  # fmt: skip
    )
    await session.flush()


async def _article(session, company, project, *, published_at, views=0, revisions=0):
    story = Story(company_id=company.id, project_id=project.id, title="t", state="PUBLISHED")
    session.add(story)
    await session.flush()
    article = Article(
        company_id=company.id, story_id=story.id, slug=f"s-{uuid.uuid4().hex[:8]}",
        title="t", state=ArticleState.PUBLISHED.value, primary_lang="zh-TW",
        published_langs=["zh-TW"], published_at=published_at, revision_count=revisions,
    )  # fmt: skip
    session.add(article)
    await session.flush()
    if views:
        session.add(
            AnalyticsDaily(
                company_id=company.id,
                article_id=article.id,
                lang="zh-TW",
                day=published_at.date(),
                views=views,
                uniques=views,
                read_complete=views // 2,
            )  # fmt: skip
        )
        await session.flush()
    return article


def _call(company, project, cost):
    return ModelCall(
        company_id=company.id, project_id=project.id, role="writer", capability="drafting",
        alias="frontier", provider="nvidia", model_id="m", status="ok",
        cost_usd=Decimal(cost), created_at=START + timedelta(hours=1),
    )  # fmt: skip


async def _measure(session, company, scope=KpiScope.COMPANY, **ids):
    reporting = Reporting(clock=lambda: END)
    reporting.register(NAME, kpis)
    return await reporting.metrics_for(
        session,
        Window(company_id=company.id, since=START, until=END, scope=scope, **ids),
    )


async def test_the_newsroom_counts_what_it_published_and_who_read_it(db_session):
    company, unit, project = await _newsroom(db_session)
    await _article(db_session, company, project, published_at=START + timedelta(hours=3), views=120)
    second = await _article(
        db_session, company, project, published_at=START + timedelta(hours=5), views=80
    )
    await _sent_back(db_session, company, second, at=START + timedelta(hours=4))
    await _sent_back(db_session, company, second, at=START + timedelta(hours=4, minutes=30))
    await _sent_back(db_session, company, second, at=START - timedelta(days=2))  # another cycle
    db_session.add(_call(company, project, "1.00"))
    await db_session.flush()

    metrics = await _measure(db_session, company)

    assert metrics["newsroom.published_articles"] == 2
    assert metrics["newsroom.views"] == 200
    assert metrics["newsroom.read_complete"] == 100
    assert metrics["newsroom.revisions_requested"] == 2


async def test_the_ratios_use_the_same_cost_the_core_measured(db_session):
    """cost_per_published_article is what AI Media's kill criteria are written against, so the
    two sides have to come from one measurement."""
    company, unit, project = await _newsroom(db_session)
    await _article(db_session, company, project, published_at=START + timedelta(hours=3), views=50)
    await _article(db_session, company, project, published_at=START + timedelta(hours=4))
    db_session.add(_call(company, project, "3.00"))
    await db_session.flush()

    metrics = await _measure(db_session, company)

    assert metrics["cost"] == "96.000000"  # the meter's $3, in TWD at 32 (D-023)
    assert metrics["newsroom.cost_per_published_article"] == "48.0000"
    assert metrics["newsroom.views_per_cost_unit"] == "0.5208"  # 50 views per NT$96


async def test_a_window_that_published_nothing_has_no_cost_per_article(db_session):
    """Zero would read as free and infinity as broken; neither is true, so the key is absent."""
    company, unit, project = await _newsroom(db_session)
    db_session.add(_call(company, project, "2.00"))
    await db_session.flush()

    metrics = await _measure(db_session, company)

    assert metrics["newsroom.published_articles"] == 0
    assert "newsroom.cost_per_published_article" not in metrics
    assert "newsroom.views_per_cost_unit" not in metrics


async def test_an_article_published_in_another_cycle_is_not_counted(db_session):
    company, unit, project = await _newsroom(db_session)
    await _article(db_session, company, project, published_at=START - timedelta(days=1), views=999)
    await db_session.flush()

    metrics = await _measure(db_session, company)

    assert metrics["newsroom.published_articles"] == 0


async def test_another_business_gets_none_of_the_newsroom_s_numbers(db_session):
    """The reason the hook is scoped: a second business must not read the newsroom's work as
    its own."""
    company, unit, project = await _newsroom(db_session)
    await _article(db_session, company, project, published_at=START + timedelta(hours=2), views=10)
    other = await add_business_unit(
        db_session, company_id=company.id, key="ai_saas", name="AI SaaS",
        actor=ACTOR, state=BusinessUnitState.ACTIVE,
    )  # fmt: skip
    await db_session.flush()

    mine = await _measure(db_session, company, KpiScope.BUSINESS_UNIT, business_unit_id=unit.id)
    theirs = await _measure(db_session, company, KpiScope.BUSINESS_UNIT, business_unit_id=other.id)

    assert mine["newsroom.published_articles"] == 1
    assert theirs["newsroom.published_articles"] == 0
    assert theirs["newsroom.views"] == 0


async def test_a_project_sees_only_its_own_stories(db_session):
    company, unit, project = await _newsroom(db_session)
    elsewhere = Project(
        company_id=company.id, business_unit_id=unit.id, name="other",
        state=ProjectState.ACTIVE.value, kill_criteria={},
    )  # fmt: skip
    db_session.add(elsewhere)
    await db_session.flush()
    await _article(db_session, company, project, published_at=START + timedelta(hours=2))
    await _article(db_session, company, elsewhere, published_at=START + timedelta(hours=2))
    await db_session.flush()

    metrics = await _measure(db_session, company, KpiScope.PROJECT, project_id=project.id)

    assert metrics["newsroom.published_articles"] == 1
