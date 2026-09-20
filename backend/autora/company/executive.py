"""How the CEO gets asked (T-605a).

The CEO is an agent like any other, which means it works the only way agents work here: a task
is created, the worker claims it, the runner runs it. Nothing about the runtime is special-cased
for it — and that is deliberate, because a company whose executive runs on its own private
machinery is a company whose executive cannot be watched, traced, budgeted or paused like
everybody else.

So planning is a one-node workflow, and the cycle starts it:

    PLANNING entered  -> instantiate company.cycle_plan_v1   -> the CEO plans
    PLANNING finished -> when that task is terminal
    REVIEWING entered -> instantiate company.cycle_review_v1 -> the CEO reviews

**A cycle does not wait forever for its CEO.** PLANNING has a deadline like every stage; if the
CEO is slow, stuck, or absent, the stage times out and the day goes on without a plan. A company
that stops because its executive is thinking is worse than one that has an unplanned day.

**A company with no CEO still cycles.** When nobody holds the role, no task is created and the
stage finishes at once — the honest shape of a company that has not hired one.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autora.company.agents.ceo import PLAN, REVIEW, ROLE
from autora.db.models import (
    Agent,
    AgentStatus,
    Cycle,
    CycleStage,
    Project,
    ProjectState,
    Task,
    WorkflowRun,
)
from autora.runtime.actor import Actor
from autora.runtime.dag import NodeSpec, TemplateRegistry, WorkflowEngine, WorkflowTemplate
from autora.runtime.lifecycles import TASK_FSM

log = logging.getLogger(__name__)

PLAN_TEMPLATE = "company.cycle_plan_v1"
REVIEW_TEMPLATE = "company.cycle_review_v1"
OPERATIONS = "Company operations"
"""The project the company's own work belongs to. The CEO's runs cost money like anything
else, and money has to land somewhere that can be reported on."""

TEMPLATES = (
    WorkflowTemplate(name=PLAN_TEMPLATE, nodes=(NodeSpec(PLAN, "Plan cycle {seq}", ROLE),)),
    WorkflowTemplate(name=REVIEW_TEMPLATE, nodes=(NodeSpec(REVIEW, "Review cycle {seq}", ROLE),)),
)


def register_templates(templates: TemplateRegistry) -> None:
    for template in TEMPLATES:
        templates.register(template)


async def operations_project(
    session: AsyncSession, company_id: uuid.UUID, *, actor: Actor
) -> Project:
    """The company's own project, created on demand.

    Not a workaround for a non-nullable column: the executive's work really is work with a
    cost, and giving it a project is what makes "what did running this company cost" a question
    with an answer, separate from what its businesses cost.
    """
    project = await session.scalar(
        select(Project).where(Project.company_id == company_id, Project.name == OPERATIONS)
    )
    if project is not None:
        return project
    project = Project(
        company_id=company_id,
        name=OPERATIONS,
        description="Running the company itself: planning, review, and the executive's own work.",
        state=ProjectState.ACTIVE.value,
        kill_criteria={"note": "the company's own work; it ends when the company does"},
        approved_by=actor.as_json(),
    )
    session.add(project)
    await session.flush()
    return project


class Executive:
    """Starts the CEO's work when a cycle needs it, and tells the cycle when it is done."""

    def __init__(self, workflows: WorkflowEngine, actor: Actor | None = None):
        self.workflows = workflows
        self.actor = actor or Actor.system("executive")

    # --- the hooks the cycle runs ----------------------------------------------------------

    def plan_hook(self):
        async def plan(session: AsyncSession, cycle: Cycle) -> None:
            await self._start(session, cycle, PLAN_TEMPLATE)

        return plan

    def review_hook(self):
        async def review(session: AsyncSession, cycle: Cycle) -> None:
            await self._start(session, cycle, REVIEW_TEMPLATE)

        return review

    def planning_is_done(self):
        """PLANNING ends when the CEO's task has finished — or at once when there is no CEO."""

        async def done(session: AsyncSession, cycle: Cycle) -> bool:
            return await self._finished(session, cycle, PLAN_TEMPLATE)

        return done

    def reviewing_is_done(self):
        async def done(session: AsyncSession, cycle: Cycle) -> bool:
            return await self._finished(session, cycle, REVIEW_TEMPLATE)

        return done

    def install(self, cycles) -> None:
        """Wire both stages. The plan hook runs before any business's own planner, so a unit
        head decides inside the budget the CEO just set, not beside it."""
        cycles.when_entering(CycleStage.PLANNING, self.plan_hook())
        cycles.when_entering(CycleStage.REVIEWING, self.review_hook())
        cycles.finishes_when(CycleStage.PLANNING, self.planning_is_done())
        cycles.finishes_when(CycleStage.REVIEWING, self.reviewing_is_done())

    # --- doing it --------------------------------------------------------------------------

    async def _start(self, session: AsyncSession, cycle: Cycle, template: str) -> None:
        if await self._run_for(session, cycle, template) is not None:
            return  # the stage was entered twice; one plan is enough
        if not await self._has_ceo(session, cycle.company_id):
            log.info("company %s has no active CEO; the cycle plans nothing", cycle.company_id)
            return
        project = await operations_project(session, cycle.company_id, actor=self.actor)
        await self.workflows.instantiate(
            session,
            template,
            company_id=cycle.company_id,
            project_id=project.id,
            params={"seq": cycle.seq},
            cycle_id=cycle.id,
        )

    async def _finished(self, session: AsyncSession, cycle: Cycle, template: str) -> bool:
        run = await self._run_for(session, cycle, template)
        if run is None:
            return True  # nothing was started, so there is nothing to wait for
        states = (
            await session.scalars(select(Task.state).where(Task.workflow_run_id == run.id))
        ).all()
        terminal = {state.value for state in TASK_FSM.terminal_states()}
        return all(state in terminal for state in states)

    async def _run_for(
        self, session: AsyncSession, cycle: Cycle, template: str
    ) -> WorkflowRun | None:
        return await session.scalar(
            select(WorkflowRun).where(
                WorkflowRun.cycle_id == cycle.id, WorkflowRun.template_name == template
            )
        )

    async def _has_ceo(self, session: AsyncSession, company_id: uuid.UUID) -> bool:
        return (
            await session.scalar(
                select(Agent.id).where(
                    Agent.company_id == company_id,
                    Agent.role == ROLE,
                    Agent.status == AgentStatus.ACTIVE.value,
                )
            )
        ) is not None
