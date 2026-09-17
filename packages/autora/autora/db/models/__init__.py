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
from autora.db.models.projects import Project, ProjectState
from autora.db.models.runtime import EventRecord, StateTransition

__all__ = [
    "ActivityState",
    "Agent",
    "AgentActivity",
    "AgentStatus",
    "Budget",
    "BudgetPeriod",
    "Company",
    "CompanyGoal",
    "CompanyPolicy",
    "CompanyStatus",
    "CompanyType",
    "EventRecord",
    "GoalLevel",
    "GoalStatus",
    "Project",
    "ProjectState",
    "StateTransition",
    "Transaction",
    "TransactionKind",
    "TransactionSource",
]
