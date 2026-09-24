"""When the finance agent is asked (T-705).

Like the CEO (``executive``), the finance agent works the only way agents work here: a one-node
workflow is started, the worker claims the task, the runner runs it — so its runs are traced,
budgeted and pausable like everybody else's, and its cost lands on the company's own operations
project.

It is asked once a cycle, **after the cycle is measured**:

    MEASURING entered -> memberships lapse, the ledger settles, reporting writes the KPIs
                      -> instantiate company.budget_review_v1 -> the finance agent reviews
    MEASURING ends    -> when that task is terminal (or the stage's deadline passes)
    REVIEWING         -> the CEO reviews; the proposals wait in the approvals inbox for a
                         person, and the CEO's snapshot counts them among approvals_pending

Installed after reporting's hook on purpose: the review reads the numbers the report has just
stored, and a review of last cycle's numbers would be a review of the wrong day.

**A company with no finance agent still cycles.** Nobody is asked, no task is created, and
MEASURING ends as it always did — at once.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autora.company.agents.finance import REVIEW_BUDGETS, ROLE
from autora.company.executive import operations_project
from autora.db.models import Agent, AgentStatus, Cycle, CycleStage, Task, WorkflowRun
from autora.runtime.actor import Actor
from autora.runtime.dag import NodeSpec, TemplateRegistry, WorkflowEngine, WorkflowTemplate
from autora.runtime.lifecycles import TASK_FSM

log = logging.getLogger(__name__)

TEMPLATE = "company.budget_review_v1"

TEMPLATES = (
    WorkflowTemplate(
        name=TEMPLATE, nodes=(NodeSpec(REVIEW_BUDGETS, "Review budgets after cycle {seq}", ROLE),)
    ),
)


def register_templates(templates: TemplateRegistry) -> None:
    for template in TEMPLATES:
        templates.register(template)


class FinanceDesk:
    """Starts the budget review once a cycle is measured; tells MEASURING when it is done."""

    def __init__(self, workflows: WorkflowEngine, actor: Actor | None = None):
        self.workflows = workflows
        self.actor = actor or Actor.system("finance-desk")

    def review_hook(self):
        async def review(session: AsyncSession, cycle: Cycle) -> None:
            await self._start(session, cycle)

        return review

    def measuring_is_done(self):
        """MEASURING ends when the review has finished — or at once when nobody was asked."""

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

    def install(self, cycles) -> None:
        """Wire the review into MEASURING. Call after reporting's hook is installed."""
        cycles.when_entering(CycleStage.MEASURING, self.review_hook())
        cycles.finishes_when(CycleStage.MEASURING, self.measuring_is_done())

    async def _start(self, session: AsyncSession, cycle: Cycle) -> None:
        if await self._run_for(session, cycle) is not None:
            return  # the stage was entered twice; one review is enough
        if not await self._has_finance(session, cycle.company_id):
            log.info("company %s has no active finance agent; no budget review", cycle.company_id)
            return
        project = await operations_project(session, cycle.company_id, actor=self.actor)
        await self.workflows.instantiate(
            session,
            TEMPLATE,
            company_id=cycle.company_id,
            project_id=project.id,
            params={"seq": cycle.seq},
            cycle_id=cycle.id,
        )

    async def _run_for(self, session: AsyncSession, cycle: Cycle) -> WorkflowRun | None:
        return await session.scalar(
            select(WorkflowRun).where(
                WorkflowRun.cycle_id == cycle.id, WorkflowRun.template_name == TEMPLATE
            )
        )

    async def _has_finance(self, session: AsyncSession, company_id: uuid.UUID) -> bool:
        return (
            await session.scalar(
                select(Agent.id).where(
                    Agent.company_id == company_id,
                    Agent.role == ROLE,
                    Agent.status == AgentStatus.ACTIVE.value,
                )
            )
        ) is not None
