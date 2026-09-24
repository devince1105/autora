"""Starting workflows (T-214), and starting a failed one again (AC-9).

The operator is the actor and the policy decision is recorded. A restart is not a special
back door: it goes through the command pipeline like the CEO's own requests, so it is decided,
capped by the same per-cycle limit, and written into ``commands_log`` with who asked.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from autora.company.workflows import (
    StartWorkflowError,
    WorkflowNotAllowed,
    start_workflow,
    superseded_by,
)
from autora.db.models import (
    CommandOutcome,
    CommandRecord,
    Company,
    Task,
    TaskState,
    WorkflowRun,
    WorkflowRunState,
)
from autora_api.deps import Operator, RuntimeDep, Session

router = APIRouter(prefix="/api/companies/{company_id}/workflows", tags=["workflows"])


class FailedRunOut(BaseModel):
    """A run a person could start again, and enough about it to decide whether to."""

    id: uuid.UUID
    template_name: str
    project_id: uuid.UUID
    state: str
    created_at: datetime
    params: dict[str, Any] = {}
    failed_tasks: list[str] = []
    """The tasks that failed, by display name. What went wrong is on their own pages."""
    restarted: bool = False
    """Whether somebody already asked for it to be started again, and the company did it."""
    superseded_by: uuid.UUID | None = None
    """A later run of the same work that succeeded or is still going (D-044): restarting this one
    would do the work twice, and the restart is refused."""


class RestartOut(BaseModel):
    decision: str
    """What the pipeline decided: allow, limited, needs_approval or deny."""
    outcome: str
    reason: str | None = None
    workflow_run_id: uuid.UUID | None = None
    """The new run, when there is one."""


class WorkflowStart(BaseModel):
    template: str = Field(min_length=1)
    project_id: uuid.UUID
    params: dict[str, Any] = {}


class WorkflowTaskOut(BaseModel):
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
    tasks: list[WorkflowTaskOut]


@router.get("/failed")
async def list_failed(
    company_id: uuid.UUID,
    session: Session,
    _: Operator,
    limit: int = 20,
) -> list[FailedRunOut]:
    """Runs that ended badly and could be started again, newest first (AC-9)."""
    if await session.get(Company, company_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"company {company_id} not found")
    runs = (
        await session.scalars(
            select(WorkflowRun)
            .where(
                WorkflowRun.company_id == company_id,
                WorkflowRun.state.in_([WorkflowRunState.FAILED.value]),
            )
            .order_by(WorkflowRun.created_at.desc())
            .limit(limit)
        )
    ).all()
    out = []
    for run in runs:
        failed = (
            await session.scalars(
                select(Task.display_name).where(
                    Task.workflow_run_id == run.id, Task.state == TaskState.FAILED.value
                )
            )
        ).all()
        # asked from the record of the asking, not guessed by comparing two runs' parameters:
        # two identical runs are not evidence that one restarted the other
        asked = await session.scalar(
            select(CommandRecord.id).where(
                CommandRecord.company_id == company_id,
                CommandRecord.command == "RestartWorkflow",
                CommandRecord.outcome == CommandOutcome.DONE.value,
                CommandRecord.payload["workflow_run_id"].astext == str(run.id),
            )
        )
        later = await superseded_by(session, run)
        out.append(
            FailedRunOut(
                id=run.id,
                template_name=run.template_name,
                project_id=run.project_id,
                state=run.state,
                created_at=run.created_at,
                params=run.params or {},
                failed_tasks=list(failed),
                restarted=asked is not None,
                superseded_by=later.id if later else None,
            )
        )
    return out


@router.post("/{workflow_run_id}/restart")
async def post_restart(
    company_id: uuid.UUID,
    workflow_run_id: uuid.UUID,
    session: Session,
    operator: Operator,
    runtime: RuntimeDep,
) -> RestartOut:
    """Run a failed workflow again, from the top, as a new run (AC-9).

    The old run keeps its history: what failed and why is the reason to keep it. The company
    may still refuse — the per-cycle cap on new work applies to a person's restart as much as
    to the CEO's, because a runaway is a runaway whoever started it.
    """
    if await session.get(Company, company_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"company {company_id} not found")
    result = await runtime.commands.submit(
        session,
        "RestartWorkflow",
        {"workflow_run_id": str(workflow_run_id)},
        company_id=company_id,
        actor=operator,
        idempotency_key=f"restart:{workflow_run_id}:{uuid.uuid4().hex[:8]}",
    )
    await session.commit()
    record = result.record
    started = (record.result or {}).get("workflow_run_id")
    return RestartOut(
        decision=record.decision,
        outcome=record.outcome,
        reason=record.reason,
        workflow_run_id=uuid.UUID(started) if started else None,
    )


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
        tasks=[WorkflowTaskOut.model_validate(t, from_attributes=True) for t in tasks.values()],
    )
