from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from autora.app import build_behaviors
from autora.company.agents import AgentBusy, hire_agent
from autora.company.agents import pause_agent as agent_pause
from autora.company.agents import resume_agent as agent_resume
from autora.company.agents import retire_agent as agent_retire
from autora.company.companies import CompanyAlreadyExists, create_company
from autora.company.organization import org_chart
from autora.db.models import Agent, Company, CompanyType, Department
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
    department_id: uuid.UUID | None = None
    department_key: str | None = None
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
    department = (
        await session.get(Department, agent.department_id)
        if agent.department_id is not None
        else None
    )
    return AgentOut(
        id=agent.id,
        role=agent.role,
        display_name=agent.display_name,
        avatar_key=agent.avatar_key,
        department_id=agent.department_id,
        department_key=department.key if department else None,
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


async def _agent_or_404(session: Session, company_id: uuid.UUID, agent_id: uuid.UUID) -> Agent:
    agent = await session.get(Agent, agent_id)
    if agent is None or agent.company_id != company_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"agent {agent_id} not found")
    return agent


class AgentDecision(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


@router.post("/{company_id}/agents/{agent_id}/pause")
async def pause(
    company_id: uuid.UUID,
    agent_id: uuid.UUID,
    body: AgentDecision,
    session: Session,
    operator: Operator,
) -> AgentOut:
    """No new tasks for this agent; the run it is on still finishes."""
    agent = await _agent_or_404(session, company_id, agent_id)
    return await _decide(session, agent_pause, agent, operator, body.reason)


@router.post("/{company_id}/agents/{agent_id}/resume")
async def resume(
    company_id: uuid.UUID,
    agent_id: uuid.UUID,
    body: AgentDecision,
    session: Session,
    operator: Operator,
) -> AgentOut:
    agent = await _agent_or_404(session, company_id, agent_id)
    return await _decide(session, agent_resume, agent, operator, body.reason)


@router.post("/{company_id}/agents/{agent_id}/retire")
async def retire(
    company_id: uuid.UUID,
    agent_id: uuid.UUID,
    body: AgentDecision,
    session: Session,
    operator: Operator,
) -> AgentOut:
    """The agent leaves the roster. Its runs, events and outputs stay; it cannot be brought back."""
    agent = await _agent_or_404(session, company_id, agent_id)
    return await _decide(session, agent_retire, agent, operator, body.reason)


async def _decide(session: Session, command, agent: Agent, operator: Operator, reason) -> AgentOut:
    try:
        agent = await command(session, agent, actor=operator, reason=reason)
    except AgentBusy as busy:
        raise HTTPException(status.HTTP_409_CONFLICT, str(busy)) from None
    await session.commit()
    return await _agent_out(session, agent)


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


# --- the organisation (T-600) ---------------------------------------------------------------


class RoleOut(BaseModel):
    id: uuid.UUID
    key: str
    """The string the runtime dispatches on: a task asking for this role reaches this desk."""
    title: str
    is_lead: bool
    responsibilities: str | None
    held_by: list[uuid.UUID] = []
    """Agents holding this position. Empty is a real state — a defined but unfilled chair."""


class DepartmentOut(BaseModel):
    id: uuid.UUID
    key: str
    name: str
    purpose: str | None
    office_zone_key: str | None
    roles: list[RoleOut] = []
    agents: list[AgentOut] = []
    teams: list[DepartmentOut] = []
    headcount: int


class ProductOut(BaseModel):
    id: uuid.UUID
    key: str
    name: str
    state: str
    public_url: str | None


class BusinessUnitOut(BaseModel):
    id: uuid.UUID | None
    """None for the company's own shared functions, which belong to no business."""
    key: str | None
    name: str
    mission: str | None = None
    state: str | None = None
    departments: list[DepartmentOut] = []
    products: list[ProductOut] = []


class OrgOut(BaseModel):
    """The company's organisation: what it is in, how it is arranged, and who is where."""

    company_id: uuid.UUID
    shared: BusinessUnitOut
    units: list[BusinessUnitOut] = []
    unplaced: list[AgentOut] = []
    """Agents with no department: they work, they are just not on the chart yet."""
    headcount: int


@router.get("/{company_id}/org")
async def get_org(session: Session, company_id: uuid.UUID) -> OrgOut:
    """The whole org chart in one call — what the office needs to draw its rooms."""
    await _company_or_404(session, company_id)
    chart = await org_chart(session, company_id)
    by_role: dict[uuid.UUID, list[uuid.UUID]] = {}
    for node in _walk(chart):
        for agent in node.agents:
            if agent.role_id is not None:
                by_role.setdefault(agent.role_id, []).append(agent.id)

    async def department(node) -> DepartmentOut:
        return DepartmentOut(
            id=node.department.id,
            key=node.department.key,
            name=node.department.name,
            purpose=node.department.purpose,
            office_zone_key=node.department.office_zone_key,
            roles=[
                RoleOut(
                    id=role.id,
                    key=role.key,
                    title=role.title,
                    is_lead=role.is_lead,
                    responsibilities=role.responsibilities,
                    held_by=by_role.get(role.id, []),
                )
                for role in node.roles
            ],
            agents=[await _agent_out(session, agent) for agent in node.agents],
            teams=[await department(team) for team in node.teams],
            headcount=node.headcount,
        )

    async def unit(node, *, name: str) -> BusinessUnitOut:
        return BusinessUnitOut(
            id=node.unit.id if node.unit else None,
            key=node.unit.key if node.unit else None,
            name=node.unit.name if node.unit else name,
            mission=node.unit.mission if node.unit else None,
            state=node.unit.state if node.unit else None,
            departments=[await department(d) for d in node.departments],
            products=[
                ProductOut(id=p.id, key=p.key, name=p.name, state=p.state, public_url=p.public_url)
                for p in node.products
            ],
        )

    return OrgOut(
        company_id=company_id,
        shared=await unit(chart.shared, name="Company"),
        units=[await unit(u, name="") for u in chart.units],
        unplaced=[await _agent_out(session, agent) for agent in chart.unplaced],
        headcount=chart.headcount,
    )


def _walk(chart):
    def nodes(node):
        yield node
        for team in node.teams:
            yield from nodes(team)

    for unit in (chart.shared, *chart.units):
        for node in unit.departments:
            yield from nodes(node)


DepartmentOut.model_rebuild()
