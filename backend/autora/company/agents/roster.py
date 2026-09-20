"""Company roster: hiring agents, pausing them and letting them go (platform/02).

An agent is created with its activity row (IDLE) and an AGENT_CREATED event in the caller's
transaction, so the office never shows an agent without a state.

Hiring takes the position, not just a role string: given the company's ``roles`` catalogue
(T-600), the agent's role, its position and its department are set together and the defaults of
that position are its starting point. Hiring without one still works — an agent with no place
on the org chart does its job like any other — but then nothing knows which room to draw it in.

- ``pause_agent``: the worker gives it no new task (the run it is on finishes); the office shows
  it paused. ``resume_agent`` puts it back to work.
- ``retire_agent``: it leaves the roster for good (RETIRED is not listed, and the office stops
  drawing it). Refused while a run of its is still open, so nothing is dropped half-done.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autora.company.organization import placement
from autora.db.models import AGENT_RUN_TERMINAL, Agent, AgentRun, AgentStatus, Department, Role
from autora.db.repositories import agents as agent_repo
from autora.runtime.activity import initialize_activity, set_activity
from autora.runtime.actor import Actor
from autora.runtime.events import catalog as ev
from autora.runtime.events.outbox import emit
from autora.runtime.events.schema import EventPayload, new_event


class WrongCompany(Exception):
    """A position and the agent filling it must belong to the same company, and say the same
    thing about what the agent does."""


async def hire_agent(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    role: str,
    display_name: str,
    actor: Actor,
    description: str | None = None,
    capabilities: Sequence[str] = (),
    tools: Sequence[str] = (),
    budget: dict[str, Any] | None = None,
    avatar_key: str = "default",
    position: Role | None = None,
) -> Agent:
    """Hire an agent. With ``position``, it takes that role's department and defaults."""
    if position is not None:
        if position.company_id != company_id:
            raise WrongCompany("a role of another company cannot be filled here")
        if position.key != role:
            raise WrongCompany(
                f"hired as {role!r} but the position is {position.key!r}: the string the runtime "
                "dispatches on and the position must say the same thing"
            )
        defaults = position.defaults or {}
        capabilities = capabilities or defaults.get("capabilities", ())
        tools = tools or defaults.get("tools", ())
        budget = budget or defaults.get("budget")
    agent = await agent_repo.add_agent(
        session,
        Agent(
            company_id=company_id,
            role=role,
            display_name=display_name,
            avatar_key=avatar_key,
            description=description,
            capabilities=list(capabilities),
            tools=list(tools),
            budget=budget or {},
            role_id=position.id if position else None,
            department_id=position.department_id if position else None,
        ),
    )
    department = (
        await session.get(Department, position.department_id) if position is not None else None
    )
    where = await placement(session, department)
    await emit(
        session,
        new_event(
            ev.AgentCreated(
                role=role,
                display_name=display_name,
                capabilities=list(capabilities),
                avatar_key=avatar_key,
                department_id=department.id if department else None,
                department_key=department.key if department else None,
                office_zone_key=where.zone,
                business_unit_key=where.business_unit,
            ),
            company_id=company_id,
            actor=actor,
            aggregate_type="agent",
            aggregate_id=agent.id,
            agent_id=agent.id,
        ),
    )
    await initialize_activity(session, agent, actor=actor)
    return agent


class AgentBusy(Exception):
    """The agent is in the middle of a run; wait for it, or pause it first."""


async def _set_status(
    session: AsyncSession,
    agent: Agent,
    status: AgentStatus,
    payload: EventPayload | None,
    *,
    actor: Actor,
) -> Agent:
    agent.status = status.value
    if payload is not None:
        await set_activity(session, agent, payload, actor=actor)
    await session.flush()
    return agent


async def pause_agent(
    session: AsyncSession, agent: Agent, *, actor: Actor, reason: str | None = None
) -> Agent:
    """No new work for this agent (AGENT_PAUSED). Its current run, if any, still finishes."""
    if agent.status == AgentStatus.PAUSED:
        return agent
    if agent.status == AgentStatus.RETIRED:
        raise AgentBusy(f"{agent.display_name} has left the company")
    return await _set_status(
        session, agent, AgentStatus.PAUSED, ev.AgentPaused(reason=reason), actor=actor
    )


async def resume_agent(
    session: AsyncSession, agent: Agent, *, actor: Actor, reason: str | None = None
) -> Agent:
    if agent.status == AgentStatus.ACTIVE:
        return agent
    if agent.status == AgentStatus.RETIRED:
        raise AgentBusy(f"{agent.display_name} has left the company")
    return await _set_status(
        session, agent, AgentStatus.ACTIVE, ev.AgentResumed(reason=reason), actor=actor
    )


async def retire_agent(
    session: AsyncSession, agent: Agent, *, actor: Actor, reason: str | None = None
) -> Agent:
    """The agent leaves the roster (AGENT_RETIRED). Its runs, events and outputs stay."""
    if agent.status == AgentStatus.RETIRED:
        return agent
    open_run = await session.scalar(
        select(AgentRun.id).where(
            AgentRun.agent_id == agent.id, AgentRun.state.notin_(AGENT_RUN_TERMINAL)
        )
    )
    if open_run is not None:
        raise AgentBusy(f"{agent.display_name} is working on run {open_run}")
    agent = await _set_status(session, agent, AgentStatus.RETIRED, None, actor=actor)
    await emit(
        session,
        new_event(
            ev.AgentRetired(role=agent.role, display_name=agent.display_name, reason=reason),
            company_id=agent.company_id,
            actor=actor,
            aggregate_type="agent",
            aggregate_id=agent.id,
            agent_id=agent.id,
        ),
    )
    return agent
