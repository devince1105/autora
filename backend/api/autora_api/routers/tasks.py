"""Task detail: the task, its input and output, and every run (attempt) it took."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from autora.db.models import AgentRun, Task
from autora_api.deps import Operator, Session

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


class TaskRunOut(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    attempt: int
    state: str
    cost_usd: Decimal
    started_at: datetime | None
    finished_at: datetime | None


class TaskDetailOut(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    project_id: uuid.UUID
    workflow_run_id: uuid.UUID | None
    name: str
    display_name: str
    required_role: str
    state: str
    depends_on: list[uuid.UUID]
    input: dict[str, Any]
    output: dict[str, Any] | None
    attempt: int
    max_attempts: int
    budget_usd: Decimal | None
    priority: int
    created_at: datetime
    updated_at: datetime
    runs: list[TaskRunOut]
    """Oldest attempt first."""


@router.get("/{task_id}")
async def get_task(task_id: uuid.UUID, session: Session, _: Operator) -> TaskDetailOut:
    task = await session.get(Task, task_id)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"task {task_id} not found")
    runs = (
        await session.scalars(
            select(AgentRun).where(AgentRun.task_id == task_id).order_by(AgentRun.attempt)
        )
    ).all()
    return TaskDetailOut.model_validate(
        {
            **{name: getattr(task, name) for name in TaskDetailOut.model_fields if name != "runs"},
            "runs": [TaskRunOut.model_validate(run, from_attributes=True) for run in runs],
        }
    )
