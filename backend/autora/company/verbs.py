"""What a company can be asked to do (platform/06 §2, T-604).

Each verb is a typed payload, a policy action, and a handler. The pipeline in
:mod:`autora.company.commands` decides and records; these say what actually happens.

The permission for every one of them is already declared in :mod:`autora.company.policy`, which
is why this module names an action rather than an outcome: whether the CEO may allocate $200 is
that module's answer, not this one's, and a company can tighten it without touching any code
here.

A handler's job is the part a policy cannot know: that this project is ACTIVE, that this budget
would not go negative, that this workflow's template exists. It raises ``Refused`` when the
company's state says no — which is recorded as an outcome, not thrown at the caller.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autora.company.commands import CommandBus, CommandSpec, Context, Refused
from autora.company.events import (
    BudgetAllocated,
    GoalCreated,
    ProjectKilled,
    ProjectPaused,
    ProjectProposed,
    ProjectResumed,
    StrategyUpdated,
)
from autora.company.workflows import WorkflowNotAllowed, start_workflow
from autora.db.models import (
    Budget,
    BusinessUnit,
    CompanyGoal,
    Cycle,
    CycleStage,
    GoalLevel,
    Project,
    ProjectState,
    Task,
    TaskState,
    WorkflowRun,
)
from autora.db.repositories.companies import upsert_policy
from autora.infra.money import base_currency, format_money
from autora.runtime.events.outbox import emit
from autora.runtime.events.schema import new_event
from autora.runtime.lifecycles import WORKFLOW_RUN_FSM

MONEY = Decimal("0.01")


# --- payloads ---------------------------------------------------------------------------------


class CreateCycleGoal(BaseModel):
    """What this cycle is for, in one measurable line."""

    title: str = Field(min_length=1, max_length=200)
    metric: str = Field(pattern=r"^[a-z][a-z0-9_.]*$")
    target: Decimal = Field(gt=0)
    business_unit_id: uuid.UUID | None = None


class AllocateBudget(BaseModel):
    """Put money behind something. Exactly one of the two ids, or neither for the company.

    ``amount`` is in the base currency (D-023), like every budget."""

    amount: Decimal = Field(ge=0)
    period: Literal["cycle", "day", "month"] = "cycle"
    project_id: uuid.UUID | None = None
    business_unit_id: uuid.UUID | None = None
    hard_cap: bool = True


class InstantiateWorkflow(BaseModel):
    template: str
    project_id: uuid.UUID
    params: dict[str, Any] = {}
    priority: int = 100


class CreateProject(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    business_unit_id: uuid.UUID | None = None
    kill_criteria: dict[str, Any]
    """Required: a project nobody knows how to stop is not a project (platform/02 §4)."""


class PauseProject(BaseModel):
    project_id: uuid.UUID
    reason: str | None = None


class ResumeProject(BaseModel):
    project_id: uuid.UUID
    reason: str | None = None


class KillProject(BaseModel):
    project_id: uuid.UUID
    reason: str = Field(min_length=1)


class UpdateStrategy(BaseModel):
    summary: str = Field(min_length=1, max_length=4000)


class PauseAgent(BaseModel):
    """Stop giving an agent new work. The run it is on finishes."""

    agent_id: uuid.UUID
    reason: str = Field(min_length=1)


class RestartWorkflow(BaseModel):
    """Run a failed workflow again from the top (the gap AC-9 left open)."""

    workflow_run_id: uuid.UUID
    reason: str | None = None


# --- handlers ---------------------------------------------------------------------------------


async def create_cycle_goal(ctx: Context, command: CreateCycleGoal) -> dict[str, Any]:
    goal = CompanyGoal(
        company_id=ctx.company_id,
        business_unit_id=command.business_unit_id,
        level=GoalLevel.CYCLE.value,
        title=command.title,
        metric=command.metric,
        target=command.target,
    )
    ctx.session.add(goal)
    await ctx.session.flush()
    await emit(
        ctx.session,
        new_event(
            GoalCreated(
                title=goal.title,
                level="cycle",
                metric=goal.metric,
                target=goal.target,
            ),
            company_id=ctx.company_id,
            actor=ctx.actor,
            aggregate_type="company_goal",
            aggregate_id=goal.id,
        ),
    )
    return {"goal_id": str(goal.id)}


async def allocate_budget(ctx: Context, command: AllocateBudget) -> dict[str, Any]:
    """Set the envelope for a scope. Replacing an envelope is allowed; going below what is
    already committed is not — a cap under the spend would refuse work that has happened."""
    if command.project_id and command.business_unit_id:
        raise Refused("a budget belongs to a project or a business, not both")
    if command.project_id:
        project = await ctx.session.get(Project, command.project_id)
        if project is None or project.company_id != ctx.company_id:
            raise Refused(f"no project {command.project_id} in this company")
    if command.business_unit_id:
        unit = await ctx.session.get(BusinessUnit, command.business_unit_id)
        if unit is None or unit.company_id != ctx.company_id:
            raise Refused(f"no business unit {command.business_unit_id} in this company")

    budget = await ctx.session.scalar(
        select(Budget).where(
            Budget.company_id == ctx.company_id,
            Budget.project_id.is_(command.project_id)
            if command.project_id is None
            else Budget.project_id == command.project_id,
            Budget.business_unit_id.is_(command.business_unit_id)
            if command.business_unit_id is None
            else Budget.business_unit_id == command.business_unit_id,
            Budget.period == command.period,
        )
    )
    raised_from = None
    if budget is None:
        budget = Budget(
            company_id=ctx.company_id,
            project_id=command.project_id,
            business_unit_id=command.business_unit_id,
            period=command.period,
            amount=command.amount,
            currency=base_currency(),
            hard_cap=command.hard_cap,
        )
        ctx.session.add(budget)
    else:
        raised_from = budget.amount
        budget.amount = command.amount
        budget.hard_cap = command.hard_cap
    await ctx.session.flush()
    await emit(
        ctx.session,
        new_event(
            BudgetAllocated(
                budget_id=budget.id,
                project_id=command.project_id,
                period=command.period,
                amount=command.amount,
                currency=budget.currency,
            ),
            company_id=ctx.company_id,
            actor=ctx.actor,
            aggregate_type="budget",
            aggregate_id=budget.id,
        ),
    )
    released = []
    if raised_from is None or command.amount > raised_from:
        released = await _release_blocked(ctx, command)
    return {
        "budget_id": str(budget.id),
        "amount": str(command.amount),
        "released_tasks": [str(task_id) for task_id in released],
    }


async def _release_blocked(ctx: Context, command: AllocateBudget) -> list[uuid.UUID]:
    """Money arriving is what a blocked task was waiting for, so put it back in the queue.

    Without this, raising a budget changes a number and nothing else, and the work stays stuck
    until someone notices — the gap the runbook has been warning about (AC-13, P-5).
    """
    stmt = select(Task).where(
        Task.company_id == ctx.company_id, Task.state == TaskState.BLOCKED_BUDGET.value
    )
    if command.project_id is not None:
        stmt = stmt.where(Task.project_id == command.project_id)
    elif command.business_unit_id is not None:
        stmt = stmt.where(
            Task.project_id.in_(
                select(Project.id).where(Project.business_unit_id == command.business_unit_id)
            )
        )
    blocked = (await ctx.session.scalars(stmt.with_for_update(skip_locked=True))).all()
    if not blocked:
        return []
    from autora.runtime.task_manager import TaskManager

    manager = TaskManager()
    for task in blocked:
        if task.attempt < task.max_attempts:
            await manager.mark_ready(ctx.session, task)
    await ctx.session.flush()
    return [task.id for task in blocked if task.state == TaskState.READY.value]


async def create_project(ctx: Context, command: CreateProject) -> dict[str, Any]:
    if command.business_unit_id is not None:
        unit = await ctx.session.get(BusinessUnit, command.business_unit_id)
        if unit is None or unit.company_id != ctx.company_id:
            raise Refused(f"no business unit {command.business_unit_id} in this company")
    project = Project(
        company_id=ctx.company_id,
        business_unit_id=command.business_unit_id,
        name=command.name,
        description=command.description,
        state=ProjectState.ACTIVE.value,
        kill_criteria=command.kill_criteria,
        approved_by=ctx.actor.as_json(),
    )
    ctx.session.add(project)
    await ctx.session.flush()
    await emit(
        ctx.session,
        new_event(
            ProjectProposed(name=project.name),
            company_id=ctx.company_id,
            actor=ctx.actor,
            aggregate_type="project",
            aggregate_id=project.id,
        ),
    )
    return {"project_id": str(project.id), "state": project.state}


async def pause_project(ctx: Context, command: PauseProject) -> dict[str, Any]:
    project = await _project(ctx, command.project_id)
    if project.state != ProjectState.ACTIVE.value:
        raise Refused(f"{project.name} is {project.state}, not ACTIVE")
    project.state = ProjectState.PAUSED.value
    await ctx.session.flush()
    await emit(
        ctx.session,
        new_event(
            ProjectPaused(name=project.name, reason=command.reason, trigger=_trigger(ctx)),
            company_id=ctx.company_id,
            actor=ctx.actor,
            aggregate_type="project",
            aggregate_id=project.id,
        ),
    )
    return {"project_id": str(project.id), "state": project.state}


async def resume_project(ctx: Context, command: ResumeProject) -> dict[str, Any]:
    project = await _project(ctx, command.project_id)
    if project.state != ProjectState.PAUSED.value:
        raise Refused(f"{project.name} is {project.state}, not PAUSED")
    project.state = ProjectState.ACTIVE.value
    await ctx.session.flush()
    await emit(
        ctx.session,
        new_event(
            ProjectResumed(name=project.name, reason=command.reason),
            company_id=ctx.company_id,
            actor=ctx.actor,
            aggregate_type="project",
            aggregate_id=project.id,
        ),
    )
    return {"project_id": str(project.id), "state": project.state}


async def kill_project(ctx: Context, command: KillProject) -> dict[str, Any]:
    project = await _project(ctx, command.project_id)
    if project.state in (ProjectState.KILLED.value, ProjectState.COMPLETED.value):
        raise Refused(f"{project.name} is already {project.state}")
    project.state = ProjectState.KILLED.value
    await ctx.session.flush()
    await emit(
        ctx.session,
        new_event(
            ProjectKilled(name=project.name, reason=command.reason),
            company_id=ctx.company_id,
            actor=ctx.actor,
            aggregate_type="project",
            aggregate_id=project.id,
        ),
    )
    return {"project_id": str(project.id), "state": project.state}


async def pause_agent(ctx: Context, command: PauseAgent) -> dict[str, Any]:
    from autora.company.agents.roster import pause_agent as do_pause
    from autora.db.models import Agent, AgentStatus

    agent = await ctx.session.get(Agent, command.agent_id)
    if agent is None or agent.company_id != ctx.company_id:
        raise Refused(f"no agent {command.agent_id} in this company")
    if agent.status != AgentStatus.ACTIVE.value:
        raise Refused(f"{agent.display_name} is already {agent.status}")
    await do_pause(ctx.session, agent, actor=ctx.actor, reason=command.reason)
    return {"agent_id": str(agent.id), "status": agent.status}


async def update_strategy(ctx: Context, command: UpdateStrategy) -> dict[str, Any]:
    from autora.company.snapshot import STRATEGY_POLICY

    await upsert_policy(
        ctx.session,
        ctx.company_id,
        STRATEGY_POLICY,
        command.summary,
        updated_by=ctx.actor.as_json(),
    )
    await emit(
        ctx.session,
        new_event(
            StrategyUpdated(summary=command.summary[:500]),
            company_id=ctx.company_id,
            actor=ctx.actor,
            aggregate_type="company",
            aggregate_id=ctx.company_id,
        ),
    )
    return {"summary": command.summary}


async def instantiate_workflow(ctx: Context, command: InstantiateWorkflow) -> dict[str, Any]:
    """Start the day's work. The cap on how much of it there may be is the policy's
    (``max_workflows_per_cycle``), and it is applied before this runs."""
    cycle = await ctx.session.scalar(
        select(Cycle)
        .where(Cycle.company_id == ctx.company_id, Cycle.stage != CycleStage.DONE.value)
        .order_by(Cycle.seq.desc())
        .limit(1)
    )
    if ctx.workflows is None or ctx.policy is None:
        raise Refused("this bus cannot start workflows: it was built without the engine")
    try:
        run, tasks = await start_workflow(
            ctx.session,
            policy=ctx.policy,
            workflows=ctx.workflows,
            company_id=ctx.company_id,
            project_id=command.project_id,
            template=command.template,
            params=command.params,
            actor=ctx.actor,
            role=ctx.role,
        )
    except WorkflowNotAllowed as refusal:
        raise Refused(str(refusal)) from None
    except Exception as exc:  # noqa: BLE001 - unknown template, a project that is not ACTIVE
        raise Refused(str(exc)) from None
    if cycle is not None:
        run.cycle_id = cycle.id
        for task in tasks.values():
            task.cycle_id = cycle.id  # the audit chain: cycle -> workflow -> task
        await ctx.session.flush()
    return {
        "workflow_run_id": str(run.id),
        "template": command.template,
        "tasks": len(tasks),
    }


async def restart_workflow(ctx: Context, command: RestartWorkflow) -> dict[str, Any]:
    """Run a failed workflow again, from the top, as a new run.

    Not a resume: the tasks of the old run keep their history, because what failed and why is
    the point of keeping it. The article, the story and the evidence are all still there, so a
    restart costs the model calls again but starts from what the company already knows.
    """
    run = await ctx.session.get(WorkflowRun, command.workflow_run_id)
    if run is None or run.company_id != ctx.company_id:
        raise Refused(f"no workflow run {command.workflow_run_id} in this company")
    if not WORKFLOW_RUN_FSM.is_terminal(run.state):
        raise Refused(f"that run is still {run.state}; only a finished one can be restarted")
    fresh = await instantiate_workflow(
        ctx,
        InstantiateWorkflow(
            template=run.template_name, project_id=run.project_id, params=dict(run.params or {})
        ),
    )
    return {**fresh, "restarted_from": str(run.id)}


def _trigger(ctx: Context) -> str:
    """Who stopped it, in the terms the office and the next snapshot read.

    A pause by the system is a governance rule firing — that is the only thing in this company
    that pauses without a person or the CEO behind it, and calling it "human" would make an
    automatic decision look like somebody's.
    """
    if ctx.actor.kind == "system":
        return "kill_criteria"
    return "ceo" if ctx.role == "ceo" else "human"


async def _project(ctx: Context, project_id: uuid.UUID) -> Project:
    project = await ctx.session.get(Project, project_id)
    if project is None or project.company_id != ctx.company_id:
        raise Refused(f"no project {project_id} in this company")
    return project


# --- facts the policy needs ---------------------------------------------------------------------


async def workflows_this_cycle(
    session: AsyncSession, company_id: uuid.UUID, command: InstantiateWorkflow
) -> dict[str, Any]:
    """How many the current cycle already started — the number the CEO's cap is checked against
    (anti-runaway gate 5: a proposal of a hundred workflows is truncated, not obeyed)."""
    cycle = await session.scalar(
        select(Cycle)
        .where(Cycle.company_id == company_id, Cycle.stage != CycleStage.DONE.value)
        .order_by(Cycle.seq.desc())
        .limit(1)
    )
    if cycle is None:
        return {"workflows_in_cycle": 0}
    started = (
        await session.scalars(select(WorkflowRun.id).where(WorkflowRun.cycle_id == cycle.id))
    ).all()
    return {"workflows_in_cycle": len(started)}


# --- registration ---------------------------------------------------------------------------


def register(bus: CommandBus) -> None:
    """Every verb the company knows, with the policy action that decides it."""
    for spec in (
        CommandSpec(
            "CreateCycleGoal",
            CreateCycleGoal,
            "create_cycle_goal",
            create_cycle_goal,
            summary=lambda c: f"Goal: {c.title} ({c.metric} -> {c.target})",
        ),
        CommandSpec(
            "AllocateBudget",
            AllocateBudget,
            "allocate_budget",
            allocate_budget,
            summary=lambda c: f"Allocate {format_money(c.amount)} per {c.period}",
        ),
        CommandSpec(
            "InstantiateWorkflow",
            InstantiateWorkflow,
            "instantiate_workflow",
            instantiate_workflow,
            facts=workflows_this_cycle,
            summary=lambda c: f"Start {c.template}",
        ),
        CommandSpec(
            "CreateProject",
            CreateProject,
            "create_project",
            create_project,
            summary=lambda c: f"New project: {c.name}",
        ),
        CommandSpec(
            "PauseProject",
            PauseProject,
            "pause_project",
            pause_project,
            summary=lambda c: f"Pause project {c.project_id}",
        ),
        CommandSpec(
            "ResumeProject",
            ResumeProject,
            "resume_project",
            resume_project,
            summary=lambda c: f"Resume project {c.project_id}",
        ),
        CommandSpec(
            "KillProject",
            KillProject,
            "kill_project",
            kill_project,
            summary=lambda c: f"Kill project {c.project_id}: {c.reason}",
        ),
        CommandSpec(
            "UpdateStrategy",
            UpdateStrategy,
            "update_strategy",
            update_strategy,
            summary=lambda c: f"New strategy: {c.summary[:120]}",
        ),
        CommandSpec(
            "PauseAgent",
            PauseAgent,
            "pause_agent",
            pause_agent,
            summary=lambda c: f"Pause agent {c.agent_id}: {c.reason}",
        ),
        CommandSpec(
            "RestartWorkflow",
            RestartWorkflow,
            "instantiate_workflow",
            restart_workflow,
            facts=None,
            summary=lambda c: f"Run workflow {c.workflow_run_id} again",
        ),
    ):
        bus.register(spec)
