"""The day, in a few lines (platform/02, T-607).

When a cycle reaches DONE, the company writes down what happened: what it planned, what it
produced, what it cost, what the rules did, and what the review concluded. One document, one
cycle, rewritten rather than duplicated if the stage is entered twice.

**Written by arithmetic, not by a model.** Everything in it is already a number or a recorded
decision, so the summary costs nothing, cannot be wrong about what happened, and still exists
on the days the CEO failed. A model-written summary of a day the model could not plan would be
the least trustworthy sentence in the company.

It is deliberately short. A summary nobody reads is worse than no summary, and the numbers
behind every line are one query away for anyone who wants them.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from autora.company.reporting import latest
from autora.db.models import (
    Approval,
    ApprovalState,
    Cycle,
    Document,
    DocumentKind,
    EventRecord,
    KpiScope,
    Task,
    TaskState,
    WorkflowRun,
)
from autora.infra.money import format_money

REF = "cycle"


@dataclass(frozen=True)
class Summary:
    title: str
    body: str


async def write_daily_summary(session: AsyncSession, cycle: Cycle) -> Document:
    """Write (or rewrite) the summary of one cycle. Does not commit."""
    summary = await compose(session, cycle)
    existing = await session.scalar(
        select(Document).where(
            Document.company_id == cycle.company_id,
            Document.kind == DocumentKind.DAILY_SUMMARY.value,
            Document.ref_type == REF,
            Document.ref_id == cycle.id,
        )
    )
    if existing is not None:
        existing.title, existing.body = summary.title, summary.body
        await session.flush()
        return existing
    document = Document(
        company_id=cycle.company_id,
        kind=DocumentKind.DAILY_SUMMARY.value,
        title=summary.title,
        body=summary.body,
        ref_type=REF,
        ref_id=cycle.id,
    )
    session.add(document)
    await session.flush()
    return document


def stage_hook():
    """Registered on the way into DONE, after the review has been settled."""

    async def write(session: AsyncSession, cycle: Cycle) -> None:
        await write_daily_summary(session, cycle)

    return write


async def compose(session: AsyncSession, cycle: Cycle) -> Summary:
    lines: list[str] = []

    plan = cycle.plan or {}
    lines.append(_planned(plan))

    workflows = await _workflows(session, cycle)
    failed = await _failed_tasks(session, cycle)
    lines.append(_did(workflows, failed))

    measured = await latest(session, cycle.company_id, scope=KpiScope.COMPANY)
    if measured is not None and measured.cycle_id == cycle.id:
        lines.append(_cost(measured.metrics))

    governed = await _governance_events(session, cycle)
    if governed:
        lines.append("Rules fired: " + "; ".join(governed) + ".")

    pending = await _pending_approvals(session, cycle)
    if pending:
        lines.append(f"Waiting for a person: {pending} approval(s).")

    lines.append(_reviewed(cycle.review or {}))

    return Summary(title=f"Cycle {cycle.seq}", body="\n".join(line for line in lines if line))


# --- the lines ---------------------------------------------------------------------------------


def _planned(plan: dict[str, Any]) -> str:
    if not plan:
        return "Nothing was planned and nothing recorded it — this cycle has no plan at all."
    if plan.get("by") == "fallback":
        source = plan.get("source")
        held = (
            "the previous cycle's allocations"
            if source == "last_cycle"
            else "the policy's fallback"
        )
        if source == "nothing":
            held = "nothing, because there was no previous plan to hold"
        return f"No plan was made; the company fell back to {held}."
    goals = plan.get("goals") or []
    allocations = plan.get("allocations") or []
    parts = []
    if goals:
        parts.append(
            "aimed at " + ", ".join(f"{g.get('metric')} {g.get('target')}" for g in goals[:3])
        )
    if allocations:
        total = sum((_money(a.get("amount")) or Decimal(0) for a in allocations), Decimal(0))
        parts.append(f"allocated {format_money(total)}")
    return "The CEO " + (" and ".join(parts) if parts else "planned nothing in particular") + "."


def _did(workflows: dict[str, int], failed: list[str]) -> str:
    started = sum(workflows.values())
    if not started:
        return "No work was started."
    shape = ", ".join(f"{count}× {name}" for name, count in sorted(workflows.items()))
    line = f"{started} workflow(s) ran: {shape}."
    if failed:
        line += f" {len(failed)} task(s) failed: " + ", ".join(failed[:3]) + "."
    return line


def _cost(metrics: dict[str, Any]) -> str:
    cost = _money(metrics.get("cost")) or Decimal(0)
    revenue = _money(metrics.get("revenue")) or Decimal(0)
    calls = metrics.get("model_calls") or 0
    line = f"It cost {format_money(cost)} across {calls} model call(s)"
    if revenue:
        line += f" and earned {format_money(revenue)}"
    produced = [
        f"{key.rsplit('.', 1)[-1].replace('_', ' ')} {value}"
        for key, value in sorted(metrics.items())
        if "." in key and isinstance(value, int) and value
    ]
    if produced:
        line += ". Produced: " + ", ".join(produced[:4])
    return line + "."


def _reviewed(review: dict[str, Any]) -> str:
    if not review:
        return "There is no review of this cycle."
    if review.get("missing"):
        return f"The review is missing: {review.get('reason') or 'the CEO did not answer'}."
    summary = review.get("summary")
    decisions = review.get("projects") or []
    line = f"The CEO reviewed {len(decisions)} project(s)."
    if summary:
        line += f" {summary}"
    return line


# --- the numbers -------------------------------------------------------------------------------


async def _workflows(session: AsyncSession, cycle: Cycle) -> dict[str, int]:
    rows = (
        await session.execute(
            select(WorkflowRun.template_name, func.count())
            .where(WorkflowRun.cycle_id == cycle.id)
            .group_by(WorkflowRun.template_name)
        )
    ).all()
    return {name: int(count) for name, count in rows}


async def _failed_tasks(session: AsyncSession, cycle: Cycle) -> list[str]:
    return list(
        await session.scalars(
            select(Task.display_name).where(
                Task.cycle_id == cycle.id, Task.state == TaskState.FAILED.value
            )
        )
    )


async def _governance_events(session: AsyncSession, cycle: Cycle) -> list[str]:
    rows = (
        await session.scalars(
            select(EventRecord)
            .where(
                EventRecord.cycle_id == cycle.id,
                EventRecord.event_type.in_(
                    ["PROJECT_PAUSED", "BUSINESS_UNIT_PAUSED", "AGENT_PAUSED", "BUDGET_WARNING"]
                ),
            )
            .order_by(EventRecord.seq)
        )
    ).all()
    said = []
    for event in rows:
        payload = event.payload or {}
        if event.event_type == "BUDGET_WARNING":
            said.append(f"{int(float(payload.get('ratio', 0)) * 100)}% of a budget spent")
        else:
            what = payload.get("name") or payload.get("display_name") or "something"
            said.append(f"{what} paused")
    return said


async def _pending_approvals(session: AsyncSession, cycle: Cycle) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(Approval)
            .where(
                Approval.company_id == cycle.company_id,
                Approval.state == ApprovalState.PENDING.value,
            )
        )
        or 0
    )


def _money(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        return None


async def for_cycle(
    session: AsyncSession, company_id: uuid.UUID, cycle_id: uuid.UUID
) -> Document | None:
    return await session.scalar(
        select(Document).where(
            Document.company_id == company_id,
            Document.kind == DocumentKind.DAILY_SUMMARY.value,
            Document.ref_type == REF,
            Document.ref_id == cycle_id,
        )
    )
