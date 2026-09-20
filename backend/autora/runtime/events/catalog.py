"""Runtime-owned events: agents, tools, tasks, workflows, approvals, policy, scheduler.

Payload shapes follow logs/3d-office/03_EVENT_MODEL.md §2.1–2.4. Company events live in
``autora.company.events``; newsroom events are registered by ``autora.domains.newsroom``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from autora.runtime.events.schema import EventPayload, Persistence, event


class _Part(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Progress(_Part):
    label: str
    current: int = Field(ge=0)
    target: int | None = Field(default=None, ge=0)


class Handoff(_Part):
    to_role: str
    task_id: uuid.UUID


class Link(_Part):
    """Where an agent's work can be seen (a domain's ``activity_links``, T-514), e.g. a draft."""

    label: str
    href: str


class ProducedRef(_Part):
    """A domain artifact created by a tool call, e.g. {type: "evidence", id: ...}."""

    type: str
    id: uuid.UUID


class UnlockedTask(_Part):
    task_id: uuid.UUID
    required_role: str


# --- Agent ---------------------------------------------------------------------------------

ThinkPhase = Literal["plan", "reason", "finalize"]
ReviewPhase = Literal["evaluate", "repair"]
WaitReason = Literal["approval", "budget", "upstream", "rate_limit"]


@event("AGENT_CREATED")
class AgentCreated(EventPayload):
    role: str
    display_name: str
    capabilities: list[str] = []
    avatar_key: str = "default"
    """Which figure the office draws; lets a reducer add the agent from this event alone."""
    department_id: uuid.UUID | None = None
    department_key: str | None = None
    """Where it works (T-600). Here for the same reason as ``avatar_key``: an agent is rebuilt
    from this event alone, so anything the office needs to draw it has to travel with it."""


@event("AGENT_PAUSED")
class AgentPaused(EventPayload):
    reason: str | None = None


@event("AGENT_RESUMED")
class AgentResumed(EventPayload):
    reason: str | None = None


@event("AGENT_RETIRED")
class AgentRetired(EventPayload):
    """The agent left the roster: no new work, and the office stops drawing it."""

    role: str
    display_name: str
    reason: str | None = None


@event("AGENT_IDLE")
class AgentIdle(EventPayload):
    """Explicit return to IDLE.

    - ``initialized``: activity row created for a new agent
    - ``failure_acknowledged``: a human acknowledged a FAILED run
    - ``waiting_cleared``: the wait ended without a new run (task cancelled)
    - ``run_ended``: the run ended without a final failure (retry scheduled, lease reclaimed
      after a worker crash, task cancelled); the agent is free again

    Not emitted when a COMPLETED display period simply expires; that is a projection rule
    (logs/3d-office/02_AGENT_STATE_MODEL.md §5).
    """

    reason: Literal["failure_acknowledged", "waiting_cleared", "initialized", "run_ended"]


@event("AGENT_RUN_STARTED")
class AgentRunStarted(EventPayload):
    attempt: int = Field(ge=1)
    task_name: str
    required_role: str
    input_summary: str | None = None


@event("AGENT_THINKING")
class AgentThinking(EventPayload):
    phase: ThinkPhase
    step_seq: int = Field(ge=0)
    links: list[Link] = []


@event("AGENT_WORKING")
class AgentWorking(EventPayload):
    tool: str
    tool_call_id: str
    step_seq: int = Field(ge=0)
    progress: Progress | None = None
    links: list[Link] = []


@event("AGENT_WAITING")
class AgentWaiting(EventPayload):
    reason: WaitReason
    approval_id: uuid.UUID | None = None
    blocked_task_id: uuid.UUID | None = None
    waiting_on_roles: list[str] = []


@event("AGENT_REVIEWING")
class AgentReviewing(EventPayload):
    phase: ReviewPhase
    attempt: int = Field(ge=1)
    issues_count: int = Field(ge=0)
    links: list[Link] = []


@event("AGENT_RUN_COMPLETED")
class AgentRunCompleted(EventPayload):
    output_summary: str | None = None
    cost_usd: Decimal = Field(ge=0)
    steps: int = Field(ge=0)
    duration_ms: int = Field(ge=0)
    handoff: list[Handoff] = []
    display_until: datetime | None = None
    links: list[Link] = []


@event("AGENT_RUN_FAILED")
class AgentRunFailed(EventPayload):
    error_class: str
    message: str
    attempt: int = Field(ge=1)
    final: bool


@event("AGENT_RUN_ABORTED")
class AgentRunAborted(EventPayload):
    reason: Literal["budget", "policy", "timeout", "human"]
    message: str | None = None
    final: bool = True
    """False: trace data only (lease reaped with attempts left, budget abort); the agent's
    activity comes from the event that follows, not from this one. Like AGENT_RUN_FAILED."""


@event("AGENT_STEP_PROGRESS", persistence=Persistence.EPHEMERAL)
class AgentStepProgress(EventPayload):
    step_seq: int = Field(ge=0)
    tokens_so_far: int = Field(ge=0)
    progress: Progress | None = None


@event("AGENT_HEARTBEAT", persistence=Persistence.EPHEMERAL)
class AgentHeartbeat(EventPayload):
    alive_at: datetime


# --- Tool ----------------------------------------------------------------------------------

SideEffect = Literal["read", "write", "irreversible"]


@event("TOOL_CALLED")
class ToolCalled(EventPayload):
    tool: str
    tool_call_id: str
    args_summary: str | None = None
    side_effect: SideEffect
    step_seq: int = Field(ge=0)


@event("TOOL_COMPLETED")
class ToolCompleted(EventPayload):
    tool: str
    tool_call_id: str
    duration_ms: int = Field(ge=0)
    result_summary: str | None = None
    cost_usd: Decimal | None = Field(default=None, ge=0)
    produced: list[ProducedRef] = []


@event("TOOL_FAILED")
class ToolFailed(EventPayload):
    tool: str
    tool_call_id: str
    error_class: str
    message: str
    will_retry: bool


@event("TOOL_DENIED")
class ToolDenied(EventPayload):
    tool: str
    tool_call_id: str
    decision: Literal["DENY", "NEEDS_APPROVAL"]
    rule_id: str
    approval_id: uuid.UUID | None = None


# --- Workflow / Task -----------------------------------------------------------------------


@event("WORKFLOW_RUN_CREATED")
class WorkflowRunCreated(EventPayload):
    template: str
    params: dict[str, Any] = {}
    project_id: uuid.UUID
    task_ids: list[uuid.UUID]


@event("WORKFLOW_RUN_EXTENDED")
class WorkflowRunExtended(EventPayload):
    """A loop added another round of tasks (T-514), e.g. a revision after the editor's review."""

    reason: str
    round: int = Field(ge=2)
    task_ids: list[uuid.UUID]


@event("WORKFLOW_RUN_COMPLETED")
class WorkflowRunCompleted(EventPayload):
    duration_ms: int = Field(ge=0)


@event("WORKFLOW_RUN_FAILED")
class WorkflowRunFailed(EventPayload):
    duration_ms: int = Field(ge=0)
    failed_task_id: uuid.UUID | None = None
    reason: str | None = None


@event("WORKFLOW_RUN_CANCELLED")
class WorkflowRunCancelled(EventPayload):
    duration_ms: int = Field(ge=0)
    reason: str


@event("TASK_CREATED")
class TaskCreated(EventPayload):
    name: str
    display_name: str
    required_role: str
    depends_on: list[uuid.UUID] = []
    workflow_run_id: uuid.UUID | None = None
    budget_usd: Decimal | None = Field(default=None, ge=0)


@event("TASK_READY")
class TaskReady(EventPayload):
    required_role: str


@event("TASK_STARTED")
class TaskStarted(EventPayload):
    run_id: uuid.UUID
    agent_id: uuid.UUID
    attempt: int = Field(ge=1)


@event("TASK_WAITING")
class TaskWaiting(EventPayload):
    reason: WaitReason
    approval_id: uuid.UUID | None = None


@event("TASK_SUCCEEDED")
class TaskSucceeded(EventPayload):
    run_id: uuid.UUID | None = None
    """None for service/human nodes that complete without an agent run."""
    output_ref: str | None = None
    unlocks: list[UnlockedTask] = []


@event("TASK_FAILED")
class TaskFailed(EventPayload):
    run_id: uuid.UUID | None = None
    final: bool
    error_class: str


@event("TASK_CANCELLED")
class TaskCancelled(EventPayload):
    reason: str


@event("TASK_BLOCKED")
class TaskBlocked(EventPayload):
    reason: Literal["budget"]


# --- Governance / Scheduler ----------------------------------------------------------------

ApprovalKind = Literal["tool_call", "command", "project", "kill", "strategy", "article"]


@event("APPROVAL_REQUESTED")
class ApprovalRequested(EventPayload):
    kind: ApprovalKind
    ref_type: str
    ref_id: uuid.UUID
    summary: str
    expires_at: datetime | None = None


@event("APPROVAL_APPROVED")
class ApprovalApproved(EventPayload):
    kind: ApprovalKind
    ref_type: str
    ref_id: uuid.UUID
    reason: str | None = None


@event("APPROVAL_REJECTED")
class ApprovalRejected(EventPayload):
    kind: ApprovalKind
    ref_type: str
    ref_id: uuid.UUID
    reason: str | None = None


@event("APPROVAL_EXPIRED")
class ApprovalExpired(EventPayload):
    kind: ApprovalKind
    ref_type: str
    ref_id: uuid.UUID


@event("POLICY_DENIED")
class PolicyDenied(EventPayload):
    action: str
    rule_id: str
    detail: str | None = None


@event("BUDGET_EXHAUSTED")
class BudgetExhausted(EventPayload):
    """Raised by the runtime cost guard when a model call would exceed a hard budget."""

    scope: Literal["company", "project", "task", "run"]
    project_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None
    limit: Decimal = Field(ge=0)
    spent: Decimal = Field(ge=0)
    """Recorded spend plus open reservations in the budget window."""
    requested: Decimal = Field(default=Decimal(0), ge=0)
    """Estimated cost of the refused call."""
    currency: str = "USD"


@event("SCHEDULE_FIRED")
class ScheduleFired(EventPayload):
    schedule_name: str
    scheduled_for: datetime
