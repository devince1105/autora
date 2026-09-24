"""When the business agent looks at the market (T-706).

Once a week, inside the daily cycle — the business loop has no scheduler of its own and never
will (ARCHITECTURE_V2_1 §4). On entering EXECUTING, if the company has an active business agent
and nobody has looked in the last seven days, a one-node workflow is started; EXECUTING already
waits for every workflow its cycle started, so the look is part of the day's work.

    EXECUTING entered -> no look in 7 days? -> instantiate company.market_watch_v1
                      -> the business agent searches, captures, writes opportunities down
    REVIEWING         -> the CEO finds them among its open opportunities, and decides

**Looking is a project, like exploring.** Its cost lands on the company's "Market watch"
project, so "what has looking cost us" is a query; and the project's kill criteria stop a look
that spends more than a cycle's exploration cap. A paused project is a decision somebody has to
undo: until they do, nobody looks.

**A company with no business agent still cycles**, exactly as before.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autora.company.agents.business import ROLE, WATCH_MARKET
from autora.company.exploration import DEFAULT_EXPLORATION_CAP
from autora.db.models import (
    Agent,
    AgentStatus,
    Cycle,
    CycleStage,
    Project,
    ProjectState,
    WorkflowRun,
    WorkflowRunState,
)
from autora.runtime.actor import Actor
from autora.runtime.dag import NodeSpec, TemplateRegistry, WorkflowEngine, WorkflowTemplate

log = logging.getLogger(__name__)

TEMPLATE = "company.market_watch_v1"
PROJECT = "Market watch"
EVERY = timedelta(days=7)
"""How often it looks (platform/04 §7: weekly). The market does not change daily, and a look
that searches and reads pages costs more than a day's review."""
SLACK = timedelta(hours=12)
"""Cycles start at slightly different minutes each day; without this, a look that started a
minute later last week would push this week's to the day after."""

TEMPLATES = (
    WorkflowTemplate(name=TEMPLATE, nodes=(NodeSpec(WATCH_MARKET, "市場觀察：第 {seq} 期", ROLE),)),
)


def register_templates(templates: TemplateRegistry) -> None:
    for template in TEMPLATES:
        templates.register(template)


async def market_watch_project(
    session: AsyncSession, company_id: uuid.UUID, *, actor: Actor
) -> Project:
    """The project that pays for looking, created on demand."""
    project = await session.scalar(
        select(Project).where(Project.company_id == company_id, Project.name == PROJECT)
    )
    if project is not None:
        return project
    project = Project(
        company_id=company_id,
        name=PROJECT,
        description="Looking for what the company could do next: the business agent's weekly look.",
        state=ProjectState.ACTIVE.value,
        kill_criteria={
            "auto_pause_if": {"metric": "cost", "op": ">", "value": DEFAULT_EXPLORATION_CAP},
            "note": "looking is meant to be cheap; a look that costs more needs a decision",
        },
        approved_by=actor.as_json(),
    )
    session.add(project)
    await session.flush()
    return project


class MarketWatchDesk:
    """Starts the weekly look on entering EXECUTING. Owns no loop of its own."""

    def __init__(self, workflows: WorkflowEngine, actor: Actor | None = None):
        self.workflows = workflows
        self.actor = actor or Actor.system("market-watch")

    def stage_hook(self):
        async def look(session: AsyncSession, cycle: Cycle) -> None:
            await self.start(session, cycle)

        return look

    def install(self, cycles) -> None:
        cycles.when_entering(CycleStage.EXECUTING, self.stage_hook())

    async def start(self, session: AsyncSession, cycle: Cycle) -> WorkflowRun | None:
        """Start this week's look, unless there is nobody to do it or it was done. No commit."""
        if not await self._has_business_agent(session, cycle.company_id):
            return None  # nobody to look: a real state, and not an error
        if await self._looked_recently(session, cycle):
            return None  # once a week; and the stage may be entered twice
        project = await market_watch_project(session, cycle.company_id, actor=self.actor)
        if project.state != ProjectState.ACTIVE.value:
            log.info("company %s: market watch is %s; not looking", cycle.company_id, project.state)
            return None
        run, _ = await self.workflows.instantiate(
            session,
            TEMPLATE,
            company_id=cycle.company_id,
            project_id=project.id,
            params={"seq": cycle.seq},
            cycle_id=cycle.id,
        )
        return run

    async def _looked_recently(self, session: AsyncSession, cycle: Cycle) -> bool:
        """A look started in the last week counts, unless it failed: an outage should not cost
        the company a week."""
        return (
            await session.scalar(
                select(WorkflowRun.id).where(
                    WorkflowRun.company_id == cycle.company_id,
                    WorkflowRun.template_name == TEMPLATE,
                    WorkflowRun.state.not_in(
                        (WorkflowRunState.FAILED.value, WorkflowRunState.CANCELLED.value)
                    ),
                    WorkflowRun.created_at > cycle.started_at - EVERY + SLACK,
                )
            )
        ) is not None

    async def _has_business_agent(self, session: AsyncSession, company_id: uuid.UUID) -> bool:
        return (
            await session.scalar(
                select(Agent.id).where(
                    Agent.company_id == company_id,
                    Agent.role == ROLE,
                    Agent.status == AgentStatus.ACTIVE.value,
                )
            )
        ) is not None
