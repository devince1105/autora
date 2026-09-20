"""Where the newsroom sits in the company (logs/ARCHITECTURE_V2.md §16, T-600).

Until now the newsroom *was* the company: five agents with a role each and nothing above them.
It is one department of one business now — AI Media — and this module is where that is said.

The shape it builds:

    Company
      Executive                      (no business unit: it decides which businesses to run)
      AI Media                       business unit
        Newsroom                     department      -> editor_in_chief
          Research                   team            -> researcher, analyst
          Writing                    team            -> writer
          Editing                    team            -> editor
          Audience                   team            -> marketing
        Daily English World          product

The roles are the same keys as before, so nothing in the runtime changes: a behavior is still
registered for ``writer``, a task still asks for ``writer``. What is new is that the key now
resolves to a desk in a team in a department of a business.

``editor_in_chief`` is defined here but nobody holds it yet — its agent arrives with T-605b.
The chair being empty is the honest state: today a person starts the line, and the newsroom
runs without anybody choosing the day's stories.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from autora.company.organization import (
    add_business_unit,
    add_department,
    add_product,
    add_role,
    business_unit_by_key,
    department_by_key,
    product_by_key,
    role_by_key,
)
from autora.db.models import BusinessUnit, BusinessUnitState, Department, Product, ProductState
from autora.runtime.actor import Actor

BUSINESS_UNIT = "ai_media"
NEWSROOM = "newsroom"
PRODUCT = "daily_english_world"
CHIEF = "editor_in_chief"


@dataclass(frozen=True)
class Team:
    key: str
    name: str
    zone: str
    roles: tuple[tuple[str, str], ...]
    """(role key, title). The keys are the ones the runtime already runs."""


TEAMS = (
    Team(
        "newsroom_research",
        "Research",
        "research",
        (("researcher", "Researcher"), ("analyst", "Analyst")),
    ),
    Team("newsroom_writing", "Writing", "editorial", (("writer", "Writer"),)),
    Team("newsroom_editing", "Editing", "editorial", (("editor", "Copy Editor"),)),
    Team("newsroom_audience", "Audience", "growth", (("marketing", "Audience Lead"),)),
)
"""The desk's teams. ``zone`` is which part of the floor they occupy — the 3D office's own
setting, kept next to the department it describes rather than hard-coded in the frontend."""

RESPONSIBILITIES = {
    "editor_in_chief": "Chooses what the newsroom covers, and holds the editorial standard.",
    "researcher": "Finds and captures the evidence a story stands on.",
    "analyst": "Turns evidence into checkable claims and the numbers a story leads with.",
    "writer": "Writes the story in both languages from the claims, and nothing else.",
    "editor": "Checks the draft against its claims and sends it back or accepts it.",
    "marketing": "Takes a published article to its readers.",
}


@dataclass(frozen=True)
class NewsroomOrg:
    business_unit: BusinessUnit
    newsroom: Department
    teams: tuple[Department, ...]
    product: Product


async def build(session: AsyncSession, company_id: uuid.UUID, *, actor: Actor) -> NewsroomOrg:
    """Create the newsroom's place in the company, or return what is already there.

    Idempotent by key, like the staffing it replaces: running the seed twice builds one
    organisation, not two.
    """
    unit = await business_unit_by_key(session, company_id, BUSINESS_UNIT)
    if unit is None:
        unit = await add_business_unit(
            session,
            company_id=company_id,
            key=BUSINESS_UNIT,
            name="AI Media",
            actor=actor,
            mission="Bilingual reporting that says where every fact came from.",
            state=BusinessUnitState.ACTIVE,
            kill_criteria={
                "evaluate_after_cycles": 7,
                "auto_pause_if": {
                    "metric": "cost_per_published_article",
                    "op": ">",
                    "value": 3.0,
                },
            },
        )
    desk = await department_by_key(session, company_id, NEWSROOM)
    if desk is None:
        desk = await add_department(
            session,
            company_id=company_id,
            key=NEWSROOM,
            name="Newsroom",
            actor=actor,
            business_unit_id=unit.id,
            purpose="Reports the city, in Chinese and English.",
            office_zone_key="editorial",
        )
    chief = await role_by_key(session, company_id, CHIEF)
    if chief is None:
        chief = await add_role(
            session,
            company_id=company_id,
            key=CHIEF,
            title="Editor-in-Chief",
            department_id=desk.id,
            actor=actor,
            is_lead=True,
            responsibilities=RESPONSIBILITIES[CHIEF],
        )

    teams = []
    for spec in TEAMS:
        team = await department_by_key(session, company_id, spec.key)
        if team is None:
            team = await add_department(
                session,
                company_id=company_id,
                key=spec.key,
                name=spec.name,
                actor=actor,
                parent_department_id=desk.id,
                office_zone_key=spec.zone,
            )
        for role_key, title in spec.roles:
            if await role_by_key(session, company_id, role_key) is None:
                await add_role(
                    session,
                    company_id=company_id,
                    key=role_key,
                    title=title,
                    department_id=team.id,
                    actor=actor,
                    reports_to_role_id=chief.id,
                    responsibilities=RESPONSIBILITIES.get(role_key),
                )
        teams.append(team)

    product = await product_by_key(session, company_id, PRODUCT)
    if product is None:
        product = await add_product(
            session,
            company_id=company_id,
            key=PRODUCT,
            name="Daily English World",
            business_unit_id=unit.id,
            actor=actor,
            description="The bilingual site the newsroom publishes to.",
            state=ProductState.LIVE,
            public_url="/news/zh-TW",
        )
    return NewsroomOrg(business_unit=unit, newsroom=desk, teams=tuple(teams), product=product)
