"""Company lifecycle operations.

Each operation changes state and emits its event in the caller's transaction. When the command
pipeline lands (T-604) these become command handlers behind the PolicyEngine; the event contract
stays the same.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from autora.company.cycle import ensure_cycle_schedule
from autora.company.events import CompanyCreated
from autora.db.models import Company, CompanyType
from autora.db.repositories import companies
from autora.runtime.actor import Actor
from autora.runtime.events.outbox import emit
from autora.runtime.events.schema import EventEnvelope, new_event


class CompanyAlreadyExists(Exception):
    def __init__(self, slug: str):
        self.slug = slug
        super().__init__(f"a company with slug {slug!r} already exists")


async def create_company(
    session: AsyncSession,
    *,
    slug: str,
    name: str,
    type: CompanyType,
    mission: str | None,
    actor: Actor,
    timezone: str = "UTC",
) -> tuple[Company, EventEnvelope]:
    """Create a company and give it its daily cycle.

    The schedule is part of being a company, not an extra somebody remembers to switch on
    (AC-11): from here nobody has to start anything — the schedule opens a cycle, the cycle
    plans, and the plan starts the day's work. A company with no agents simply spends its days
    planning nothing, which is the correct amount of work for a company with nobody in it.
    """
    if await companies.get_company_by_slug(session, slug) is not None:
        raise CompanyAlreadyExists(slug)
    company = await companies.add_company(
        session, Company(slug=slug, name=name, type=type.value, mission=mission)
    )
    await ensure_cycle_schedule(session, company.id, timezone=timezone)
    envelope = await emit(
        session,
        new_event(
            CompanyCreated(slug=slug, name=name, type=type.value),
            company_id=company.id,
            actor=actor,
            aggregate_type="company",
            aggregate_id=company.id,
        ),
    )
    return company, envelope
