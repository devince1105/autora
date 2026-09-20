from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from autora.app import build_behaviors
from autora.company.agents import hire_agent
from autora.company.companies import CompanyAlreadyExists, create_company
from autora.db.models import Agent, Company, CompanyType
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
    agents: int = 0
    """Active agents. A page with no company asked for shows a company that has some."""

    @classmethod
    def of(cls, company: Company, agents: int = 0) -> CompanyOut:
        return cls.model_validate(company, from_attributes=True).model_copy(
            update={"agents": agents}
        )


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
async def list_companies(
    session: Session,
    _: Operator,
    include_archived: Annotated[bool, Query(description="Archived companies too")] = False,
) -> list[CompanyOut]:
    counts = await company_repo.active_agent_counts(session)
    companies = await company_repo.list_companies(session, include_archived=include_archived)
    return [CompanyOut.of(c, counts.get(c.id, 0)) for c in companies]


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


async def _agent_out(session: Session, agent: Agent) -> AgentOut:
    activity = await get_activity(session, agent.id)
    return AgentOut(
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


@router.get("/{company_id}/agents")
async def list_company_agents(
    company_id: uuid.UUID, session: Session, _: Operator
) -> list[AgentOut]:
    await _company_or_404(session, company_id)
    return [await _agent_out(session, a) for a in await agent_repo.list_agents(session, company_id)]


class NewAgent(BaseModel):
    role: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=40)
    """A role the runtime can run (GET /api/roles); anything else would never pick up a task."""
    display_name: str = Field(min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=500)
    tools: list[str] = Field(default=[], max_length=20)
    """Narrows the behavior's tools for this agent; empty means all of them."""
    per_run_usd: Decimal | None = Field(default=None, ge=0, le=100)
    avatar_key: str = Field(default="default", max_length=40)


@router.post("/{company_id}/agents", status_code=status.HTTP_201_CREATED)
async def hire(
    company_id: uuid.UUID, body: NewAgent, session: Session, operator: Operator
) -> AgentOut:
    """Hire an agent: it appears in the office and starts taking its role's tasks."""
    await _company_or_404(session, company_id)
    known = build_behaviors().roles()
    if body.role not in known:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"no behavior for role {body.role!r}; the runtime knows {', '.join(known)}",
        )
    agent = await hire_agent(
        session,
        company_id=company_id,
        role=body.role,
        display_name=body.display_name,
        actor=operator,
        description=body.description,
        tools=body.tools,
        budget={"per_run_usd": float(body.per_run_usd)} if body.per_run_usd is not None else None,
        avatar_key=body.avatar_key,
    )
    await session.commit()
    return await _agent_out(session, agent)
