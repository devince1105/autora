"""The CEO: decides what the company spends on, not how the work is done (T-605a).

Twice a cycle it is handed one document — the CompanySnapshot — and asked a question it can
answer with money and priorities:

- **PLANNING**: what is this cycle for, and what does each business get to spend?
- **REVIEWING**: what did each business do with it, and what changes?

**It does not do the businesses' work.** It never chooses today's stories, writes, or edits;
that is the newsroom's editor-in-chief (T-605b). A CEO that picks five news topics is the thing
ARCHITECTURE_V2 was written to stop. Its only tool is ``submit_command``, so everything it
decides goes through the pipeline that can refuse it, cap it, or hold it for a person.

**The plan is a record, not a wish.** Its output says what it asked the company for, and a
validator checks that against the commands it actually submitted in that run. An agent that
writes "I allocated $20 to AI Media" without having asked is sent back — "say what you did" is
a cheap property to check and an expensive one to lose.
"""

from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autora.company.reporting import Reporting
from autora.company.snapshot import SnapshotBuilder
from autora.db.models import CommandOutcome, CommandRecord, Project, ProjectState
from autora.runtime.behaviors import AgentBehavior, BehaviorRegistry, RunContext

ROLE = "ceo"
PLAN = "plan"
REVIEW = "review"

MAX_GOALS = 3
"""A cycle with five goals has none. The cap is a prompt rule and a validator both."""


# --- what it produces --------------------------------------------------------------------------


class Goal(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    metric: str = Field(pattern=r"^[a-z][a-z0-9_.]*$")
    target: Decimal = Field(gt=0)


class Allocation(BaseModel):
    """Money put behind one scope this cycle."""

    amount: Decimal = Field(ge=0)
    business_unit_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    rationale: str = Field(min_length=1, max_length=400)


class Priority(BaseModel):
    project_id: uuid.UUID
    rank: int = Field(ge=1)
    rationale: str = Field(min_length=1, max_length=400)


class CyclePlan(BaseModel):
    """What the CEO decided this cycle is for, and what it asked the company for."""

    goals: list[Goal] = Field(default_factory=list, max_length=MAX_GOALS)
    allocations: list[Allocation] = Field(default_factory=list)
    priorities: list[Priority] = Field(default_factory=list)
    rationale: str = Field(min_length=1, max_length=2000)
    """Why this, in the CEO's own words. Read by a person, and by the next cycle's CEO."""


class ProjectDecision(BaseModel):
    project_id: uuid.UUID
    decision: str = Field(pattern="^(continue|modify|pause|kill_proposal)$")
    rationale: str = Field(min_length=1, max_length=400)


class CycleReview(BaseModel):
    """What the CEO concluded about the cycle that just ended."""

    projects: list[ProjectDecision] = Field(default_factory=list)
    summary: str = Field(min_length=1, max_length=2000)
    next_cycle_hints: dict[str, str] = Field(default_factory=dict)
    strategy_change_proposal: str | None = Field(default=None, max_length=2000)


# --- what it is told ----------------------------------------------------------------------------

SYSTEM_PROMPT = """You are the chief executive of an autonomous company.

You decide what the company is in, what each business may spend, and which work matters most.
You do NOT do the businesses' work: you never choose what to publish, write, edit or build.
Each business has its own head who decides that within the budget you give them.

You are given one document: a snapshot of the company. If something is not in it, you do not
know it — do not invent numbers, and do not assume work happened that the snapshot does not
show. When `trimmed` is not empty, your view is deliberately partial; say so in your rationale
if it affects the decision.

Your only tool is submit_command. Use it for everything you decide:
- CreateCycleGoal {title, metric, target} — at most %(max_goals)d, each measurable
- AllocateBudget {amount, period, business_unit_id|project_id} — money for one scope
- PauseProject {project_id, reason} — stop work that is not paying for itself
- KillProject {project_id, reason} — a person must approve it
- UpdateStrategy {summary} — a person must approve it

The company may refuse you, cap you, or say a person must approve. Read the decision in each
result. A refusal is an answer, not a failure: adapt and continue.

Then write your plan. It must say what you actually asked for — the commands you submitted are
checked against it. Do not write an allocation you did not submit.

Be brief. A cycle with three clear goals beats one with ten."""


REVIEW_PROMPT = """You are the chief executive of an autonomous company, reviewing the cycle
that just ended.

You are given a snapshot of the company. Judge each business and project by what it cost and
what it returned, using only the numbers in front of you.

For each project decide one of: continue, modify, pause, kill_proposal. Pausing is yours to do
(submit PauseProject); proposing to kill is a recommendation a person must approve.

Automatic pausing by kill criteria is not your job and happens without you — do not repeat it,
and do not argue with it.

Write a short summary a person can read in ten seconds, and hints for the next cycle. Say what
you did not know: if the snapshot was trimmed, or a number was missing, that belongs in the
summary."""


# --- what it must not get wrong -------------------------------------------------------------


async def goals_are_measurable(
    session: AsyncSession, ctx: RunContext, output: BaseModel
) -> list[str]:
    plan = output
    issues = []
    if isinstance(plan, CyclePlan) and len(plan.goals) > MAX_GOALS:
        issues.append(f"{len(plan.goals)} goals is more than the {MAX_GOALS} a cycle can have")
    return issues


async def projects_are_real_and_running(
    session: AsyncSession, ctx: RunContext, output: BaseModel
) -> list[str]:
    """Every project named must exist in this company, and be one work can happen in."""
    named: list[uuid.UUID] = []
    if isinstance(output, CyclePlan):
        named = [p.project_id for p in output.priorities]
        named += [a.project_id for a in output.allocations if a.project_id]
    elif isinstance(output, CycleReview):
        named = [p.project_id for p in output.projects]
    if not named:
        return []
    rows = {
        row.id: row
        for row in (
            await session.scalars(
                select(Project).where(Project.company_id == ctx.company_id, Project.id.in_(named))
            )
        ).all()
    }
    issues = []
    for project_id in named:
        project = rows.get(project_id)
        if project is None:
            issues.append(f"project {project_id} is not one of this company's")
        elif project.state in (ProjectState.KILLED.value, ProjectState.COMPLETED.value):
            issues.append(f"project {project.name} is {project.state}; it takes no decisions now")
    return issues


async def said_what_it_did(session: AsyncSession, ctx: RunContext, output: BaseModel) -> list[str]:
    """The plan has to match the commands this run submitted.

    The cheapest lie an agent can tell is to describe a decision it never asked for, and it is
    the most expensive one to discover later — the company would look like it had a budget it
    never allocated. So the written plan is checked against the record of what was submitted.
    """
    if not isinstance(output, CyclePlan):
        return []
    submitted = (
        await session.scalars(
            select(CommandRecord).where(
                CommandRecord.run_id == ctx.run_id,
                CommandRecord.outcome != CommandOutcome.REFUSED.value,
            )
        )
    ).all()
    by_command: dict[str, list[dict]] = {}
    for record in submitted:
        by_command.setdefault(record.command, []).append(record.payload or {})

    issues = []
    for goal in output.goals:
        asked = by_command.get("CreateCycleGoal", [])
        if not any(payload.get("metric") == goal.metric for payload in asked):
            issues.append(
                f"the plan claims a goal for {goal.metric!r} but no CreateCycleGoal was submitted "
                "for it in this run"
            )
    for allocation in output.allocations:
        if not any(
            _same_money(payload.get("amount"), allocation.amount)
            and str(payload.get("business_unit_id") or "") == str(allocation.business_unit_id or "")
            and str(payload.get("project_id") or "") == str(allocation.project_id or "")
            for payload in by_command.get("AllocateBudget", [])
        ):
            issues.append(
                f"the plan claims an allocation of {allocation.amount} but no matching "
                "AllocateBudget was submitted in this run"
            )
    return issues


def _same_money(left: object, right: Decimal) -> bool:
    try:
        return Decimal(str(left)) == right
    except (InvalidOperation, TypeError):
        return False


# --- what it is given -------------------------------------------------------------------------


def snapshot_context(snapshots: SnapshotBuilder):
    """OBSERVE: the company, as one document. Nothing else is fetched for it."""

    async def context(session: AsyncSession, ctx: RunContext) -> str | None:
        snapshot = await snapshots.build(session, ctx.company_id)
        return "The company right now:\n" + snapshot.model_dump_json(indent=2, exclude_none=True)

    return context


def behaviors(snapshots: SnapshotBuilder | None = None) -> tuple[AgentBehavior, ...]:
    """The CEO's two jobs. ``snapshots`` is what gives it its view of the company."""
    builder = snapshots or SnapshotBuilder(Reporting())
    context = snapshot_context(builder)
    return (
        AgentBehavior(
            role=ROLE,
            task_name=PLAN,
            capability="reasoning",
            system_prompt=SYSTEM_PROMPT % {"max_goals": MAX_GOALS},
            output_model=CyclePlan,
            tools=("submit_command",),
            validators=(goals_are_measurable, projects_are_real_and_running, said_what_it_did),
            max_steps=6,
            repair_limit=2,
            max_output_tokens=4096,
            context=context,
            summarize=_plan_summary,
        ),
        AgentBehavior(
            role=ROLE,
            task_name=REVIEW,
            capability="reasoning",
            system_prompt=REVIEW_PROMPT,
            output_model=CycleReview,
            tools=("submit_command",),
            validators=(projects_are_real_and_running,),
            max_steps=6,
            repair_limit=2,
            max_output_tokens=4096,
            context=context,
            summarize=_review_summary,
        ),
    )


def register_behaviors(
    registry: BehaviorRegistry, snapshots: SnapshotBuilder | None = None
) -> None:
    for behavior in behaviors(snapshots):
        registry.register(behavior)


def _plan_summary(plan: BaseModel) -> str:
    assert isinstance(plan, CyclePlan)
    parts = []
    if plan.goals:
        parts.append(f"{len(plan.goals)} goal(s): " + ", ".join(g.metric for g in plan.goals))
    if plan.allocations:
        total = sum((a.amount for a in plan.allocations), Decimal(0))
        parts.append(f"${total} allocated")
    if plan.priorities:
        parts.append(f"{len(plan.priorities)} project(s) prioritised")
    return "; ".join(parts) or "nothing to do this cycle"


def _review_summary(review: BaseModel) -> str:
    assert isinstance(review, CycleReview)
    decisions: dict[str, int] = {}
    for project in review.projects:
        decisions[project.decision] = decisions.get(project.decision, 0) + 1
    shape = ", ".join(f"{count} {decision}" for decision, count in sorted(decisions.items()))
    return shape or "nothing to decide"
