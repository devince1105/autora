"""KPIs for the dashboard (T-314): GET /api/companies/{id}/kpis."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status

from autora.company.reporting_min import Kpis, load_kpis
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
