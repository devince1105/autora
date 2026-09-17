from __future__ import annotations

import uuid
from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select

from autora.db.models import AgentStep
from autora.infra.blobstore import BlobNotFound, BlobStore, LocalFSBlobStore
from autora.infra.settings import Settings
from autora.runtime.trace import get_run_trace
from autora_api.deps import Operator, Session, settings_dep

router = APIRouter(prefix="/api/runs", tags=["runs"])


def blob_store_dep(settings: Annotated[Settings, Depends(settings_dep)]) -> BlobStore:
    return LocalFSBlobStore(settings.blob_store_dir)


@router.get("/{run_id}/trace")
async def run_trace(run_id: uuid.UUID, session: Session, _: Operator) -> dict[str, Any]:
    """The run's real events in seq order, each joined with the agent step it belongs to."""
    trace = await get_run_trace(session, run_id)
    if trace is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"agent run {run_id} not found")
    return asdict(trace)


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
