"""Starting workflows (T-214). The operator is the actor; the policy decision is recorded."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from autora.company.workflows import StartWorkflowError, WorkflowNotAllowed, start_workflow
from autora.db.models import Company
from autora_api.deps import Operator, RuntimeDep, Session

router = APIRouter(prefix="/api/companies/{company_id}/workflows", tags=["workflows"])


class WorkflowStart(BaseModel):
    template: str = Field(min_length=1)
    project_id: uuid.UUID
    params: dict[str, Any] = {}


class TaskOut(BaseModel):
    id: uuid.UUID
    name: str
    display_name: str
    required_role: str
    state: str
    depends_on: list[uuid.UUID]


class WorkflowRunOut(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    project_id: uuid.UUID
    template_name: str
    params: dict[str, Any]
    state: str
    created_at: datetime
    tasks: list[TaskOut]


@router.post("", status_code=status.HTTP_201_CREATED)
async def post_workflow(
    company_id: uuid.UUID,
    body: WorkflowStart,
    session: Session,
    operator: Operator,
    runtime: RuntimeDep,
) -> WorkflowRunOut:
    if await session.get(Company, company_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"company {company_id} not found")
    try:
        run, tasks = await start_workflow(
            session,
            policy=runtime.policy,
            workflows=runtime.workflows,
            company_id=company_id,
            project_id=body.project_id,
            template=body.template,
            params=body.params,
            actor=operator,
        )
    except StartWorkflowError as exc:
        missing = "not found" in str(exc)
        code = status.HTTP_404_NOT_FOUND if missing else status.HTTP_422_UNPROCESSABLE_CONTENT
        raise HTTPException(code, str(exc)) from exc
    except WorkflowNotAllowed as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    await session.commit()
    return WorkflowRunOut(
        id=run.id,
        company_id=run.company_id,
        project_id=run.project_id,
        template_name=run.template_name,
        params=run.params,
        state=run.state,
        created_at=run.created_at,
        tasks=[TaskOut.model_validate(t, from_attributes=True) for t in tasks.values()],
    )
