from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from autora.db.repositories import companies as company_repo
from autora.runtime.events.outbox import load_events
from autora_api.deps import Operator, Session

router = APIRouter(prefix="/api/events", tags=["events"])

MAX_LIMIT = 500


class EventsPage(BaseModel):
    items: list[dict[str, Any]]
    """Event envelopes as JSON (shape: frontend/event-schema EventEnvelope)."""
    next_after: int
    """Pass as ``after`` to fetch the next page. Equals ``after`` when the page is empty."""
    has_more: bool


@router.get("")
async def list_events(
    session: Session,
    _: Operator,
    company_id: uuid.UUID,
    after: Annotated[int, Query(ge=0, description="Return events with seq > after")] = 0,
    until: Annotated[int | None, Query(ge=0, description="Return events with seq <= until")] = None,
    type: Annotated[
        list[str] | None, Query(description="Filter by event_type (repeatable)")
    ] = None,
    agent_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 200,
) -> EventsPage:
    """Events of one company ordered by ``seq``. Used for trace views and realtime gap-fill."""
    if await company_repo.get_company(session, company_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"company {company_id} not found")
    events = await load_events(
        session,
        company_id,
        after_seq=after,
        until_seq=until,
        event_types=type,
        agent_id=agent_id,
        run_id=run_id,
        limit=limit + 1,
    )
    page, has_more = events[:limit], len(events) > limit
    return EventsPage(
        items=[event.model_dump(mode="json") for event in page],
        next_after=page[-1].seq if page else after,
        has_more=has_more,
    )
