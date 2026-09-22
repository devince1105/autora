"""Finding out, as ordinary work (ARCHITECTURE_V2_1 §4, T-611).

The business loop has no scheduler of its own and never will. It moves inside the daily cycle,
**at most one step per cycle**, and the step it takes here is the one that was missing: when an
opportunity is being evaluated and nobody has written a proposal for it, the company starts a
project to write one.

Three things follow from "exploring is a project", and all three are the reason it is one:

- it has a **budget** — the CEO funds it with ``AllocateExplorationBudget``, and the cost guard
  holds it to that like any other work;
- its cost **lands on the opportunity** (``projects.opportunity_id``), so "what has finding out
  about this cost us" is a query, not an estimate;
- it has **kill criteria**, so an exploration that keeps spending stops itself.

**One proposal a cycle, for one opportunity.** Not a throttle bolted on: it is §4's rule that
the slow loop advances one step a day. A company that noticed five things looks at them one
day at a time, in the order its own scores put them.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autora.company.agents.strategist import PROPOSE, ROLE
from autora.company.opportunities import open_opportunities
from autora.db.models import (
    Agent,
    AgentStatus,
    BusinessProposal,
    Cycle,
    CycleStage,
    Opportunity,
    OpportunityState,
    Project,
    ProjectState,
    ProposalState,
    WorkflowRun,
)
from autora.runtime.actor import Actor
from autora.runtime.dag import NodeSpec, TemplateRegistry, WorkflowEngine, WorkflowTemplate

log = logging.getLogger(__name__)

PROPOSAL_TEMPLATE = "company.business_proposal_v1"
DEFAULT_EXPLORATION_CAP = 64
"""What one exploration may spend before it pauses itself, in the base currency (D-023).
Small: it is a document, and a company that can spend freely on finding out can fund a
business by calling it research."""

TEMPLATES = (
    WorkflowTemplate(
        name=PROPOSAL_TEMPLATE,
        nodes=(NodeSpec(PROPOSE, "提案：{title}", ROLE),),
    ),
)

LIVE_PROPOSALS = (ProposalState.DRAFT.value, ProposalState.SUBMITTED.value)


def register_templates(templates: TemplateRegistry) -> None:
    for template in TEMPLATES:
        templates.register(template)


async def exploration_project(
    session: AsyncSession, opportunity: Opportunity, *, actor: Actor
) -> Project:
    """The project that finds out about one opportunity, created on demand.

    Its kill criteria are the point: an exploration that has spent more than the cap without
    producing a proposal is paused by the ordinary rules, with no new mechanism and nobody
    watching it.
    """
    project = await session.scalar(
        select(Project).where(
            Project.company_id == opportunity.company_id,
            Project.opportunity_id == opportunity.id,
        )
    )
    if project is not None:
        return project
    project = Project(
        company_id=opportunity.company_id,
        opportunity_id=opportunity.id,
        name=f"Explore: {opportunity.title}"[:200],
        description="Finding out whether this is a business worth opening.",
        state=ProjectState.ACTIVE.value,
        kill_criteria={
            "auto_pause_if": {
                "metric": "cost",
                "op": ">",
                "value": DEFAULT_EXPLORATION_CAP,
            },
            "note": "exploring is meant to be cheap; past this it needs a decision, not more work",
        },
        approved_by=actor.as_json(),
    )
    session.add(project)
    await session.flush()
    return project


class Exploration:
    """Starts the work that turns an opportunity into a proposal. Owns no loop of its own."""

    def __init__(self, workflows: WorkflowEngine, actor: Actor | None = None):
        self.workflows = workflows
        self.actor = actor or Actor.system("exploration")

    def stage_hook(self):
        """Registered on entering EXECUTING, after the CEO's plan has been settled: the day's
        budget exists by then, which is what an exploration is supposed to spend."""

        async def explore(session: AsyncSession, cycle: Cycle) -> None:
            await self.start_one(session, cycle)

        return explore

    def install(self, cycles) -> None:
        cycles.when_entering(CycleStage.EXECUTING, self.stage_hook())

    async def start_one(self, session: AsyncSession, cycle: Cycle) -> WorkflowRun | None:
        """Write a proposal for the best opportunity that has none. Does not commit."""
        if not await self._has_strategist(session, cycle.company_id):
            return None  # nobody to write it: a real state, and not an error
        if await self._already_started(session, cycle):
            return None  # one step a cycle (§4), and the stage may be entered twice
        opportunity = await self._next(session, cycle.company_id)
        if opportunity is None:
            return None
        project = await exploration_project(session, opportunity, actor=self.actor)
        run, _ = await self.workflows.instantiate(
            session,
            PROPOSAL_TEMPLATE,
            company_id=cycle.company_id,
            project_id=project.id,
            params={"title": opportunity.title, "opportunity_id": str(opportunity.id)},
            cycle_id=cycle.id,
        )
        log.info(
            "company %s: exploring %s in cycle %s", cycle.company_id, opportunity.key, cycle.seq
        )
        return run

    async def _next(self, session: AsyncSession, company_id: uuid.UUID) -> Opportunity | None:
        """The best-scored opportunity being evaluated that nobody has written a proposal for.

        EVALUATING and not DISCOVERED: a proposal is written for something the company has
        decided is worth looking at properly, which is the CEO's call, not this hook's.
        """
        for opportunity in await open_opportunities(session, company_id):
            if opportunity.state != OpportunityState.EVALUATING.value:
                continue
            live = await session.scalar(
                select(BusinessProposal.id).where(
                    BusinessProposal.opportunity_id == opportunity.id,
                    BusinessProposal.state.in_(LIVE_PROPOSALS),
                )
            )
            if live is None:
                return opportunity
        return None

    async def _already_started(self, session: AsyncSession, cycle: Cycle) -> bool:
        return (
            await session.scalar(
                select(WorkflowRun.id).where(
                    WorkflowRun.cycle_id == cycle.id,
                    WorkflowRun.template_name == PROPOSAL_TEMPLATE,
                )
            )
        ) is not None

    async def _has_strategist(self, session: AsyncSession, company_id: uuid.UUID) -> bool:
        return (
            await session.scalar(
                select(Agent.id).where(
                    Agent.company_id == company_id,
                    Agent.role == ROLE,
                    Agent.status == AgentStatus.ACTIVE.value,
                )
            )
        ) is not None
