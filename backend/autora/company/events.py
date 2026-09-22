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
    type: str | None = None
    """No longer written (D-019): a company is a portfolio, and the industry belongs to its
    business units. Kept and declared optional rather than deleted, because events already
    recorded carry it and a reader must be able to tell "absent" from "wrong"."""


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


# --- Opportunities and proposals (T-611, ARCHITECTURE_V2_1 §1-§2) ---------------------------


@event("OPPORTUNITY_DISCOVERED")
class OpportunityDiscovered(EventPayload):
    """Somebody noticed something that might be a business. Nothing has been spent on it yet."""

    key: str
    title: str
    thesis: str | None = None
    market: str | None = None


@event("OPPORTUNITY_SIGNAL_RECORDED")
class OpportunitySignalRecorded(EventPayload):
    """One observation attached to an opportunity. The company stores it without reading it."""

    key: str
    source: str
    summary: str
    metric: str | None = None
    value: Decimal | None = None


@event("OPPORTUNITY_SCORED")
class OpportunityScored(EventPayload):
    """A number for comparing opportunities. Not a decision — the decision is its own event."""

    key: str
    score: Decimal
    previous: Decimal | None = None
    reason: str | None = None


@event("OPPORTUNITY_ADVANCED")
class OpportunityAdvanced(EventPayload):
    key: str
    from_state: str
    to_state: str
    reason: str | None = None


@event("OPPORTUNITY_REJECTED")
class OpportunityRejected(EventPayload):
    """Kept on purpose: a company that forgets why it said no pays to find out twice."""

    key: str
    from_state: str
    reason: str


@event("OPPORTUNITY_EXPIRED")
class OpportunityExpired(EventPayload):
    """Its conclusion went stale. The market moved on while nobody decided."""

    key: str
    from_state: str


@event("PROPOSAL_DRAFTED")
class ProposalDrafted(EventPayload):
    opportunity_key: str
    version: int = Field(ge=1)
    title: str


@event("PROPOSAL_SUBMITTED")
class ProposalSubmitted(EventPayload):
    """Frozen from here: an approval must point at the exact thing that was approved."""

    opportunity_key: str
    version: int = Field(ge=1)
    estimated_startup_cost: Decimal | None = None


@event("PROPOSAL_DECIDED")
class ProposalDecided(EventPayload):
    opportunity_key: str
    version: int = Field(ge=1)
    outcome: Literal["APPROVED", "REJECTED"]
    reason: str | None = None


@event("PROPOSAL_SUPERSEDED")
class ProposalSuperseded(EventPayload):
    """A newer version of the same proposal was submitted."""

    opportunity_key: str
    version: int = Field(ge=1)
    superseded_by_version: int = Field(ge=1)


# --- Organisation (T-600) ------------------------------------------------------------------


@event("BUSINESS_UNIT_CREATED")
class BusinessUnitCreated(EventPayload):
    key: str
    name: str
    state: str
    proposal_id: uuid.UUID | None = None
    """The proposal it was opened from (T-611). None for a business a person set up by hand."""
    capital: Decimal | None = None


@event("BUSINESS_UNIT_PAUSED")
class BusinessUnitPaused(EventPayload):
    """A business stopped spending. By a rule, by the CEO, or by a person — ``trigger`` says
    which, the same way a project's pause does."""

    key: str
    name: str
    reason: str | None = None
    trigger: Literal["human", "ceo", "kill_criteria"] = "human"


@event("BUSINESS_UNIT_SCALED")
class BusinessUnitScaled(EventPayload):
    """Capital moved into (or out of) a business that is already running (T-611)."""

    key: str
    name: str
    amount: Decimal
    reason: str | None = None


@event("BUSINESS_UNIT_WOUND_DOWN")
class BusinessUnitWoundDown(EventPayload):
    """The end of a business. Irreversible, so only a person can do it."""

    key: str
    name: str
    reason: str


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
    office_zone_key: str | None = None
    business_unit_key: str | None = None
    """The same two the office needs to draw it (see ``AGENT_CREATED``): moving between
    departments can move an agent to another part of the floor and another business."""
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
    currency: str
    """The base currency (D-023)."""


class _LedgerEvent(EventPayload):
    transaction_id: uuid.UUID
    project_id: uuid.UUID | None = None
    category: str
    amount: Decimal = Field(gt=0)
    currency: str
    """Always the base (D-023). A converted row's original is on the transaction, not here."""


@event("BUDGET_WARNING")
class BudgetWarning(EventPayload):
    """Spending is close to a cap but has not hit it (platform/07 §5).

    A warning, not a block: the cost guard is what refuses a call at 100%. This exists so the
    approach is visible before the refusal, in the timeline and in the next snapshot.
    """

    budget_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    business_unit_id: uuid.UUID | None = None
    period: Literal["cycle", "day", "month"]
    spent: Decimal = Field(ge=0)
    cap: Decimal = Field(gt=0)
    ratio: float = Field(ge=0)


@event("EXPENSE_RECORDED")
class ExpenseRecorded(_LedgerEvent):
    pass


@event("REVENUE_RECORDED")
class RevenueRecorded(_LedgerEvent):
    pass


@event("CUSTOMER_ACQUIRED")
class CustomerAcquired(EventPayload):
    """Somebody started paying (T-612). The payload carries no personal data either: the
    provider's reference and what kind of customer they are is all the company keeps."""

    external_ref: str
    kind: str
    business_unit_id: uuid.UUID | None = None
    product_id: uuid.UUID | None = None


@event("CUSTOMER_CHURNED")
class CustomerChurned(EventPayload):
    external_ref: str
    kind: str
    business_unit_id: uuid.UUID | None = None
    reason: str | None = None
    days: int | None = None
    """How long they stayed. The number a business is judged by, kept where it is cheap."""


@event("SUBSCRIPTION_STARTED")
class SubscriptionStarted(EventPayload):
    """A customer began paying a price (T-701). Provider references only, as for customers."""

    customer_id: uuid.UUID
    business_unit_id: uuid.UUID
    product_id: uuid.UUID
    price_id: uuid.UUID
    state: str
    provider: str
    external_ref: str


@event("SUBSCRIPTION_STATE_CHANGED")
class SubscriptionStateChanged(EventPayload):
    customer_id: uuid.UUID
    business_unit_id: uuid.UUID
    from_state: str
    to_state: str
    reason: str | None = None


@event("PAYMENT_RECEIVED")
class PaymentReceived(EventPayload):
    """Money a provider says arrived (T-701). The ledger's REVENUE_RECORDED follows it in the
    same transaction; this one says where the money came from, that one what it counts as."""

    payment_id: uuid.UUID
    transaction_id: uuid.UUID
    customer_id: uuid.UUID
    business_unit_id: uuid.UUID
    subscription_id: uuid.UUID | None = None
    provider: str
    amount: Decimal
    currency: str


@event("KPI_SNAPSHOT_CREATED")
class KpiSnapshotCreated(EventPayload):
    snapshot_id: uuid.UUID
    cycle_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    metrics: dict[str, Any]
