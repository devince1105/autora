from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autora.db.models import AGENT_RUN_TERMINAL, AgentRun, AgentStep, StepKind
from autora.infra.blobstore import BlobStore


class TraceError(Exception):
    pass


def step_blob_key(company_id: uuid.UUID, run_id: uuid.UUID, seq: int) -> str:
    return f"companies/{company_id}/runs/{run_id}/steps/{seq:05d}.json"


async def record_step(
    session: AsyncSession,
    blobs: BlobStore,
    run_id: uuid.UUID,
    *,
    kind: StepKind,
    summary: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    prompt_hash: str | None = None,
    cost_usd: Decimal = Decimal(0),
    payload: dict[str, Any] | None = None,
) -> AgentStep:
    """Append the next step of ``run_id`` in the caller's transaction.

    ``seq`` comes from ``agent_runs.steps_count`` under a row lock, so concurrent writers for the
    same run cannot produce gaps or duplicates. ``payload`` (full prompt/response) is written to
    the BlobStore before the row; if the transaction later rolls back, the blob is an unreferenced
    orphan, which is harmless. The reverse order could leave a row pointing at nothing.
    """
    run = await session.scalar(select(AgentRun).where(AgentRun.id == run_id).with_for_update())
    if run is None:
        raise TraceError(f"agent run {run_id} does not exist")
    if run.state in AGENT_RUN_TERMINAL:
        raise TraceError(f"agent run {run_id} is {run.state}; its trace is closed")

    seq = run.steps_count
    blob_key = None
    if payload is not None:
        blob_key = await blobs.put(
            step_blob_key(run.company_id, run.id, seq),
            json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8"),
        )

    step = AgentStep(
        company_id=run.company_id,
        run_id=run.id,
        seq=seq,
        kind=kind.value,
        summary=summary,
        tool_calls=tool_calls,
        prompt_hash=prompt_hash,
        blob_key=blob_key,
        cost_usd=cost_usd,
    )
    session.add(step)
    run.steps_count = seq + 1
    await session.flush()
    return step
