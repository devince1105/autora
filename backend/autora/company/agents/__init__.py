"""The company's people: who it employs, and the agents that run the company itself.

``roster`` is hiring, pausing and letting go — the company as an employer. ``ceo`` is an agent
that decides what the company does with what it employs. They live together because both answer
to the company rather than to a business, and both must keep working when every domain is
deleted (ARCHITECTURE_V2_1 §9).
"""

from autora.company.agents.ceo import behaviors, register_behaviors
from autora.company.agents.roster import (
    AgentBusy,
    WrongCompany,
    hire_agent,
    pause_agent,
    resume_agent,
    retire_agent,
)

__all__ = [
    "AgentBusy",
    "WrongCompany",
    "behaviors",
    "hire_agent",
    "pause_agent",
    "register_behaviors",
    "resume_agent",
    "retire_agent",
]
