"""Reporting: what each part of the company achieved, and what it cost (T-603).

Run in MEASURING, after the ledger has settled, and **never by a model**. A KPI is arithmetic
over rows that already exist, so it is reproducible, cheap to redo, and cannot be argued with.

**The core counts money; domains count their own work.** This module knows what money is and
what a cycle is. It does not know what a published article is, and it must not learn: a company
that runs two businesses would then need the core to learn both. So a domain registers a hook:

    reporting.register("newsroom", newsroom_kpis)

and gets back a window to measure. Whatever numbers it returns are stored under its own name
(``newsroom.published_articles``), while the core's own stay bare (``cost``). Reading a
metric therefore tells you who computed it — and deleting a domain deletes its metrics, leaving
the rest of the report standing.

A domain hook may use the ledger, because a ratio like "cost per published article" mixes a
domain's unit with the company's money and only the domain knows what its unit is. Layering
allows it: domains sit above the company.

Three scopes are measured every cycle: the company, each business unit, and each project that
was active. The rows are idempotent per cycle, so a MEASURING that runs twice writes once.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from autora.company import customers
from autora.company import events as company_events
from autora.company.ledger import MODEL_COST, SPENDING
from autora.db.models import (
    BusinessUnit,
    Cycle,
    KpiScope,
    KpiSnapshot,
    ModelCall,
    ModelCallStatus,
    Project,
    ProjectState,
    Transaction,
    TransactionKind,
)
from autora.infra.money import Fx
from autora.runtime.actor import Actor
from autora.runtime.events.outbox import emit
from autora.runtime.events.schema import new_event

log = logging.getLogger(__name__)

MONEY = Decimal("0.000001")


@dataclass(frozen=True)
class Window:
    """What a hook is asked to measure: a scope, and a stretch of time."""

    company_id: uuid.UUID
    since: datetime
    until: datetime
    scope: KpiScope = KpiScope.COMPANY
    business_unit_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    cycle_id: uuid.UUID | None = None


KpiHook = Callable[[AsyncSession, Window, Mapping[str, object]], Awaitable[Mapping[str, object]]]
"""Returns the domain's numbers for that window. Keys are bare; the core adds the domain's name.

The third argument is what the core already measured for the same window, so a ratio that mixes
a domain's unit with the company's money (cost per article) has both sides from one
measurement rather than from two queries that might disagree.

It is called once per scope, so a hook with nothing to say about a scope should return an empty
mapping rather than the company's numbers."""


class ReportingError(Exception):
    pass


@dataclass
class Reporting:
    """Computes KPIs and stores them. Does not commit."""

    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    hooks: dict[str, KpiHook] = field(default_factory=dict)
    fx: Fx = field(default_factory=Fx.from_settings)

    @property
    def actor(self) -> Actor:
        return Actor.system("reporting")

    def register(self, domain: str, hook: KpiHook) -> None:
        """Let a domain contribute the numbers only it can define."""
        if domain in self.hooks:
            raise ReportingError(f"{domain!r} already contributes KPIs")
        if not domain.isidentifier():
            raise ReportingError(f"{domain!r} is not usable as a metric prefix")
        self.hooks[domain] = hook

    # --- measuring --------------------------------------------------------------------------

    async def metrics_for(self, session: AsyncSession, window: Window) -> dict[str, object]:
        """The core's numbers for a window, plus every domain's, under their own names."""
        core = await self._money(session, window)
        metrics: dict[str, object] = dict(core)
        for domain, hook in self.hooks.items():
            try:
                produced = await hook(session, window, core)
            except Exception:  # noqa: BLE001 - one domain's arithmetic must not lose the report
                log.exception("KPIs from %s failed for %s", domain, window.scope)
                continue
            for key, value in produced.items():
                metrics[f"{domain}.{key}"] = _plain(value)
        return metrics

    async def measure_cycle(self, session: AsyncSession, cycle: Cycle) -> list[KpiSnapshot]:
        """Write this cycle's KPIs for the company, each business and each active project.

        Idempotent: the unique key is the cycle and the scope, so running MEASURING again
        updates the numbers in place instead of adding a second set.
        """
        since, until = cycle.started_at, cycle.ended_at or self.clock()
        windows = [
            Window(
                company_id=cycle.company_id,
                since=since,
                until=until,
                scope=KpiScope.COMPANY,
                cycle_id=cycle.id,
            )
        ]
        units = (
            await session.scalars(
                select(BusinessUnit).where(BusinessUnit.company_id == cycle.company_id)
            )
        ).all()
        windows += [
            Window(
                company_id=cycle.company_id,
                since=since,
                until=until,
                scope=KpiScope.BUSINESS_UNIT,
                business_unit_id=unit.id,
                cycle_id=cycle.id,
            )
            for unit in units
        ]
        projects = (
            await session.scalars(
                select(Project).where(
                    Project.company_id == cycle.company_id,
                    Project.state.in_([ProjectState.ACTIVE.value, ProjectState.PAUSED.value]),
                )
            )
        ).all()
        windows += [
            Window(
                company_id=cycle.company_id,
                since=since,
                until=until,
                scope=KpiScope.PROJECT,
                business_unit_id=project.business_unit_id,
                project_id=project.id,
                cycle_id=cycle.id,
            )
            for project in projects
        ]

        written = []
        for window in windows:
            written.append(await self._store(session, window))
        return written

    async def _store(self, session: AsyncSession, window: Window) -> KpiSnapshot:
        metrics = await self.metrics_for(session, window)
        existing = await session.scalar(
            select(KpiSnapshot).where(
                KpiSnapshot.cycle_id == window.cycle_id,
                KpiSnapshot.scope == window.scope.value,
                KpiSnapshot.business_unit_id.is_(window.business_unit_id)
                if window.business_unit_id is None
                else KpiSnapshot.business_unit_id == window.business_unit_id,
                KpiSnapshot.project_id.is_(window.project_id)
                if window.project_id is None
                else KpiSnapshot.project_id == window.project_id,
            )
        )
        if existing is not None:
            existing.metrics = metrics
            existing.period_end = window.until
            await session.flush()
            return existing
        snapshot = KpiSnapshot(
            company_id=window.company_id,
            cycle_id=window.cycle_id,
            business_unit_id=window.business_unit_id,
            project_id=window.project_id,
            scope=window.scope.value,
            metrics=metrics,
            period_start=window.since,
            period_end=window.until,
        )
        session.add(snapshot)
        await session.flush()
        await emit(
            session,
            new_event(
                company_events.KpiSnapshotCreated(
                    snapshot_id=snapshot.id,
                    cycle_id=window.cycle_id,
                    project_id=window.project_id,
                    metrics=metrics,
                ),
                company_id=window.company_id,
                actor=self.actor,
                aggregate_type="kpi_snapshot",
                aggregate_id=snapshot.id,
                cycle_id=window.cycle_id,
            ),
        )
        return snapshot

    def stage_hook(self):
        """Measure on the way into MEASURING — registered after the ledger, so the money it
        reports is already settled."""

        async def measure(session: AsyncSession, cycle: Cycle) -> None:
            await self.measure_cycle(session, cycle)

        return measure

    # --- the core's own numbers ---------------------------------------------------------------

    async def _money(self, session: AsyncSession, window: Window) -> dict[str, object]:
        """Cost, revenue and profit for a scope.

        Cost is counted from ``model_calls`` plus non-model expenses, never from the settled
        ``model_cost`` transactions as well — that is the same rule the dashboard follows, and
        it keeps the number right whether or not the cycle has been settled yet.
        """
        scope = self._scope_filter(window)
        model_cost = await session.scalar(
            select(func.sum(ModelCall.cost_usd)).where(
                ModelCall.company_id == window.company_id,
                ModelCall.status == ModelCallStatus.OK.value,
                ModelCall.created_at >= window.since,
                ModelCall.created_at <= window.until,
                *self._calls_filter(window),
            )
        )
        other_cost = await session.scalar(
            select(func.sum(Transaction.amount)).where(
                Transaction.company_id == window.company_id,
                Transaction.kind.in_([k.value for k in SPENDING]),
                Transaction.category != MODEL_COST,
                Transaction.occurred_at >= window.since,
                Transaction.occurred_at <= window.until,
                *scope,
            )
        )
        revenue = await session.scalar(
            select(func.sum(Transaction.amount)).where(
                Transaction.company_id == window.company_id,
                Transaction.kind == TransactionKind.REVENUE.value,
                Transaction.occurred_at >= window.since,
                Transaction.occurred_at <= window.until,
                *scope,
            )
        )
        calls = await session.scalar(
            select(func.count())
            .select_from(ModelCall)
            .where(
                ModelCall.company_id == window.company_id,
                ModelCall.created_at >= window.since,
                ModelCall.created_at <= window.until,
                *self._calls_filter(window),
            )
        )
        # the meter is in USD, the ledger in the base; the report is in the base (D-023)
        model_cost = self.fx.metered(model_cost)
        cost = (model_cost + Decimal(other_cost or 0)).quantize(MONEY)
        earned = Decimal(revenue or 0).quantize(MONEY)
        metrics = {
            "cost": _plain(cost),
            "model_cost": _plain(model_cost),
            "revenue": _plain(earned),
            "profit": _plain((earned - cost).quantize(MONEY)),
            "model_calls": int(calls or 0),
        }
        # counted for the scopes a customer can belong to; a project has no customers of its own
        if window.scope in (KpiScope.COMPANY, KpiScope.BUSINESS_UNIT):
            metrics["customers"] = await customers.paying(
                session,
                window.company_id,
                business_unit_id=window.business_unit_id,
                at=window.until,
            )
        return metrics

    def _scope_filter(self, window: Window) -> list:
        if window.scope is KpiScope.PROJECT:
            return [Transaction.project_id == window.project_id]
        if window.scope is KpiScope.BUSINESS_UNIT:
            return [Transaction.business_unit_id == window.business_unit_id]
        return []

    def _calls_filter(self, window: Window) -> list:
        if window.scope is KpiScope.PROJECT:
            return [ModelCall.project_id == window.project_id]
        if window.scope is KpiScope.BUSINESS_UNIT:
            # a call belongs to a business through its project
            return [
                ModelCall.project_id.in_(
                    select(Project.id).where(Project.business_unit_id == window.business_unit_id)
                )
            ]
        return []


# --- reading ----------------------------------------------------------------------------------


async def latest(
    session: AsyncSession,
    company_id: uuid.UUID,
    *,
    scope: KpiScope = KpiScope.COMPANY,
    business_unit_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
) -> KpiSnapshot | None:
    """The most recent measurement of a scope."""
    stmt = (
        select(KpiSnapshot)
        .where(KpiSnapshot.company_id == company_id, KpiSnapshot.scope == scope.value)
        .order_by(KpiSnapshot.period_end.desc(), KpiSnapshot.created_at.desc())
        .limit(1)
    )
    if business_unit_id is not None:
        stmt = stmt.where(KpiSnapshot.business_unit_id == business_unit_id)
    if project_id is not None:
        stmt = stmt.where(KpiSnapshot.project_id == project_id)
    return await session.scalar(stmt)


async def history(
    session: AsyncSession,
    company_id: uuid.UUID,
    *,
    scope: KpiScope = KpiScope.COMPANY,
    business_unit_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    limit: int = 7,
) -> list[KpiSnapshot]:
    """The last ``limit`` measurements, newest first — what a trend is computed from."""
    stmt = (
        select(KpiSnapshot)
        .where(KpiSnapshot.company_id == company_id, KpiSnapshot.scope == scope.value)
        .order_by(KpiSnapshot.period_end.desc())
        .limit(limit)
    )
    if business_unit_id is not None:
        stmt = stmt.where(KpiSnapshot.business_unit_id == business_unit_id)
    if project_id is not None:
        stmt = stmt.where(KpiSnapshot.project_id == project_id)
    return list(await session.scalars(stmt))


def _plain(value: object) -> object:
    """JSON keeps numbers, not Decimals. Money is stored as a string so nothing rounds it."""
    if isinstance(value, Decimal):
        return str(value)
    return value
