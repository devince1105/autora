"""Lifecycle state machines for work execution (logs/platform/02_COMPANY_MODEL.md §6).

Declared once here; TaskManager (T-202), DAG (T-203), Approvals (T-206) and AgentRunner (T-211)
move entities only through these machines, so every transition is validated and audited in
``state_transitions``.

Task notes:
- A retryable failure goes RUNNING -> READY (attempt is incremented by TaskManager); FAILED is
  only the final failure. An expired lease also returns RUNNING -> READY. A budget-blocked task
  with no attempts left goes BLOCKED_BUDGET -> FAILED when the block is released.
- Approval releases the worker: RUNNING -> WAITING_APPROVAL -> READY (re-claimed, the run
  resumes). Human/service nodes use READY -> WAITING_APPROVAL -> SUCCEEDED directly.
- Service nodes (T-514) have no run and no lease: a step done at once goes READY -> SUCCEEDED,
  one that cannot be done READY -> FAILED.

AgentRun notes:
- WAITING_APPROVAL -> RUNNING resumes the same run after approval.
- REPAIRING -> RUNNING: a repair is another model turn followed by evaluation again.
"""

from __future__ import annotations

from autora.db.models import AgentRunState, ApprovalState, TaskState, WorkflowRunState
from autora.runtime.fsm import StateMachine, transitions

T = TaskState
TASK_FSM = StateMachine(
    entity_type="task",
    states=TaskState,
    initial=T.PENDING,
    transitions=transitions(
        {
            T.PENDING: [T.READY, T.CANCELLED],
            T.READY: [
                T.RUNNING,
                T.WAITING_APPROVAL,
                T.BLOCKED_BUDGET,
                T.CANCELLED,
                T.SUCCEEDED,  # service node (no run)
                T.FAILED,  # service node (no run)
            ],
            T.RUNNING: [
                T.SUCCEEDED,
                T.FAILED,
                T.READY,
                T.WAITING_APPROVAL,
                T.BLOCKED_BUDGET,
                T.CANCELLED,
            ],
            T.WAITING_APPROVAL: [T.READY, T.SUCCEEDED, T.CANCELLED],
            T.BLOCKED_BUDGET: [T.READY, T.FAILED, T.CANCELLED],
        }
    ),
)

R = AgentRunState
AGENT_RUN_FSM = StateMachine(
    entity_type="agent_run",
    states=AgentRunState,
    initial=R.CREATED,
    transitions=transitions(
        {
            R.CREATED: [R.RUNNING, R.ABORTED],
            R.RUNNING: [R.EVALUATING, R.WAITING_APPROVAL, R.FAILED, R.ABORTED],
            R.WAITING_APPROVAL: [R.RUNNING, R.ABORTED],
            R.EVALUATING: [R.COMPLETED, R.REPAIRING, R.FAILED, R.ABORTED],
            R.REPAIRING: [R.RUNNING, R.FAILED, R.ABORTED],
        }
    ),
)

W = WorkflowRunState
WORKFLOW_RUN_FSM = StateMachine(
    entity_type="workflow_run",
    states=WorkflowRunState,
    initial=W.RUNNING,
    transitions=transitions({W.RUNNING: [W.SUCCEEDED, W.FAILED, W.CANCELLED]}),
)

P = ApprovalState
APPROVAL_FSM = StateMachine(
    entity_type="approval",
    states=ApprovalState,
    initial=P.PENDING,
    transitions=transitions({P.PENDING: [P.APPROVED, P.REJECTED, P.RETURNED, P.EXPIRED]}),
)
