"""What the company looks like right now: the dashboard's KPIs (T-314) and the CEO's snapshot
(T-603). The snapshot endpoint returns the same document the CEO is given, so a person can read
exactly what the decision was made from."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status

from autora.company.reporting_min import Kpis, load_kpis
from autora.company.snapshot import CompanySnapshot
from autora.db.models import Company
from autora_api.deps import Operator, RuntimeDep, Session

router = APIRouter(prefix="/api/companies/{company_id}", tags=["reporting"])


@router.get("/kpis")
async def get_kpis(
    company_id: uuid.UUID, session: Session, runtime: RuntimeDep, _: Operator
) -> Kpis:
    """Cash, today's revenue and expenses (model calls included), today's goal, and whatever
    each domain counted today under its own name."""
    if await session.get(Company, company_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"company {company_id} not found")
    return await load_kpis(session, company_id, reporting=runtime.reporting)


@router.get("/snapshot")
async def get_snapshot(
    company_id: uuid.UUID,
    session: Session,
    runtime: RuntimeDep,
    _: Operator,
    tokens: int | None = None,
) -> CompanySnapshot:
    """The CEO's view of the company: capital, goals, the portfolio, and what each domain put
    in front of the decision. ``trimmed`` says what a size limit left out."""
    if await session.get(Company, company_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"company {company_id} not found")
    return await runtime.snapshots.build(session, company_id, token_budget=tokens)
