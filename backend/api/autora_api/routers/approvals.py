from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from autora.db.models import Approval, ApprovalState
from autora.runtime.approvals import ApprovalError
from autora_api.deps import Operator, RuntimeDep, Session

router = APIRouter(prefix="/api/approvals", tags=["approvals"])


class ApprovalOut(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    kind: str
    ref_type: str
    ref_id: uuid.UUID
    task_id: uuid.UUID | None
    run_id: uuid.UUID | None
    action: str | None
    payload: dict[str, Any]
    summary: str
    requested_by: dict[str, Any]
    state: str
    expires_at: datetime | None
    decided_by: dict[str, Any] | None
    decided_at: datetime | None
    reason: str | None
    created_at: datetime


class DecisionIn(BaseModel):
    decision: Literal["approve", "reject"]
    reason: str | None = Field(default=None, max_length=2000)


@router.get("")
async def list_approvals(
    session: Session,
    _: Operator,
    company_id: uuid.UUID,
    state: ApprovalState | None = ApprovalState.PENDING,
) -> list[ApprovalOut]:
    """The approval inbox. Oldest first, so the longest-waiting request is on top."""
    stmt = select(Approval).where(Approval.company_id == company_id)
    if state is not None:
        stmt = stmt.where(Approval.state == state)
    rows = await session.scalars(stmt.order_by(Approval.created_at, Approval.id))
    return [ApprovalOut.model_validate(row, from_attributes=True) for row in rows]


@router.post("/{approval_id}/decide")
async def decide(
    approval_id: uuid.UUID,
    body: DecisionIn,
    session: Session,
    operator: Operator,
    runtime: RuntimeDep,
) -> ApprovalOut:
    try:
        approval = await runtime.approvals.decide(
            session, approval_id, outcome=body.decision, actor=operator, reason=body.reason
        )
    except ApprovalError as exc:
        missing = "not found" in str(exc)
        code = status.HTTP_404_NOT_FOUND if missing else status.HTTP_409_CONFLICT
        raise HTTPException(code, str(exc)) from exc
    await session.commit()
    return ApprovalOut.model_validate(approval, from_attributes=True)
