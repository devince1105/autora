"""Seed a demo company for the echo workflow: company, ACTIVE project, three agents.

    python backend/scripts/seed_echo.py                     # prints company_id and project_id
    python backend/scripts/seed_echo.py --approval on       # the writer's note needs a human
    python backend/scripts/seed_echo.py --approval off      # back to fully automatic

Idempotent: re-running reuses the ``echo-demo`` company and its project. ``--approval on`` sets
the company policy override ``echo_note`` / ``writer`` = ``needs_approval`` (policies may only
tighten; the engine enforces that), so every run stops at the writer until an operator decides
in the approval inbox. Then start a run:

    curl -X POST localhost:8000/api/companies/<company_id>/workflows \
      -H "Authorization: Bearer $API_BEARER_TOKEN" -H "Content-Type: application/json" \
      -d '{"template": "echo.chain_v1", "project_id": "<project_id>", "params": {"topic": "AI"}}'
"""

import argparse
import asyncio
import json

from sqlalchemy import select

from autora.app import build_runtime
from autora.company.companies import create_company
from autora.db.models import CompanyType, Project, ProjectState
from autora.db.repositories.companies import get_company_by_slug, get_policies, upsert_policy
from autora.db.session import dispose_engine, get_sessionmaker
from autora.domains import echo
from autora.runtime.actor import Actor
from autora.runtime.policy import OVERRIDES_KEY

SLUG = "echo-demo"
ACTOR = Actor.human("seed_echo")


WRITER_NEEDS_APPROVAL = {"echo_note": {"writer": "needs_approval"}}


async def seed(approval: str | None = None) -> dict[str, object]:
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
        if approval is not None:
            overrides = WRITER_NEEDS_APPROVAL if approval == "on" else {}
            await upsert_policy(
                session, company.id, OVERRIDES_KEY, overrides, updated_by=ACTOR.as_json()
            )
        policies = await get_policies(session, company.id)
        await session.commit()
        return {
            "company_id": str(company.id),
            "project_id": str(project.id),
            "hired": [a.role for a in hired],
            "writer_needs_approval": policies.get(OVERRIDES_KEY) == WRITER_NEEDS_APPROVAL,
        }


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--approval", choices=["on", "off"], help="writer's note needs a human")
    args = parser.parse_args()
    try:
        print(json.dumps(await seed(args.approval), indent=2))
    finally:
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
