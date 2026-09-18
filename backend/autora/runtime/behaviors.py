"""What a role does when it runs a task: prompt, output schema, validators, tools (T-211).

The runtime knows nothing about the content of any agent (logs/platform/04_AGENT_SPEC.md §8).
A domain (or the company layer for the CEO) registers one ``AgentBehavior`` per role, or per
(role, task name) when a role runs several kinds of task. The ``agents`` row is the identity
and budget of an agent; the behavior is its job description in code.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from autora.db.models import Agent, Task
from autora.runtime.models.types import Capability


@dataclass(frozen=True)
class RunContext:
    """What hooks may know about the run they are called for."""

    company_id: uuid.UUID
    project_id: uuid.UUID
    task: Task
    agent: Agent
    run_id: uuid.UUID


Validator = Callable[[AsyncSession, RunContext, BaseModel], Awaitable[list[str]]]
"""Domain check of a parsed output; returns the issues found (empty = passed)."""
ContextHook = Callable[[AsyncSession, RunContext], Awaitable[str | None]]
"""Extra text for the first message (OBSERVE): story, evidence, recent runs... Token-bounded."""
FactsHook = Callable[[AsyncSession, RunContext, str, Mapping[str, Any]], Awaitable[dict[str, Any]]]
"""Facts the policy engine may need for a tool call, e.g. {"fact_check_passed": True}."""


class UnknownBehavior(Exception):
    pass


@dataclass(frozen=True)
class AgentBehavior:
    role: str
    capability: Capability
    """Routes the model calls of this behavior (router: role.capability -> alias)."""
    system_prompt: str
    output_model: type[BaseModel]
    """The final reply must be JSON valid against this model; validators then check it."""
    tools: tuple[str, ...] = ()
    """Tools offered to the model. The agent row's ``tools`` list, when set, narrows this."""
    validators: tuple[Validator, ...] = ()
    task_name: str | None = None
    """None: the role's default behavior for any task."""
    max_steps: int = 10
    """Model calls per run, including repairs."""
    repair_limit: int = 2
    """Evaluation failures fed back to the model before the run fails."""
    max_output_tokens: int = 4096
    prompt_version: str = "v1"
    context: ContextHook | None = None
    policy_facts: FactsHook | None = None
    summarize: Callable[[BaseModel], str] | None = None
    """One line for AGENT_RUN_COMPLETED / TASK_SUCCEEDED; defaults to the reply's first text."""

    def offered_tools(self, agent: Agent) -> list[str]:
        allowed = set(agent.tools or ())
        return [t for t in self.tools if not allowed or t in allowed]


class BehaviorRegistry:
    def __init__(self) -> None:
        self._by_key: dict[tuple[str, str | None], AgentBehavior] = {}

    def register(self, behavior: AgentBehavior) -> None:
        key = (behavior.role, behavior.task_name)
        if key in self._by_key:
            raise ValueError(f"behavior for {key} already registered")
        self._by_key[key] = behavior

    def resolve(self, role: str, task_name: str) -> AgentBehavior:
        behavior = self._by_key.get((role, task_name)) or self._by_key.get((role, None))
        if behavior is None:
            raise UnknownBehavior(f"no behavior registered for role={role!r} task={task_name!r}")
        return behavior

    def roles(self) -> list[str]:
        return sorted({role for role, _ in self._by_key})
