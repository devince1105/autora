"""All SQLAlchemy models. Importing this package registers every table on ``Base.metadata``
(Alembic's env.py relies on that)."""

from autora.db.models.agents import ActivityState, Agent, AgentActivity, AgentStatus
from autora.db.models.company import (
    Company,
    CompanyGoal,
    CompanyPolicy,
    CompanyStatus,
    CompanyType,
    GoalLevel,
    GoalStatus,
)
from autora.db.models.finance import (
    Budget,
    BudgetPeriod,
    Transaction,
    TransactionKind,
    TransactionSource,
)
from autora.db.models.model_calls import CostReservation, ModelCall, ModelCallStatus
from autora.db.models.projects import Project, ProjectState
from autora.db.models.runtime import (
    Approval,
    ApprovalKind,
    ApprovalState,
    EventRecord,
    PolicyDecision,
    Schedule,
    StateTransition,
)
from autora.db.models.tasks import (
    AGENT_RUN_TERMINAL,
    AgentRun,
    AgentRunState,
    AgentStep,
    StepKind,
    Task,
    TaskState,
    WorkflowRun,
    WorkflowRunState,
)

__all__ = [
    "AGENT_RUN_TERMINAL",
    "ActivityState",
    "Agent",
    "AgentActivity",
    "AgentRun",
    "AgentRunState",
    "AgentStep",
    "AgentStatus",
    "Approval",
    "ApprovalKind",
    "ApprovalState",
    "Budget",
    "BudgetPeriod",
    "Company",
    "CompanyGoal",
    "CompanyPolicy",
    "CompanyStatus",
    "CompanyType",
    "CostReservation",
    "EventRecord",
    "GoalLevel",
    "GoalStatus",
    "ModelCall",
    "ModelCallStatus",
    "PolicyDecision",
    "Project",
    "ProjectState",
    "Schedule",
    "StateTransition",
    "StepKind",
    "Task",
    "TaskState",
    "Transaction",
    "TransactionKind",
    "TransactionSource",
    "WorkflowRun",
    "WorkflowRunState",
]
