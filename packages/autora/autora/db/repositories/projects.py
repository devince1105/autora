from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autora.db.models import Budget, Project


async def add_project(session: AsyncSession, project: Project) -> Project:
    session.add(project)
    await session.flush()
    return project


async def get_project(session: AsyncSession, project_id: uuid.UUID) -> Project | None:
    return await session.get(Project, project_id)


async def list_projects(
    session: AsyncSession, company_id: uuid.UUID, *, state: str | None = None
) -> Sequence[Project]:
    stmt = select(Project).where(Project.company_id == company_id)
    if state is not None:
        stmt = stmt.where(Project.state == state)
    return (await session.scalars(stmt.order_by(Project.id))).all()


async def add_budget(session: AsyncSession, budget: Budget) -> Budget:
    session.add(budget)
    await session.flush()
    return budget


async def list_budgets(
    session: AsyncSession, company_id: uuid.UUID, *, project_id: uuid.UUID | None = None
) -> Sequence[Budget]:
    stmt = select(Budget).where(Budget.company_id == company_id)
    if project_id is not None:
        stmt = stmt.where(Budget.project_id == project_id)
    return (await session.scalars(stmt.order_by(Budget.id))).all()
