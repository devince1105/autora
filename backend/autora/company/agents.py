"""Company roster: hiring agents (logs/platform/02_COMPANY_MODEL.md).

An agent is created with its activity row (IDLE) and an AGENT_CREATED event in the caller's
transaction, so the office never shows an agent without a state.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from autora.db.models import Agent
from autora.db.repositories import agents as agent_repo
from autora.runtime.activity import initialize_activity
from autora.runtime.actor import Actor
from autora.runtime.events import catalog as ev
from autora.runtime.events.outbox import emit
from autora.runtime.events.schema import new_event


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
) -> Agent:
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
        ),
    )
    await emit(
        session,
        new_event(
            ev.AgentCreated(
                role=role,
                display_name=display_name,
                capabilities=list(capabilities),
                avatar_key=avatar_key,
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
