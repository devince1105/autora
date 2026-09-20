"""CompanySnapshot: everything the CEO is given, and nothing else (platform/02 §3, T-603).

The CEO does not query the database. Once a cycle it is handed one JSON document, and whatever
is not in it cannot influence the decision. That makes this module the place where "what does
the company look like right now" is answered once, the same way, for the model and for the
person reading the same page.

**Organised by business, not by task** (ARCHITECTURE_V2): the portfolio is the spine, because
the decisions this document exists for are about businesses — keep funding this one, pause that
one, try a new one. Work that belongs to no business sits beside the portfolio rather than
inside it.

**It has a size limit, and it says when it hit it.** A snapshot that grows with the company
would eventually cost more than the decision is worth, and silently pushing the interesting
parts out of the context window is the worst way to lose them. So the document is measured, and
trimmed in a fixed order, and every trim is recorded in ``trimmed`` — the CEO is told its view
is partial instead of being left to assume it is complete.

What is never trimmed: the period, the capital, and the name and state of every business. A
decision made without those is not a decision.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from autora.company.ledger import Ledger
from autora.company.reporting import Reporting, history, latest
from autora.db.models import (
    Approval,
    ApprovalState,
    Budget,
    BusinessUnit,
    CompanyGoal,
    Cycle,
    GoalStatus,
    KpiScope,
    Product,
    Project,
    ProjectState,
    Task,
    TaskState,
)
from autora.db.repositories import companies as company_repo

DEFAULT_TOKEN_BUDGET = 6000
"""Big enough for a portfolio of a few businesses with their projects; small enough that the
CEO's own reasoning has room left. Overridable per company (``company.snapshot_tokens``)."""

CHARS_PER_TOKEN = 4
HUMAN_NOTES_POLICY = "company.human_notes"
STRATEGY_POLICY = "company.strategy_summary"
TOKENS_POLICY = "company.snapshot_tokens"

SnapshotHook = Callable[[AsyncSession, uuid.UUID], Awaitable[Mapping[str, Any]]]
"""A domain's contribution to the snapshot: candidate stories, open issues, whatever it is that
only that domain can put in front of a decision. Trimmed first when the document is too big."""


# --- the document ------------------------------------------------------------------------------


class Period(BaseModel):
    cycle_id: uuid.UUID | None = None
    seq: int | None = None
    stage: str | None = None
    date: datetime


class Capital(BaseModel):
    balance: Decimal
    daily_cap: Decimal | None = None
    daily_spent: Decimal
    currency: str = "USD"


class GoalLine(BaseModel):
    id: uuid.UUID
    level: str
    title: str
    metric: str
    target: Decimal
    current: Decimal | None = None
    trend_7d: list[float] | None = None
    """The metric's last few measured values, oldest first. None when nothing measured it —
    which is itself worth seeing: a goal nobody measures cannot be managed."""


class ProjectLine(BaseModel):
    id: uuid.UUID
    name: str
    state: str
    budget_remaining: Decimal | None = None
    kpis_last_cycle: dict[str, Any] = {}
    kill_criteria: dict[str, Any] | None = None
    open_tasks: int = 0
    failed_tasks: int = 0


class ProductLine(BaseModel):
    id: uuid.UUID
    key: str
    name: str
    state: str


class BusinessLine(BaseModel):
    id: uuid.UUID | None = None
    key: str | None = None
    name: str
    state: str | None = None
    mission: str | None = None
    kpis_last_cycle: dict[str, Any] = {}
    trend_7d: dict[str, list[float]] = {}
    kill_criteria: dict[str, Any] | None = None
    products: list[ProductLine] = []
    projects: list[ProjectLine] = []


class LastCycle(BaseModel):
    seq: int | None = None
    stage: str | None = None
    kpis: dict[str, Any] = {}
    failed_tasks: list[str] = []
    approvals_pending: int = 0
    planned_by: str | None = None
    """Who decided the last cycle's plan: the CEO, or the fallback when it did not (T-607)."""
    review: str | None = None
    """What the last review concluded."""
    review_missing: str | None = None
    """Why there was no review, when there was none. The CEO is told it is missing rather than
    left to assume the silence means everything went well (platform/02 §2)."""


class CompanySnapshot(BaseModel):
    """What the CEO sees. The same document the operator can read on the page."""

    company: str
    period: Period
    capital: Capital
    goals: list[GoalLine] = []
    portfolio: list[BusinessLine] = []
    """One line per business the company runs."""
    company_work: list[ProjectLine] = []
    """Projects that belong to no business: platform work, exploration (v2.1)."""
    last_cycle: LastCycle = Field(default_factory=LastCycle)
    domains: dict[str, Any] = {}
    """What each domain put in front of the decision, under its own name."""
    strategy_summary: str | None = None
    human_notes: str | None = None
    """What a person told the company to do, left in the UI. Read, never invented."""
    trimmed: list[str] = []
    """What was left out to fit the size limit. Empty means the view is complete."""
    tokens: int = 0

    def size(self) -> int:
        return len(self.model_dump_json()) // CHARS_PER_TOKEN


# --- building ----------------------------------------------------------------------------------


class SnapshotBuilder:
    """Assembles the snapshot. Reads only; never writes, never calls a model."""

    def __init__(self, reporting: Reporting, ledger: Ledger | None = None):
        self.reporting = reporting
        self.ledger = ledger or Ledger()
        self.hooks: dict[str, SnapshotHook] = {}

    def register(self, domain: str, hook: SnapshotHook) -> None:
        self.hooks[domain] = hook

    async def build(
        self,
        session: AsyncSession,
        company_id: uuid.UUID,
        *,
        cycle: Cycle | None = None,
        now: datetime | None = None,
        token_budget: int | None = None,
    ) -> CompanySnapshot:
        now = now or datetime.now(UTC)
        company = await company_repo.get_company(session, company_id)
        if company is None:
            raise ValueError(f"no company {company_id}")
        policies = await company_repo.get_policies(session, company_id)
        budget = token_budget or int(policies.get(TOKENS_POLICY, DEFAULT_TOKEN_BUDGET))

        snapshot = CompanySnapshot(
            company=company.name,
            period=await self._period(session, company_id, cycle, now),
            capital=await self._capital(session, company_id, now),
            goals=await self._goals(session, company_id),
            portfolio=await self._portfolio(session, company_id, now),
            company_work=await self._projects(session, company_id, None, now),
            last_cycle=await self._last_cycle(session, company_id, cycle),
            domains=await self._domains(session, company_id),
            strategy_summary=_text(policies.get(STRATEGY_POLICY)) or company.mission,
            human_notes=_text(policies.get(HUMAN_NOTES_POLICY)),
        )
        return _fit(snapshot, budget)

    # --- the parts ------------------------------------------------------------------------

    async def _period(
        self, session: AsyncSession, company_id: uuid.UUID, cycle: Cycle | None, now: datetime
    ) -> Period:
        cycle = cycle or await session.scalar(
            select(Cycle).where(Cycle.company_id == company_id).order_by(Cycle.seq.desc()).limit(1)
        )
        return Period(
            cycle_id=cycle.id if cycle else None,
            seq=cycle.seq if cycle else None,
            stage=cycle.stage if cycle else None,
            date=now,
        )

    async def _capital(
        self, session: AsyncSession, company_id: uuid.UUID, now: datetime
    ) -> Capital:
        day = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        cap = await session.scalar(
            select(Budget.amount).where(
                Budget.company_id == company_id,
                Budget.project_id.is_(None),
                Budget.business_unit_id.is_(None),
                Budget.period == "day",
            )
        )
        return Capital(
            balance=await self.ledger.balance(session, company_id),
            daily_cap=cap,
            daily_spent=await self.ledger.spent(session, company_id, since=day, until=now),
        )

    async def _goals(self, session: AsyncSession, company_id: uuid.UUID) -> list[GoalLine]:
        goals = await session.scalars(
            select(CompanyGoal)
            .where(
                CompanyGoal.company_id == company_id,
                CompanyGoal.status == GoalStatus.ACTIVE.value,
            )
            .order_by(CompanyGoal.deadline.asc().nulls_last(), CompanyGoal.created_at)
        )
        measured = await history(session, company_id, limit=7)
        lines = []
        for goal in goals:
            lines.append(
                GoalLine(
                    id=goal.id,
                    level=goal.level,
                    title=goal.title,
                    metric=goal.metric,
                    target=goal.target,
                    current=goal.current,
                    trend_7d=_trend(measured, goal.metric),
                )
            )
        return lines

    async def _portfolio(
        self, session: AsyncSession, company_id: uuid.UUID, now: datetime
    ) -> list[BusinessLine]:
        units = (
            await session.scalars(
                select(BusinessUnit)
                .where(BusinessUnit.company_id == company_id)
                .order_by(BusinessUnit.key)
            )
        ).all()
        lines = []
        for unit in units:
            measured = await history(
                session, company_id, scope=KpiScope.BUSINESS_UNIT, business_unit_id=unit.id
            )
            products = (
                await session.scalars(
                    select(Product).where(Product.business_unit_id == unit.id).order_by(Product.key)
                )
            ).all()
            lines.append(
                BusinessLine(
                    id=unit.id,
                    key=unit.key,
                    name=unit.name,
                    state=unit.state,
                    mission=unit.mission,
                    kpis_last_cycle=dict(measured[0].metrics) if measured else {},
                    trend_7d=_trends(measured),
                    kill_criteria=unit.kill_criteria,
                    products=[
                        ProductLine(id=p.id, key=p.key, name=p.name, state=p.state)
                        for p in products
                    ],
                    projects=await self._projects(session, company_id, unit.id, now),
                )
            )
        return lines

    async def _projects(
        self,
        session: AsyncSession,
        company_id: uuid.UUID,
        business_unit_id: uuid.UUID | None,
        now: datetime,
    ) -> list[ProjectLine]:
        stmt = select(Project).where(
            Project.company_id == company_id,
            Project.state.in_([ProjectState.ACTIVE.value, ProjectState.PAUSED.value]),
        )
        stmt = stmt.where(
            Project.business_unit_id.is_(None)
            if business_unit_id is None
            else Project.business_unit_id == business_unit_id
        )
        projects = (await session.scalars(stmt.order_by(Project.name))).all()
        lines = []
        for project in projects:
            measured = await latest(
                session, company_id, scope=KpiScope.PROJECT, project_id=project.id
            )
            budget = await session.scalar(
                select(Budget.amount).where(
                    Budget.company_id == company_id, Budget.project_id == project.id
                )
            )
            spent = await self.ledger.spent(session, company_id, project_id=project.id)
            counts = await self._task_counts(session, project.id)
            lines.append(
                ProjectLine(
                    id=project.id,
                    name=project.name,
                    state=project.state,
                    budget_remaining=(budget - spent) if budget is not None else None,
                    kpis_last_cycle=dict(measured.metrics) if measured else {},
                    kill_criteria=project.kill_criteria,
                    open_tasks=counts[0],
                    failed_tasks=counts[1],
                )
            )
        return lines

    async def _task_counts(self, session: AsyncSession, project_id: uuid.UUID) -> tuple[int, int]:
        row = (
            await session.execute(
                select(
                    func.count().filter(
                        Task.state.notin_(
                            [
                                TaskState.SUCCEEDED.value,
                                TaskState.FAILED.value,
                                TaskState.CANCELLED.value,
                            ]
                        )
                    ),
                    func.count().filter(Task.state == TaskState.FAILED.value),
                ).where(Task.project_id == project_id)
            )
        ).one()
        return int(row[0]), int(row[1])

    async def _last_cycle(
        self, session: AsyncSession, company_id: uuid.UUID, cycle: Cycle | None
    ) -> LastCycle:
        done = await session.scalar(
            select(Cycle)
            .where(Cycle.company_id == company_id, Cycle.ended_at.is_not(None))
            .order_by(Cycle.seq.desc())
            .limit(1)
        )
        pending_count = int(
            await session.scalar(
                select(func.count())
                .select_from(Approval)
                .where(
                    Approval.company_id == company_id,
                    Approval.state == ApprovalState.PENDING.value,
                )
            )
            or 0
        )
        if done is None:
            return LastCycle(approvals_pending=pending_count)
        measured = await latest(session, company_id)
        failed = (
            await session.scalars(
                select(Task.display_name).where(
                    Task.cycle_id == done.id, Task.state == TaskState.FAILED.value
                )
            )
        ).all()
        review = done.review or {}
        return LastCycle(
            seq=done.seq,
            stage=done.stage,
            kpis=dict(measured.metrics) if measured and measured.cycle_id == done.id else {},
            failed_tasks=list(failed),
            approvals_pending=pending_count,
            planned_by=(done.plan or {}).get("by"),
            review=review.get("summary"),
            review_missing=review.get("reason") if review.get("missing") else None,
        )

    async def _domains(self, session: AsyncSession, company_id: uuid.UUID) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name, hook in self.hooks.items():
            try:
                produced = await hook(session, company_id)
            except Exception:  # noqa: BLE001 - a domain's extras are never worth losing the rest
                continue
            if produced:
                out[name] = dict(produced)
        return out


# --- fitting -----------------------------------------------------------------------------------

TRIMS: list[tuple[str, Callable[[CompanySnapshot], None]]] = []


def _trim(what: str):
    def register(fn):
        TRIMS.append((what, fn))
        return fn

    return register


@_trim("domain extras")
def _drop_domains(snapshot: CompanySnapshot) -> None:
    snapshot.domains = {}


@_trim("trends")
def _drop_trends(snapshot: CompanySnapshot) -> None:
    for line in snapshot.portfolio:
        line.trend_7d = {}
    for goal in snapshot.goals:
        goal.trend_7d = None


@_trim("the projects of businesses that are not running")
def _drop_paused_projects(snapshot: CompanySnapshot) -> None:
    for line in snapshot.portfolio:
        if line.state != "ACTIVE":
            line.projects = []
            line.products = []


@_trim("project KPIs")
def _drop_project_kpis(snapshot: CompanySnapshot) -> None:
    for line in [*snapshot.portfolio, None]:
        projects = snapshot.company_work if line is None else line.projects
        for project in projects:
            project.kpis_last_cycle = {}


@_trim("the failed tasks of the last cycle")
def _drop_failed(snapshot: CompanySnapshot) -> None:
    snapshot.last_cycle.failed_tasks = snapshot.last_cycle.failed_tasks[:3]


@_trim("the projects of every business")
def _drop_all_projects(snapshot: CompanySnapshot) -> None:
    for line in snapshot.portfolio:
        line.projects = []
    snapshot.company_work = []


def _fit(snapshot: CompanySnapshot, budget: int) -> CompanySnapshot:
    """Shrink until it fits, in a fixed order, recording every step.

    The order goes from what a decision can most afford to lose to what it cannot: a domain's
    suggestions, then history, then the detail of businesses that are not running, then detail
    of the ones that are. The period, the capital and every business's name and state are never
    removed — without them there is nothing to decide with, and a snapshot that cannot carry
    them is a budget that is too small to use.
    """
    snapshot.tokens = snapshot.size()
    for what, trim in TRIMS:
        if snapshot.tokens <= budget:
            break
        trim(snapshot)
        snapshot.trimmed.append(what)
        snapshot.tokens = snapshot.size()
    return snapshot


def _trend(measured, metric: str) -> list[float] | None:
    """A goal names a metric; the measurements may carry it under a domain's name."""
    values = []
    for row in reversed(measured):
        hit = row.metrics.get(metric)
        if hit is None:
            hit = next((v for k, v in row.metrics.items() if k.rsplit(".", 1)[-1] == metric), None)
        if hit is not None:
            values.append(_number(hit))
    return values or None


def _trends(measured) -> dict[str, list[float]]:
    if not measured:
        return {}
    keys = [k for k in measured[0].metrics if isinstance(measured[0].metrics[k], int | str)]
    out = {}
    for key in keys:
        series = [_number(row.metrics[key]) for row in reversed(measured) if key in row.metrics]
        if len(series) > 1:
            out[key] = series
    return out


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _text(value: Any) -> str | None:
    if value is None:
        return None
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
