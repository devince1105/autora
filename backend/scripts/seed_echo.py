"""Seed a demo company for the echo workflow: company, ACTIVE project, three agents.

    python backend/scripts/seed_echo.py            # prints company_id and project_id

Idempotent: re-running reuses the ``echo-demo`` company and its project. Then start a run:

    curl -X POST localhost:8000/api/companies/<company_id>/workflows \
      -H "Authorization: Bearer $API_BEARER_TOKEN" -H "Content-Type: application/json" \
      -d '{"template": "echo.chain_v1", "project_id": "<project_id>", "params": {"topic": "AI"}}'
"""

import asyncio
import json

from sqlalchemy import select

from autora.app import build_runtime
from autora.company.companies import create_company
from autora.db.models import CompanyType, Project, ProjectState
from autora.db.repositories.companies import get_company_by_slug
from autora.db.session import dispose_engine, get_sessionmaker
from autora.domains import echo
from autora.runtime.actor import Actor

SLUG = "echo-demo"
ACTOR = Actor.human("seed_echo")


async def seed() -> dict[str, str]:
    build_runtime()  # loads event catalogs and models
    async with get_sessionmaker()() as session:
        company = await get_company_by_slug(session, SLUG)
        if company is None:
            company, _ = await create_company(
                session,
                slug=SLUG,
                name="Echo Demo",
                type=CompanyType.NEWSROOM,
                mission="Pass a note down three desks.",
                actor=ACTOR,
            )
        project = await session.scalar(
            select(Project).where(Project.company_id == company.id, Project.name == "Echo relay")
        )
        if project is None:
            project = Project(
                company_id=company.id,
                name="Echo relay",
                state=ProjectState.ACTIVE.value,
                kill_criteria={"max_cost_usd": 5},
            )
            session.add(project)
        hired = await echo.staff_company(session, company.id, actor=ACTOR)
        await session.commit()
        return {
            "company_id": str(company.id),
            "project_id": str(project.id),
            "hired": [a.role for a in hired],
        }


async def main() -> None:
    try:
        print(json.dumps(await seed(), indent=2))
    finally:
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
