"""The company's days: what it planned, what it did, and what it concluded (T-608).

Two views, because there are two questions. The list answers "how have the last few days gone"
— one line each, enough to see a pattern. The detail answers "what happened on this one", and
it is assembled from the things that already exist rather than from a narrative: the plan the
CEO recorded, the KPIs Reporting measured, the workflows that ran, the summary written at the
end, and the events in order.

Nothing here is generated. If a day has no plan, this says so, because the fallback recorded
why (T-607) and a page that quietly showed an empty plan would hide the interesting part.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select

from autora.company.summary import for_cycle
from autora.db.models import (
    Company,
    Cycle,
    KpiScope,
    KpiSnapshot,
    Task,
    TaskState,
    WorkflowRun,
)
from autora.infra.money import base_currency
from autora.runtime.events.outbox import to_envelope
from autora.runtime.events.schema import EventEnvelope
from autora_api.deps import Operator, Session

router = APIRouter(tags=["cycles"])

TIMELINE_LIMIT = 200


class CycleGoal(BaseModel):
    """A target the cycle was aimed at, and where it got to."""

    metric: str
    target: float | None = None
    current: float | None = None
    title: str | None = None


class CycleLine(BaseModel):
    id: uuid.UUID
    seq: int
    stage: str
    started_at: datetime
    ended_at: datetime | None
    stage_deadline: datetime | None
    planned_by: str | None = None
    """"ceo" when the CEO planned it, "fallback" when nobody did (T-607)."""
    goals: list[CycleGoal] = []
    review: str | None = None
    review_missing: str | None = None
    workflows: int = 0
    failed_tasks: int = 0
    cost: str | None = None
    """What the cycle cost, in ``currency`` — the base (D-023)."""
    currency: str


class CycleDetail(CycleLine):
    plan: dict[str, Any] | None = None
    review_detail: dict[str, Any] | None = None
    governance: dict[str, Any] | None = None
    """What the deterministic rules looked at that day, and what they did (T-610). Null on a
    cycle that never reached REVIEWING."""
    kpis: dict[str, Any] = {}
    summary: str | None = None
    """The daily summary, as written at the end of the cycle."""
    timeline: list[EventEnvelope] = []


@router.get("/api/companies/{company_id}/cycles")
async def list_cycles(
    company_id: uuid.UUID,
    session: Session,
    _: Operator,
    limit: int = Query(default=14, ge=1, le=60),
) -> list[CycleLine]:
    """The company's recent days, newest first."""
    if await session.get(Company, company_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"company {company_id} not found")
    cycles = (
        await session.scalars(
            select(Cycle)
            .where(Cycle.company_id == company_id)
            .order_by(Cycle.seq.desc())
            .limit(limit)
        )
    ).all()
    return [await _line(session, cycle) for cycle in cycles]


@router.get("/api/cycles/{cycle_id}")
async def get_cycle(cycle_id: uuid.UUID, session: Session, _: Operator) -> CycleDetail:
    """One day, in full: the plan, the numbers, the work, the summary and the timeline."""
    cycle = await session.get(Cycle, cycle_id)
    if cycle is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"cycle {cycle_id} not found")
    line = await _line(session, cycle)
    measured = await session.scalar(
        select(KpiSnapshot).where(
            KpiSnapshot.cycle_id == cycle.id, KpiSnapshot.scope == KpiScope.COMPANY.value
        )
    )
    document = await for_cycle(session, cycle.company_id, cycle.id)
    review = cycle.review or {}
    return CycleDetail(
        **line.model_dump(),
        plan=cycle.plan,
        review_detail=review or None,
        governance=cycle.governance,
        kpis=dict(measured.metrics) if measured else {},
        summary=document.body if document else None,
        timeline=await _timeline(session, cycle),
    )


# --- assembling ---------------------------------------------------------------------------------


async def _line(session: Session, cycle: Cycle) -> CycleLine:
    plan = cycle.plan or {}
    review = cycle.review or {}
    workflows = int(
        await session.scalar(
            select(func.count()).select_from(WorkflowRun).where(WorkflowRun.cycle_id == cycle.id)
        )
        or 0
    )
    failed = int(
        await session.scalar(
            select(func.count())
            .select_from(Task)
            .where(Task.cycle_id == cycle.id, Task.state == TaskState.FAILED.value)
        )
        or 0
    )
    measured = await session.scalar(
        select(KpiSnapshot).where(
            KpiSnapshot.cycle_id == cycle.id, KpiSnapshot.scope == KpiScope.COMPANY.value
        )
    )
    metrics = dict(measured.metrics) if measured else {}
    return CycleLine(
        id=cycle.id,
        seq=cycle.seq,
        stage=cycle.stage,
        started_at=cycle.started_at,
        ended_at=cycle.ended_at,
        stage_deadline=cycle.stage_deadline,
        planned_by=plan.get("by"),
        goals=_goals(plan, metrics),
        review=review.get("summary"),
        review_missing=review.get("reason") if review.get("missing") else None,
        workflows=workflows,
        failed_tasks=failed,
        cost=metrics.get("cost"),
        currency=base_currency(),
    )


def _goals(plan: dict[str, Any], metrics: dict[str, Any]) -> list[CycleGoal]:
    """Each goal with the number that was actually reached, when something measured it.

    This is what AC-12 asks for: the target comes from the plan, the progress from the real
    count — never from the plan's own idea of how it went.
    """
    lines = []
    for goal in plan.get("goals") or []:
        metric = str(goal.get("metric") or "")
        if not metric:
            continue
        lines.append(
            CycleGoal(
                metric=metric,
                target=_number(goal.get("target")),
                current=_number(_measured_for(metric, metrics)),
                title=goal.get("title"),
            )
        )
    return lines


def _measured_for(metric: str, metrics: dict[str, Any]) -> Any:
    """A goal names a metric; a measurement may carry it under a domain's name."""
    if metric in metrics:
        return metrics[metric]
    return next((value for key, value in metrics.items() if key.rsplit(".", 1)[-1] == metric), None)


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def _timeline(session: Session, cycle: Cycle) -> list[EventEnvelope]:
    from autora.db.models import EventRecord

    rows = (
        await session.scalars(
            select(EventRecord)
            .where(EventRecord.cycle_id == cycle.id)
            .order_by(EventRecord.seq)
            .limit(TIMELINE_LIMIT)
        )
    ).all()
    return [to_envelope(row) for row in rows]
