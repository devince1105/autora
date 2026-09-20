"""Company events: identity, organisation, goals, policy, projects, cycles, budgets, ledger, KPIs.

Payload shapes follow logs/platform/11_EVENT_CATALOG.md. The acting human/agent/system is
carried on the envelope (``actor``), so payloads do not repeat it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import Field

from autora.runtime.events.schema import EventPayload, event

CycleStage = Literal["PLANNING", "EXECUTING", "MEASURING", "REVIEWING", "DONE"]


# --- Company / goals / policy --------------------------------------------------------------


@event("COMPANY_CREATED")
class CompanyCreated(EventPayload):
    slug: str
    name: str
    type: str


@event("GOAL_CREATED")
class GoalCreated(EventPayload):
    title: str
    level: Literal["annual", "quarter", "cycle"]
    metric: str
    target: Decimal
    deadline: datetime | None = None
    parent_goal_id: uuid.UUID | None = None


@event("GOAL_UPDATED")
class GoalUpdated(EventPayload):
    current: Decimal | None = None
    target: Decimal | None = None
    status: Literal["active", "achieved", "missed", "cancelled"] | None = None


@event("POLICY_UPDATED")
class PolicyUpdated(EventPayload):
    key: str
    value: Any
    previous: Any = None


@event("STRATEGY_UPDATED")
class StrategyUpdated(EventPayload):
    summary: str
    approval_id: uuid.UUID | None = None


# --- Projects ------------------------------------------------------------------------------


class _ProjectEvent(EventPayload):
    name: str
    reason: str | None = None


@event("PROJECT_PROPOSED")
class ProjectProposed(_ProjectEvent):
    pass


@event("PROJECT_APPROVED")
class ProjectApproved(_ProjectEvent):
    pass


@event("PROJECT_REJECTED")
class ProjectRejected(_ProjectEvent):
    pass


@event("PROJECT_PAUSED")
class ProjectPaused(_ProjectEvent):
    trigger: Literal["human", "ceo", "kill_criteria"] = "human"


@event("PROJECT_RESUMED")
class ProjectResumed(_ProjectEvent):
    pass


@event("PROJECT_KILL_PROPOSED")
class ProjectKillProposed(_ProjectEvent):
    approval_id: uuid.UUID | None = None


@event("PROJECT_KILLED")
class ProjectKilled(_ProjectEvent):
    pass


@event("PROJECT_COMPLETED")
class ProjectCompleted(_ProjectEvent):
    pass


# --- Cycle ---------------------------------------------------------------------------------


@event("CYCLE_STARTED")
class CycleStarted(EventPayload):
    seq: int = Field(ge=1)
    stage: CycleStage
    deadline: datetime | None = None


@event("CYCLE_STAGE_CHANGED")
class CycleStageChanged(EventPayload):
    from_stage: CycleStage
    to_stage: CycleStage
    deadline: datetime | None = None


@event("CYCLE_STAGE_TIMEOUT")
class CycleStageTimeout(EventPayload):
    stage: CycleStage
    cancelled_task_ids: list[uuid.UUID] = []


@event("CYCLE_PLAN_FALLBACK")
class CyclePlanFallback(EventPayload):
    reason: str


@event("CYCLE_REVIEWED")
class CycleReviewed(EventPayload):
    summary: str


@event("CYCLE_COMPLETED")
class CycleCompleted(EventPayload):
    seq: int = Field(ge=1)


# --- Organisation (T-600) ------------------------------------------------------------------


@event("BUSINESS_UNIT_CREATED")
class BusinessUnitCreated(EventPayload):
    key: str
    name: str
    state: str


@event("DEPARTMENT_CREATED")
class DepartmentCreated(EventPayload):
    key: str
    name: str
    business_unit_id: uuid.UUID | None = None
    parent_department_id: uuid.UUID | None = None


@event("AGENT_ASSIGNED")
class AgentAssigned(EventPayload):
    """An agent took up a position: its role, and the department that comes with it.

    The realtime reducer rebuilds an agent from ``AGENT_CREATED`` alone, so this is the only
    thing that can move a drawn agent to another room without a full snapshot reload.
    """

    role: str
    role_id: uuid.UUID
    department_id: uuid.UUID
    department_key: str | None = None
    previous_role: str | None = None
    previous_department_id: uuid.UUID | None = None


@event("PRODUCT_CREATED")
class ProductCreated(EventPayload):
    key: str
    name: str
    business_unit_id: uuid.UUID
    state: str


# --- Budget / ledger / KPI -----------------------------------------------------------------


@event("BUDGET_ALLOCATED")
class BudgetAllocated(EventPayload):
    budget_id: uuid.UUID
    project_id: uuid.UUID | None = None
    period: Literal["cycle", "day", "month"]
    amount: Decimal = Field(ge=0)
    currency: str = "USD"


class _LedgerEvent(EventPayload):
    transaction_id: uuid.UUID
    project_id: uuid.UUID | None = None
    category: str
    amount: Decimal = Field(gt=0)
    currency: str = "USD"


@event("EXPENSE_RECORDED")
class ExpenseRecorded(_LedgerEvent):
    pass


@event("REVENUE_RECORDED")
class RevenueRecorded(_LedgerEvent):
    pass


@event("KPI_SNAPSHOT_CREATED")
class KpiSnapshotCreated(EventPayload):
    snapshot_id: uuid.UUID
    cycle_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    metrics: dict[str, Any]
