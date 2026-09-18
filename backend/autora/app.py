"""Composition root: the only module allowed to import every layer (runtime, company, realtime,
domains). Processes (api, worker) and tools (schema codegen) assemble the system from here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autora.runtime.approvals import ApprovalService
    from autora.runtime.dag import TemplateRegistry, WorkflowEngine
    from autora.runtime.policy import PolicyEngine
    from autora.runtime.task_manager import TaskManager


def load_event_catalogs() -> None:
    """Import every module that registers event payloads, so the registry is complete."""
    import autora.company.events  # noqa: F401
    import autora.runtime.events  # noqa: F401

    # Domains register their events here once they exist (Phase 5: autora.domains.newsroom).


def build_policy_engine():
    """The policy engine with every layer's rules registered."""
    from autora.company import policy as company_policy
    from autora.domains.newsroom import policy as newsroom_policy
    from autora.runtime.policy import PolicyEngine

    engine = PolicyEngine()
    company_policy.register(engine)
    newsroom_policy.register(engine)
    return engine


@dataclass(frozen=True)
class Runtime:
    """The wired-together runtime services a process needs (api, worker)."""

    task_manager: TaskManager
    templates: TemplateRegistry
    workflows: WorkflowEngine
    approvals: ApprovalService
    policy: PolicyEngine


def build_runtime() -> Runtime:
    from autora.runtime.approvals import ApprovalService
    from autora.runtime.dag import TemplateRegistry, WorkflowEngine
    from autora.runtime.task_manager import TaskManager

    load_event_catalogs()
    task_manager = TaskManager()
    templates = TemplateRegistry()
    # Domains register their workflow templates here (Phase 5: newsroom.story_to_article_v2).
    workflows = WorkflowEngine(task_manager, templates)
    return Runtime(
        task_manager=task_manager,
        templates=templates,
        workflows=workflows,
        approvals=ApprovalService(task_manager),
        policy=build_policy_engine(),
    )
