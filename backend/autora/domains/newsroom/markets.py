"""The investing newsroom (D-035, D-036): the company the public site is really for.

What it covers: US and Taiwan stocks, the AI and technology industry, and personal investing —
led by "what the big investors hold", in Chinese, each story linked to the filing it reports.
It reports facts and what others said, never its own advice (``newsroom.no_advice``, on here).

Its sources, and why each is set up the way it is:

- **13F filings** of five investors, from SEC EDGAR's own Atom feed per filer. Every entry is
  titled the same ("13F-HR - Quarterly report ..."), so each source prefixes the investor's name
  (``title_prefix``), every filing is a story of its own (``own_story``) and needs no second
  source to rank (``primary``, the filing is the record): this quarter's
  filing looks exactly like last quarter's and must not join the story already written. Only
  filings of the last 120 days are taken on the first poll (``max_age_days``), not the ten years
  of history the feed lists. A 13F is due 45 days after the quarter, so these arrive four times a
  year each; polling twice a day is plenty. SEC answers only requests that name a contact
  (``FETCH_CONTACT_EMAIL``).
- **AI companies' press releases** (NVIDIA, OpenAI, Google AI, Microsoft), whose feeds answer
  automated readers. TSMC's refuses them (403) and Anthropic has none; AMD's timed out when tried.
- **Searches** for Taiwan's market and the AI supply chain, which have no feed a program may read.
  Each search costs a Tavily credit, so they run twice a day: three searches, about 180 credits
  a month of the free plan's 1,000, leaving the rest for the researcher's own searches.

Scion Asset Management (Michael Burry) is on the list the user chose, but its last 13F was filed
on 2025-11-03: it may never produce another story. Kept, because a filing would be news.

Nothing here works in fixture mode: the sources are the real web (``TOOLS_PROFILE=live``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autora.company.agents.roster import hire_agent
from autora.company.companies import create_company
from autora.company.organization import bootstrap_executive, business_unit_by_key
from autora.db.models import Agent, Company, Project, ProjectState
from autora.db.repositories.companies import get_company_by_slug, get_policies, upsert_policy
from autora.domains.newsroom import organization as newsroom_org
from autora.domains.newsroom.advice import NO_ADVICE_KEY
from autora.domains.newsroom.models import Source
from autora.domains.newsroom.sources import (
    MAX_AGE_DAYS,
    OWN_STORY,
    PRIMARY,
    TITLE_PREFIX,
    add_source,
)
from autora.domains.newsroom.tools.filings import PREDECESSORS
from autora.domains.newsroom.workflow import staff_newsroom
from autora.runtime.actor import Actor

SLUG = "autora-finance"
NAME = "Autora 財經"
PROJECT = "持股動態與科技產業"
MISSION = (
    "用附原始出處的中英雙語報導，整理美股大人物持股、台美股與 AI 科技產業的公開資訊；"
    "只報導事實與別人說的話，不提供投資建議。"
)

HALF_DAY = 12 * 3600


def edgar_13f(cik: str) -> str:
    """SEC's Atom feed of one filer's 13F-HR filings, newest first."""
    return (
        "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
        f"&CIK={cik}&type=13F-HR&dateb=&owner=include&count=10&output=atom"
    )


@dataclass(frozen=True)
class MarketSource:
    name: str
    kind: str
    trust_level: Decimal
    language: str
    url: str | None = None
    config: dict[str, Any] = field(default_factory=dict)
    poll_interval_seconds: int = 3600


def _investor(
    name: str, filer: str, cik: str, *, predecessors: tuple[str, ...] = ()
) -> MarketSource:
    config: dict[str, Any] = {
        TITLE_PREFIX: f"{name}（{filer}）",
        OWN_STORY: True,
        PRIMARY: True,
        MAX_AGE_DAYS: 120,
    }
    if predecessors:
        config[PREDECESSORS] = list(predecessors)
    return MarketSource(
        name=f"SEC 13F：{name}",
        kind="rss",
        url=edgar_13f(cik),
        trust_level=Decimal("0.95"),
        language="en",
        config=config,
        poll_interval_seconds=HALF_DAY,
    )


def _press(name: str, url: str) -> MarketSource:
    return MarketSource(
        name=name,
        kind="rss",
        url=url,
        trust_level=Decimal("0.8"),
        language="en",
        config={MAX_AGE_DAYS: 7},
    )


def _search(query: str) -> MarketSource:
    return MarketSource(
        name=f"搜尋：{query}",
        kind="search_query",
        trust_level=Decimal("0.5"),
        language="zh-TW",
        config={"query": query, "k": 5, "recency_days": 2},
        poll_interval_seconds=HALF_DAY,
    )


SOURCES: tuple[MarketSource, ...] = (
    _investor("巴菲特", "Berkshire Hathaway", "0001067983"),
    # Pershing Square Capital Management (CIK 1336528) filed only a 13F-NT for 2026-06-30: its
    # holdings are reported by Pershing Square Inc. (CIK 2026053) from then on, so the previous
    # quarter adds up both entities' filings
    _investor("比爾・艾克曼", "Pershing Square", "0002026053", predecessors=("1336528",)),
    _investor("麥可・貝瑞", "Scion Asset Management", "0001649339"),
    _investor("杜肯米勒", "Duquesne Family Office", "0001536411"),
    _investor("段永平", "H&H International Investment", "0001759760"),
    _press("NVIDIA Newsroom", "https://nvidianews.nvidia.com/releases.xml"),
    _press("OpenAI News", "https://openai.com/news/rss.xml"),
    _press("Google AI Blog", "https://blog.google/technology/ai/rss/"),
    _press("Microsoft Source", "https://news.microsoft.com/source/feed/"),
    _search("台股 AI 伺服器 供應鏈"),
    _search("台積電 營收 法說會"),
    _search("美股 科技股 財報"),
)


@dataclass
class MarketsNewsroom:
    company: Company
    project: Project
    sources: list[Source]
    added: list[str]


async def seed_markets(
    session: AsyncSession, *, actor: Actor, slug: str = SLUG, name: str = NAME
) -> MarketsNewsroom:
    """The company, its desks, its project, its sources and its no-advice policy. Idempotent:
    run it again and what is missing is added, sources already there take the settings above,
    and a name given later renames it."""
    company = await get_company_by_slug(session, slug)
    if company is None:
        company, _ = await create_company(
            session, slug=slug, name=name, mission=MISSION, actor=actor
        )
    elif company.name != name:
        company.name = name
    if not (await get_policies(session, company.id)).get(NO_ADVICE_KEY):
        await upsert_policy(session, company.id, NO_ADVICE_KEY, True, updated_by=actor.as_json())

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

    existing = (await session.scalars(select(Source).where(Source.company_id == company.id))).all()
    known = {s.name: s for s in existing}  # a company's source names are unique
    added: list[str] = []
    for spec in SOURCES:
        if (source := known.get(spec.name)) is not None:
            # what the code says a source is wins: a setting added later, or a filer that moved
            # (Pershing Square), reaches the source already there, and keeps its history
            source.url = spec.url
            source.config = dict(spec.config)
            source.trust_level = spec.trust_level
            source.poll_interval_seconds = spec.poll_interval_seconds
            continue
        await add_source(
            session,
            company_id=company.id,
            name=spec.name,
            kind=spec.kind,
            url=spec.url,
            config=spec.config,
            trust_level=spec.trust_level,
            language=spec.language,
            poll_interval_seconds=spec.poll_interval_seconds,
        )
        added.append(spec.name)
    sources = (await session.scalars(select(Source).where(Source.company_id == company.id))).all()
    return MarketsNewsroom(company=company, project=project, sources=list(sources), added=added)
