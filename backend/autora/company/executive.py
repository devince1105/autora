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

**And when the CEO fails, the day still has a plan** (T-607). A failed or timed-out planning
task is not silence: the fallback runs, ``CYCLE_PLAN_FALLBACK`` says so with the reason, and the
company carries on doing what it was doing yesterday. The fallback is deliberately the dullest
possible decision — *keep the last cycle's allocations, set no new goals* — because a day nobody
planned is a day to hold position, not a day to improvise. A company may write its own in the
policy ``company.fallback_plan``.

What the CEO decided is copied onto the cycle itself (``cycles.plan``, ``cycles.review``) as
each stage ends, so the day's decision outlives the run that made it and the next snapshot can
say whether there was one.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autora.company import events as company_events
from autora.company.agents.ceo import PLAN, REVIEW, ROLE
from autora.db.models import (
    Agent,
    AgentStatus,
    Cycle,
    CycleStage,
    Project,
    ProjectState,
    Task,
    TaskState,
    WorkflowRun,
)
from autora.db.repositories import companies as company_repo
from autora.runtime.actor import Actor
from autora.runtime.dag import NodeSpec, TemplateRegistry, WorkflowEngine, WorkflowTemplate
from autora.runtime.events.outbox import emit
from autora.runtime.events.schema import new_event
from autora.runtime.lifecycles import TASK_FSM

log = logging.getLogger(__name__)

PLAN_TEMPLATE = "company.cycle_plan_v1"
REVIEW_TEMPLATE = "company.cycle_review_v1"
FALLBACK_POLICY = "company.fallback_plan"
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
        """Wire both stages.

        The settling hook runs on entering EXECUTING — after PLANNING has ended, before any
        business's own planner — so a desk head decides inside a budget that exists, whether the
        CEO set it or the fallback did.
        """
        cycles.when_entering(CycleStage.PLANNING, self.plan_hook())
        cycles.when_entering(CycleStage.EXECUTING, self.settle_plan_hook())
        cycles.when_entering(CycleStage.REVIEWING, self.review_hook())
        cycles.when_entering(CycleStage.DONE, self.settle_review_hook())
        cycles.finishes_when(CycleStage.PLANNING, self.planning_is_done())
        cycles.finishes_when(CycleStage.REVIEWING, self.reviewing_is_done())

    # --- what the CEO decided, kept ----------------------------------------------------------

    def settle_plan_hook(self):
        """Record the plan on the cycle, or run the fallback when there is none."""

        async def settle(session: AsyncSession, cycle: Cycle) -> None:
            if cycle.plan is not None:
                return  # already settled: the stage was entered twice
            output = await self._output(session, cycle, PLAN_TEMPLATE)
            if output is not None:
                cycle.plan = {"by": "ceo", **output}
                await session.flush()
                return
            await self._fallback(session, cycle)

        return settle

    def settle_review_hook(self):
        """Record the review, or record that there was not one."""

        async def settle(session: AsyncSession, cycle: Cycle) -> None:
            if cycle.review is not None:
                return
            output = await self._output(session, cycle, REVIEW_TEMPLATE)
            if output is not None:
                cycle.review = {"by": "ceo", **output}
            else:
                # the cycle still reaches DONE; the next snapshot says the review is missing
                cycle.review = {
                    "by": None,
                    "missing": True,
                    "reason": await self._why(session, cycle, REVIEW_TEMPLATE),
                }
            await session.flush()

        return settle

    async def _fallback(self, session: AsyncSession, cycle: Cycle) -> None:
        """No plan: hold position and say so.

        Repeating yesterday is the dullest decision available, and that is the point — a day
        nobody planned is not a day to improvise. A company that wants something else writes it
        in ``company.fallback_plan``.
        """
        reason = await self._why(session, cycle, PLAN_TEMPLATE)
        policies = await company_repo.get_policies(session, cycle.company_id)
        written = policies.get(FALLBACK_POLICY)
        if isinstance(written, dict):
            plan = {"by": "fallback", "source": "policy", **written}
        else:
            previous = await session.scalar(
                select(Cycle.plan)
                .where(
                    Cycle.company_id == cycle.company_id,
                    Cycle.seq < cycle.seq,
                    Cycle.plan.is_not(None),
                )
                .order_by(Cycle.seq.desc())
                .limit(1)
            )
            plan = {
                "by": "fallback",
                "source": "last_cycle" if previous else "nothing",
                "goals": [],
                "allocations": (previous or {}).get("allocations", []),
                "rationale": (
                    "No plan was made this cycle, so the company holds the position it was "
                    "already in."
                ),
            }
        cycle.plan = plan
        await session.flush()
        await emit(
            session,
            new_event(
                company_events.CyclePlanFallback(reason=reason),
                company_id=cycle.company_id,
                actor=self.actor,
                aggregate_type="cycle",
                aggregate_id=cycle.id,
                cycle_id=cycle.id,
            ),
        )

    async def _output(self, session: AsyncSession, cycle: Cycle, template: str) -> dict | None:
        """What the CEO produced, if it did."""
        run = await self._run_for(session, cycle, template)
        if run is None:
            return None
        return await session.scalar(
            select(Task.output).where(
                Task.workflow_run_id == run.id, Task.state == TaskState.SUCCEEDED.value
            )
        )

    async def _why(self, session: AsyncSession, cycle: Cycle, template: str) -> str:
        """Why there is nothing: nobody was asked, it failed, or it ran out of time."""
        run = await self._run_for(session, cycle, template)
        if run is None:
            return "no CEO was asked"
        task = await session.scalar(select(Task).where(Task.workflow_run_id == run.id).limit(1))
        if task is None:
            return "the planning task is missing"
        if task.state == TaskState.FAILED.value:
            return f"the CEO's run failed after {task.attempt} attempt(s)"
        return f"the stage ended with the CEO's task still {task.state}"

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
