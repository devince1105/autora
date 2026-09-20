"""The newsroom plans its own day (T-605b).

The cycle asks the CEO what the company will spend; then it asks the desk what to spend it on.
This is the desk's half: a one-node workflow for the editor-in-chief.

**It runs at the start of EXECUTING, not during PLANNING**, and the reason is the whole point of
the split. Both planners' tasks would otherwise be created in the same transaction and become
claimable at the same moment, so the desk could choose its day before the company had set the
budget it is choosing inside. PLANNING ends only when the CEO has answered; entering EXECUTING
is therefore the first moment at which the budget is known. Which is also what a newsroom
actually does: the working day starts with the desk deciding what the day is.

EXECUTING then waits for what the desk commissioned, because its completion check is "every
workflow this cycle started has finished" — and this is one of them.

Like the CEO's, this is an ordinary workflow: claimed, traced, budgeted and interruptible by
the same machinery as every other task. And like the CEO's, a desk with nobody in the chair
starts nothing and holds nothing up.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autora.db.models import (
    Agent,
    AgentStatus,
    BusinessUnit,
    BusinessUnitState,
    Cycle,
    CycleStage,
    Project,
    ProjectState,
    Task,
    WorkflowRun,
)
from autora.domains.newsroom.agents.editor_in_chief import ROLE, TASK
from autora.domains.newsroom.organization import BUSINESS_UNIT
from autora.runtime.actor import Actor
from autora.runtime.dag import NodeSpec, TemplateRegistry, WorkflowEngine, WorkflowTemplate
from autora.runtime.lifecycles import TASK_FSM

TEMPLATE = "newsroom.editorial_plan_v1"

PLAN_TEMPLATE = WorkflowTemplate(
    name=TEMPLATE, nodes=(NodeSpec(TASK, "Plan the desk's day (cycle {seq})", ROLE),)
)


def register_template(templates: TemplateRegistry) -> None:
    templates.register(PLAN_TEMPLATE)


class EditorialPlanning:
    """Asks the desk head what to cover, and tells the cycle when it has answered."""

    def __init__(self, workflows: WorkflowEngine, actor: Actor | None = None):
        self.workflows = workflows
        self.actor = actor or Actor.system("newsroom")

    def install(self, cycles) -> None:
        cycles.when_entering(CycleStage.EXECUTING, self.plan_hook())

    def plan_hook(self):
        async def plan(session: AsyncSession, cycle: Cycle) -> None:
            if await self._run_for(session, cycle) is not None:
                return
            if not await self._has_chief(session, cycle.company_id):
                return
            project_id = await _newsroom_project(session, cycle.company_id)
            if project_id is None:
                return  # the business has no project to work in; nothing to plan
            await self.workflows.instantiate(
                session,
                TEMPLATE,
                company_id=cycle.company_id,
                project_id=project_id,
                params={"seq": cycle.seq},
                cycle_id=cycle.id,
            )

        return plan

    def planning_is_done(self):
        """Whether the desk has answered. EXECUTING's own check covers this (the desk's run is
        one of the cycle's workflows), so this is for tests and for a caller that wants to ask."""

        async def done(session: AsyncSession, cycle: Cycle) -> bool:
            run = await self._run_for(session, cycle)
            if run is None:
                return True
            states = (
                await session.scalars(select(Task.state).where(Task.workflow_run_id == run.id))
            ).all()
            terminal = {state.value for state in TASK_FSM.terminal_states()}
            return all(state in terminal for state in states)

        return done

    async def _run_for(self, session: AsyncSession, cycle: Cycle) -> WorkflowRun | None:
        return await session.scalar(
            select(WorkflowRun).where(
                WorkflowRun.cycle_id == cycle.id, WorkflowRun.template_name == TEMPLATE
            )
        )

    async def _has_chief(self, session: AsyncSession, company_id: uuid.UUID) -> bool:
        return (
            await session.scalar(
                select(Agent.id).where(
                    Agent.company_id == company_id,
                    Agent.role == ROLE,
                    Agent.status == AgentStatus.ACTIVE.value,
                )
            )
        ) is not None


async def _newsroom_project(session: AsyncSession, company_id: uuid.UUID) -> uuid.UUID | None:
    return await session.scalar(
        select(Project.id)
        .where(
            Project.company_id == company_id,
            Project.state == ProjectState.ACTIVE.value,
            Project.business_unit_id.in_(
                select(BusinessUnit.id).where(
                    BusinessUnit.company_id == company_id,
                    BusinessUnit.key == BUSINESS_UNIT,
                    BusinessUnit.state == BusinessUnitState.ACTIVE.value,
                )
            ),
        )
        .order_by(Project.created_at)
        .limit(1)
    )
