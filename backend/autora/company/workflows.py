"""Starting a workflow: the ``instantiate_workflow`` command (T-214).

Humans start workflows through the API; the CEO agent will start them through the same function
(Phase 6), where its policy rule (max workflows per cycle) applies. Either way the decision is
recorded in ``policy_decisions`` before anything is created.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from autora.db.models import Project, ProjectState, Task, WorkflowRun
from autora.db.repositories.companies import get_policies
from autora.runtime.actor import Actor
from autora.runtime.dag import WorkflowEngine, WorkflowError
from autora.runtime.policy import PolicyEngine


class StartWorkflowError(Exception):
    """The request cannot start a workflow (unknown template or project, bad parameters)."""


class WorkflowNotAllowed(Exception):
    def __init__(self, outcome: str, reason: str):
        self.outcome = outcome
        self.reason = reason
        super().__init__(f"instantiate_workflow {outcome}: {reason}")


async def start_workflow(
    session: AsyncSession,
    *,
    policy: PolicyEngine,
    workflows: WorkflowEngine,
    company_id: uuid.UUID,
    project_id: uuid.UUID,
    template: str,
    params: dict[str, Any],
    actor: Actor,
    role: str | None = None,
    facts: dict[str, Any] | None = None,
) -> tuple[WorkflowRun, dict[str, Task]]:
    """Check, record and instantiate. ``role`` and ``facts`` matter for agent actors only."""
    project = await session.get(Project, project_id)
    if project is None or project.company_id != company_id:
        raise StartWorkflowError(f"project {project_id} not found in company {company_id}")
    if project.state != ProjectState.ACTIVE:
        raise StartWorkflowError(
            f"project {project_id} is {project.state}; work runs in ACTIVE ones"
        )
    if template not in workflows.templates.names():
        raise StartWorkflowError(f"unknown workflow template {template!r}")

    decision = await policy.decide_and_record(
        session,
        actor,
        "instantiate_workflow",
        company_id=company_id,
        role=role,
        args={"template": template, "project_id": str(project_id), "params": params},
        facts=facts,
        company_policies=await get_policies(session, company_id),
    )
    if decision.outcome != "allow":
        raise WorkflowNotAllowed(decision.outcome, decision.reason)

    try:
        return await workflows.instantiate(
            session, template, company_id=company_id, project_id=project_id, params=params
        )
    except WorkflowError as exc:
        raise StartWorkflowError(str(exc)) from exc
