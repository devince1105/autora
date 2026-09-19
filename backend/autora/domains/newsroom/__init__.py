"""AI Bilingual Newsroom domain (logs/platform/05_NEWSROOM_DOMAIN.md).

The Core knows nothing about it: it plugs in through ``autora.app`` — its models, events, policy
rules, tools, agent behaviors and simulated model — and through ``register(runtime)`` (T-514):
the workflow's service steps (approve, publish), the approval decision hook and the activity
links. Its workflow template registers with ``register_templates``.
"""

from __future__ import annotations

from typing import Protocol

from autora.domains.newsroom import workflow
from autora.domains.newsroom.activity_links import activity_links
from autora.domains.newsroom.workflow import register_templates
from autora.runtime.approvals import ApprovalService
from autora.runtime.policy import PolicyEngine
from autora.runtime.services import ServiceRegistry
from autora.runtime.task_manager import TaskManager


class RuntimeParts(Protocol):
    task_manager: TaskManager
    approvals: ApprovalService
    policy: PolicyEngine
    services: ServiceRegistry


def register(runtime: RuntimeParts) -> None:
    runtime.services.register(workflow.APPROVE, workflow.approve_step)
    runtime.services.register(workflow.PUBLISH, workflow.publish_step)
    runtime.approvals.on_decided(
        workflow.APPROVE_ACTION, workflow.on_article_decided(runtime.policy)
    )
    runtime.task_manager.link_hooks.append(activity_links)


__all__ = ["register", "register_templates"]
