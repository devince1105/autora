from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import select

from autora.db.models import AgentRun, AgentStep
from autora.infra.blobstore import BlobNotFound, BlobStore, LocalFSBlobStore
from autora.infra.settings import Settings
from autora.runtime.trace import Trace, get_run_trace
from autora_api.deps import Operator, Session, settings_dep

router = APIRouter(prefix="/api/runs", tags=["runs"])


class RunOut(BaseModel):
    """One agent run: what it was asked, what it produced, what it cost."""

    id: uuid.UUID
    company_id: uuid.UUID
    task_id: uuid.UUID
    agent_id: uuid.UUID
    attempt: int
    state: str
    input: dict[str, Any]
    output: dict[str, Any] | None
    evaluation: dict[str, Any] | None
    handoff: list[dict[str, Any]] | None
    error: dict[str, Any] | None
    cost_usd: Decimal
    tokens_in: int
    tokens_out: int
    steps_count: int
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


@router.get("/{run_id}")
async def get_run(run_id: uuid.UUID, session: Session, _: Operator) -> RunOut:
    run = await session.get(AgentRun, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"agent run {run_id} not found")
    return RunOut.model_validate(run, from_attributes=True)


def blob_store_dep(settings: Annotated[Settings, Depends(settings_dep)]) -> BlobStore:
    return LocalFSBlobStore(settings.blob_store_dir)


@router.get("/{run_id}/trace")
async def run_trace(run_id: uuid.UUID, session: Session, _: Operator) -> Trace:
    """The run's real events in seq order, each joined with the agent step it belongs to."""
    trace = await get_run_trace(session, run_id)
    if trace is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"agent run {run_id} not found")
    return trace


@router.get("/{run_id}/steps/{seq}/blob")
async def step_blob(
    run_id: uuid.UUID,
    seq: int,
    session: Session,
    _: Operator,
    blobs: Annotated[BlobStore, Depends(blob_store_dep)],
) -> Response:
    """Full prompt/response payload of one step (admin only)."""
    key = await session.scalar(
        select(AgentStep.blob_key).where(AgentStep.run_id == run_id, AgentStep.seq == seq)
    )
    if key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no payload for run {run_id} step {seq}")
    try:
        data = await blobs.get(key)
    except BlobNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"payload {key} is missing") from None
    return Response(content=data, media_type="application/json")
