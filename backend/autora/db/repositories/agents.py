from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autora.db.models import Agent, AgentStatus


async def add_agent(session: AsyncSession, agent: Agent) -> Agent:
    session.add(agent)
    await session.flush()
    return agent


async def get_agent(session: AsyncSession, agent_id: uuid.UUID) -> Agent | None:
    return await session.get(Agent, agent_id)


async def list_agents(
    session: AsyncSession,
    company_id: uuid.UUID,
    *,
    role: str | None = None,
    include_retired: bool = False,
) -> Sequence[Agent]:
    stmt = select(Agent).where(Agent.company_id == company_id)
    if role is not None:
        stmt = stmt.where(Agent.role == role)
    if not include_retired:
        stmt = stmt.where(Agent.status != AgentStatus.RETIRED)
    return (await session.scalars(stmt.order_by(Agent.id))).all()
