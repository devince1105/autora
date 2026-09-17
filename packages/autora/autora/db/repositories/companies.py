from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from autora.db.models import Company, CompanyGoal, CompanyPolicy, GoalStatus
from autora.infra.ids import uuid7


async def add_company(session: AsyncSession, company: Company) -> Company:
    session.add(company)
    await session.flush()
    return company


async def get_company(session: AsyncSession, company_id: uuid.UUID) -> Company | None:
    return await session.get(Company, company_id)


async def get_company_by_slug(session: AsyncSession, slug: str) -> Company | None:
    return await session.scalar(select(Company).where(Company.slug == slug))


async def list_companies(session: AsyncSession) -> Sequence[Company]:
    return (await session.scalars(select(Company).order_by(Company.id))).all()


# --- goals -------------------------------------------------------------------------------


async def add_goal(session: AsyncSession, goal: CompanyGoal) -> CompanyGoal:
    session.add(goal)
    await session.flush()
    return goal


async def list_goals(
    session: AsyncSession,
    company_id: uuid.UUID,
    *,
    level: str | None = None,
    status: str | None = GoalStatus.ACTIVE,
) -> Sequence[CompanyGoal]:
    stmt = select(CompanyGoal).where(CompanyGoal.company_id == company_id)
    if level is not None:
        stmt = stmt.where(CompanyGoal.level == level)
    if status is not None:
        stmt = stmt.where(CompanyGoal.status == status)
    return (await session.scalars(stmt.order_by(CompanyGoal.id))).all()


# --- policies ----------------------------------------------------------------------------


async def get_policies(session: AsyncSession, company_id: uuid.UUID) -> dict[str, Any]:
    rows = await session.scalars(
        select(CompanyPolicy).where(CompanyPolicy.company_id == company_id)
    )
    return {row.key: row.value for row in rows}


async def get_policy(
    session: AsyncSession, company_id: uuid.UUID, key: str, default: Any = None
) -> Any:
    row = await session.scalar(
        select(CompanyPolicy).where(
            CompanyPolicy.company_id == company_id, CompanyPolicy.key == key
        )
    )
    return default if row is None else row.value


async def upsert_policy(
    session: AsyncSession,
    company_id: uuid.UUID,
    key: str,
    value: Any,
    *,
    updated_by: dict[str, Any],
) -> None:
    """Low-level write. Whether a change is allowed (tighten vs loosen) is decided by the
    command layer before this is called."""
    stmt = (
        insert(CompanyPolicy)
        .values(id=uuid7(), company_id=company_id, key=key, value=value, updated_by=updated_by)
        .on_conflict_do_update(
            index_elements=[CompanyPolicy.company_id, CompanyPolicy.key],
            set_={"value": value, "updated_by": updated_by, "updated_at": func.now()},
        )
    )
    await session.execute(stmt)
