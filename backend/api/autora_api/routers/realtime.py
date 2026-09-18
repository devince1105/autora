"""Realtime snapshot (T-301): what the browser hydrates from before it opens the WebSocket."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status

from autora.db.models import Company
from autora.realtime.projection import RealtimeSnapshot, begin_consistent_read, load_snapshot
from autora_api.deps import Operator, Session

router = APIRouter(prefix="/api/companies/{company_id}/realtime", tags=["realtime"])


@router.get("/snapshot")
async def get_snapshot(company_id: uuid.UUID, session: Session, _: Operator) -> RealtimeSnapshot:
    """Agents, active tasks, recent events and ``last_seq``, read at one consistent moment.
    Connect the WebSocket with ``since=last_seq`` and apply newer events on top."""
    await begin_consistent_read(session)
    if await session.get(Company, company_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"company {company_id} not found")
    return await load_snapshot(session, company_id)
