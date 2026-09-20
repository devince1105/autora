"""The demo newsroom (T-518, 3d-office/06 §6): a company that runs the whole line in simulation.

With ``MODEL_PROVIDER=fake`` and ``TOOLS_PROFILE=fixture`` nothing leaves the machine, yet every
step is real: the sources are the fixture feeds (fictional Lumen City), the poller reads them,
the story desk clusters their items into stories, the workflow runs the five agents through the
real tools, the fact-check checks, a person approves in the inbox, the publisher publishes.

- ``seed_demo``: the company, its project, the five desks and the two fixture sources
  (idempotent);
- ``gather_stories``: poll the sources now and cluster the new items;
- ``start_demo_story``: select a story and start its workflow, with the simulation's demo knobs
  (``pace_seconds``, ``revise_first_review``, ``editor_fails``; see ``simulation.py``).

``backend/scripts/seed_newsroom.py`` runs these for a local demo; the e2e test runs them too.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autora.company.agents.roster import hire_agent
from autora.company.companies import create_company
from autora.company.organization import bootstrap_executive, business_unit_by_key
from autora.db.models import (
    Agent,
    Company,
    CompanyType,
    Project,
    ProjectState,
    WorkflowRun,
)
from autora.db.repositories.companies import get_company_by_slug
from autora.domains.newsroom import organization as newsroom_org
from autora.domains.newsroom.models import Source, Story, StoryState
from autora.domains.newsroom.sources import SourcePoller, add_source
from autora.domains.newsroom.stories import StoryDesk
from autora.domains.newsroom.workflow import staff_newsroom, start_story
from autora.runtime.actor import Actor
from autora.runtime.dag import WorkflowEngine
from autora.runtime.policy import PolicyEngine

SLUG = "newsroom-demo"
PROJECT = "流明市報導"


@dataclass(frozen=True)
class FixtureSource:
    name: str
    url: str
    language: str
    trust_level: Decimal


SOURCES = (
    FixtureSource("Lumen City News", "https://news.fixtures.autora.test/feed.xml", "en",
                  Decimal("0.7")),
    FixtureSource("流明市政府新聞稿", "https://city.fixtures.autora.test/press/feed.atom", "zh-TW",
                  Decimal("0.8")),
)  # fmt: skip


@dataclass
class DemoNewsroom:
    company: Company
    project: Project
    sources: list[Source]


async def seed_demo(
    session: AsyncSession, *, actor: Actor, slug: str = SLUG, name: str = "流明日報（示範）"
) -> DemoNewsroom:
    company = await get_company_by_slug(session, slug)
    if company is None:
        company, _ = await create_company(
            session,
            slug=slug,
            name=name,
            type=CompanyType.NEWSROOM,
            mission="用有來源、查核過的中英雙語報導，讓流明市民知道城市裡發生了什麼。",
            actor=actor,
        )
    _, ceo_role = await bootstrap_executive(session, company.id, actor=actor)
    if (
        await session.scalar(
            select(Agent.id).where(Agent.company_id == company.id, Agent.role == ceo_role.key)
        )
        is None
    ):
        await hire_agent(
            session,
            company_id=company.id,
            role=ceo_role.key,
            display_name="Cyra",
            actor=actor,
            position=ceo_role,
        )
    await staff_newsroom(session, company.id, actor=actor)
    unit = await business_unit_by_key(session, company.id, newsroom_org.BUSINESS_UNIT)
    project = await session.scalar(
        select(Project).where(Project.company_id == company.id, Project.name == PROJECT)
    )
    if project is None:
        project = Project(
            company_id=company.id,
            business_unit_id=unit.id if unit else None,
            name=PROJECT,
            state=ProjectState.ACTIVE.value,
            kill_criteria={"max_cost_usd": 5},
        )
        session.add(project)
        await session.flush()
    elif project.business_unit_id is None and unit is not None:
        project.business_unit_id = unit.id  # a demo seeded before the organisation existed
    known = set(
        (await session.scalars(select(Source.url).where(Source.company_id == company.id))).all()
    )
    for fixture in SOURCES:
        if fixture.url not in known:
            await add_source(
                session,
                company_id=company.id,
                name=fixture.name,
                kind="rss",
                url=fixture.url,
                trust_level=fixture.trust_level,
                language=fixture.language,
            )
    sources = (await session.scalars(select(Source).where(Source.company_id == company.id))).all()
    return DemoNewsroom(company=company, project=project, sources=list(sources))


async def gather_stories(
    session: AsyncSession,
    company_id: uuid.UUID,
    *,
    poller: SourcePoller,
    desk: StoryDesk,
) -> list[Story]:
    """Poll every source of the company now, cluster the new items; the open stories, best
    first."""
    sources = (await session.scalars(select(Source).where(Source.company_id == company_id))).all()
    for source in sources:
        await poller.poll(session, source)
    await desk.cluster_pending(session, company_id)
    return list(
        (
            await session.scalars(
                select(Story)
                .where(
                    Story.company_id == company_id,
                    Story.state.in_([StoryState.DISCOVERED.value, StoryState.SELECTED.value]),
                )
                .order_by(Story.score.desc(), Story.id)
            )
        ).all()
    )


def pick(stories: list[Story], keyword: str) -> Story | None:
    """The best story whose title mentions ``keyword`` (case-insensitive)."""
    keyword = keyword.lower()
    return next((s for s in stories if keyword in s.title.lower()), None)


async def start_demo_story(
    session: AsyncSession,
    *,
    policy: PolicyEngine,
    workflows: WorkflowEngine,
    desk: StoryDesk,
    story: Story,
    project_id: uuid.UUID,
    actor: Actor,
    pace_seconds: float = 0,
    revise_first_review: bool = False,
    editor_fails: bool = False,
    pause: dict | None = None,
) -> WorkflowRun:
    if story.state == StoryState.DISCOVERED:
        await desk.select(session, story, project_id=project_id, actor=actor, reason="demo")
    demo = {
        "pace_seconds": pace_seconds,
        "revise_first_review": revise_first_review,
        "editor_fails": editor_fails,
        # {"task": "draft", "attempt": 1, "seconds": 120}: hang that attempt after its tool has
        # written, so a test can kill the worker in that window (AC-S4)
        "pause": pause or {},
    }
    return await start_story(
        session,
        policy=policy,
        workflows=workflows,
        story=story,
        project_id=project_id,
        actor=actor,
        demo={k: v for k, v in demo.items() if v},
    )
