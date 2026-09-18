from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from autora.company.companies import CompanyAlreadyExists, create_company
from autora.db.models import Company, CompanyType
from autora.db.repositories import agents as agent_repo
from autora.db.repositories import companies as company_repo
from autora.runtime.activity import effective_state, get_activity
from autora_api.deps import Operator, Session

router = APIRouter(prefix="/api/companies", tags=["companies"])


class CompanyCreate(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    name: str = Field(min_length=1, max_length=200)
    type: CompanyType
    mission: str | None = None


class CompanyOut(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    type: str
    mission: str | None
    status: str
    created_at: datetime

    @classmethod
    def of(cls, company: Company) -> CompanyOut:
        return cls.model_validate(company, from_attributes=True)


class ActivityOut(BaseModel):
    state: str
    """Effective state for display (COMPLETED past display_until reads IDLE)."""
    stored_state: str
    detail: dict[str, Any]
    run_id: uuid.UUID | None
    task_id: uuid.UUID | None
    since: datetime
    last_event_seq: int


class AgentOut(BaseModel):
    id: uuid.UUID
    role: str
    display_name: str
    avatar_key: str
    status: str
    activity: ActivityOut | None


async def _company_or_404(session: Session, company_id: uuid.UUID) -> Company:
    company = await company_repo.get_company(session, company_id)
    if company is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"company {company_id} not found")
    return company


@router.get("")
async def list_companies(session: Session, _: Operator) -> list[CompanyOut]:
    return [CompanyOut.of(c) for c in await company_repo.list_companies(session)]


@router.post("", status_code=status.HTTP_201_CREATED)
async def post_company(body: CompanyCreate, session: Session, operator: Operator) -> CompanyOut:
    try:
        company, _ = await create_company(
            session,
            slug=body.slug,
            name=body.name,
            type=body.type,
            mission=body.mission,
            actor=operator,
        )
    except CompanyAlreadyExists as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await session.commit()
    return CompanyOut.of(company)


@router.get("/{company_id}")
async def get_company(company_id: uuid.UUID, session: Session, _: Operator) -> CompanyOut:
    return CompanyOut.of(await _company_or_404(session, company_id))


@router.get("/{company_id}/agents")
async def list_company_agents(
    company_id: uuid.UUID, session: Session, _: Operator
) -> list[AgentOut]:
    await _company_or_404(session, company_id)
    result = []
    for agent in await agent_repo.list_agents(session, company_id):
        activity = await get_activity(session, agent.id)
        result.append(
            AgentOut(
                id=agent.id,
                role=agent.role,
                display_name=agent.display_name,
                avatar_key=agent.avatar_key,
                status=agent.status,
                activity=None
                if activity is None
                else ActivityOut(
                    state=effective_state(activity),
                    stored_state=activity.state,
                    detail=activity.detail,
                    run_id=activity.run_id,
                    task_id=activity.task_id,
                    since=activity.since,
                    last_event_seq=activity.last_event_seq,
                ),
            )
        )
    return result
