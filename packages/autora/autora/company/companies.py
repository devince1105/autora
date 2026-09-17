"""Company lifecycle operations.

Each operation changes state and emits its event in the caller's transaction. When the command
pipeline lands (T-604) these become command handlers behind the PolicyEngine; the event contract
stays the same.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

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
) -> tuple[Company, EventEnvelope]:
    if await companies.get_company_by_slug(session, slug) is not None:
        raise CompanyAlreadyExists(slug)
    company = await companies.add_company(
        session, Company(slug=slug, name=name, type=type.value, mission=mission)
    )
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
