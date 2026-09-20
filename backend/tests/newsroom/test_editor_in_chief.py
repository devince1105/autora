"""T-605b: the editor-in-chief — what it may commission, and what it may not claim.

The point of this agent is the thing the v2 revision was written for: the company decides what
the newsroom may spend, and the newsroom decides what to spend it on. Neither does the other's
job, and the tests below are mostly about that line holding.
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from autora.app import build_policy_engine, build_runtime
from autora.company.agents.roster import hire_agent
from autora.company.organization import role_by_key
from autora.db.models import (
    AgentStatus,
    BusinessUnit,
    Cycle,
    CycleStage,
    Project,
    ProjectState,
    Task,
    WorkflowRun,
)
from autora.domains.newsroom import organization as newsroom_org
from autora.domains.newsroom.agents.editor_in_chief import (
    BEHAVIOR,
    MAX_STORIES,
    ROLE,
    TASK,
    Commission,
    EditorialPlan,
    commissioned_what_it_listed,
    desk_context,
    stories_are_the_company_s_candidates,
    the_count_matches,
)
from autora.domains.newsroom.models import Story, StoryState
from autora.domains.newsroom.planning import TEMPLATE, EditorialPlanning
from autora.domains.newsroom.tools.commission import register as register_commission
from autora.runtime.actor import Actor
from autora.runtime.behaviors import RunContext
from autora.runtime.tools import ToolRegistry
from tests.conftest import unique_company

HUMAN = Actor.human("founder")


async def _desk(db_session, *, with_chief: bool = True, stories: int = 3):
    """A newsroom with candidates on the desk, and a head for it."""
    company = await unique_company(db_session, "chief")
    await newsroom_org.build(db_session, company.id, actor=HUMAN)
    unit = await db_session.scalar(
        select(BusinessUnit).where(
            BusinessUnit.company_id == company.id,
            BusinessUnit.key == newsroom_org.BUSINESS_UNIT,
        )
    )
    project = Project(
        company_id=company.id,
        business_unit_id=unit.id,
        name="Lumen Daily",
        state=ProjectState.ACTIVE.value,
        kill_criteria={"max_cost_usd": 5},
    )
    db_session.add(project)
    await db_session.flush()
    candidates = []
    for n in range(stories):
        story = Story(
            company_id=company.id,
            project_id=project.id,
            title=f"Story {n}",
            summary=f"what story {n} is about",
            state=StoryState.DISCOVERED.value,
            score=Decimal(f"0.{9 - n}"),
            sources_count=3,
        )
        db_session.add(story)
        candidates.append(story)
    chief = None
    if with_chief:
        position = await role_by_key(db_session, company.id, ROLE)
        chief = await hire_agent(
            db_session, company_id=company.id, role=ROLE, display_name="Edda",
            actor=HUMAN, position=position,
        )  # fmt: skip
    await db_session.flush()
    return company, project, candidates, chief


def _ctx(company, project, chief):
    return RunContext(
        company_id=company.id,
        project_id=project.id,
        task=None,
        agent=chief,
        run_id=uuid.uuid4(),
    )


# --- what it is ----------------------------------------------------------------------------------


def test_the_chief_commissions_and_nothing_else():
    """The whole v2 point: the desk head chooses, the desk writes."""
    assert (BEHAVIOR.role, BEHAVIOR.task_name) == (ROLE, TASK)
    assert BEHAVIOR.tools == ("commission_story",)
    assert "You do not write,\nedit or fact-check" in BEHAVIOR.system_prompt
    assert "an empty day is a decision, not a failure" in BEHAVIOR.system_prompt


async def test_the_desk_is_shown_its_candidates_and_what_it_may_spend(db_session):
    company, project, candidates, chief = await _desk(db_session)

    context = await desk_context(db_session, _ctx(company, project, chief))

    assert "Candidate stories (best first):" in context
    assert str(candidates[0].id) in context and "Story 0" in context
    assert context.index("Story 0") < context.index("Story 2")  # best first
    assert "Articles published so far: 0" in context
    assert "no budget of its own" in context  # the CEO has not allocated yet


async def test_a_desk_with_nothing_on_it_says_so(db_session):
    company, project, _, chief = await _desk(db_session, stories=0)

    context = await desk_context(db_session, _ctx(company, project, chief))

    assert "nothing has been gathered that is worth covering" in context


# --- what it may not claim --------------------------------------------------------------------


async def test_a_plan_may_not_list_a_story_it_never_commissioned(db_session):
    """Otherwise the company believes three articles are coming and nobody is writing them."""
    company, project, candidates, chief = await _desk(db_session)
    plan = EditorialPlan(
        stories=[Commission(story_id=candidates[0].id, priority=1)],
        target_articles=1,
        rationale="I said I would",
    )

    issues = await commissioned_what_it_listed(db_session, _ctx(company, project, chief), plan)

    assert len(issues) == 1 and "never commissioned" in issues[0]


async def test_a_plan_that_matches_the_desk_passes(db_session):
    company, project, candidates, chief = await _desk(db_session)
    candidates[0].state = StoryState.IN_PRODUCTION.value
    await db_session.flush()
    plan = EditorialPlan(
        stories=[Commission(story_id=candidates[0].id, priority=1)],
        target_articles=1,
        rationale="well sourced",
    )

    assert await commissioned_what_it_listed(db_session, _ctx(company, project, chief), plan) == []


async def test_a_story_already_published_cannot_be_covered_again(db_session):
    company, project, candidates, chief = await _desk(db_session)
    candidates[0].state = StoryState.PUBLISHED.value
    await db_session.flush()
    plan = EditorialPlan(
        stories=[Commission(story_id=candidates[0].id, priority=1)],
        target_articles=1,
        rationale="again",
    )

    issues = await stories_are_the_company_s_candidates(
        db_session, _ctx(company, project, chief), plan
    )

    assert any("already PUBLISHED" in i for i in issues)


async def test_another_newsroom_s_story_is_not_this_desk_s(db_session):
    company, project, candidates, chief = await _desk(db_session)
    other, _, theirs, _ = await _desk(db_session, with_chief=False)
    plan = EditorialPlan(
        stories=[Commission(story_id=theirs[0].id, priority=1)],
        target_articles=1,
        rationale="not mine",
    )

    issues = await stories_are_the_company_s_candidates(
        db_session, _ctx(company, project, chief), plan
    )

    assert any("not one of this newsroom's" in i for i in issues)


async def test_the_number_it_promises_is_the_number_it_chose(db_session):
    company, project, candidates, chief = await _desk(db_session)
    plan = EditorialPlan(
        stories=[Commission(story_id=candidates[0].id, priority=1)],
        target_articles=3,
        rationale="optimistic",
    )

    issues = await the_count_matches(db_session, _ctx(company, project, chief), plan)

    assert any("have to be the same number" in i for i in issues)


def test_a_desk_cannot_take_more_than_it_can_carry():
    with pytest.raises(Exception, match="at most"):
        EditorialPlan(
            stories=[Commission(story_id=uuid.uuid4(), priority=1) for _ in range(MAX_STORIES + 1)],
            target_articles=MAX_STORIES + 1,
            rationale="too many",
        )


# --- commissioning ----------------------------------------------------------------------------


async def test_commissioning_puts_the_whole_desk_to_work(committed):
    """One decision, and the line starts: research, analysis, draft, review, publication."""
    async with committed() as session:
        company, project, candidates, chief = await _desk(session)
        await session.commit()
        story_id, company_id, agent_id = candidates[0].id, company.id, chief.id

    runtime = build_runtime()
    tools = ToolRegistry(committed)
    register_commission(tools, runtime.policy, runtime.workflows)

    invocation = await tools.invoke(
        "commission_story",
        {"story_id": str(story_id), "angle": "what it means for residents"},
        company_id=company_id,
        actor=Actor.agent(agent_id),
        tool_call_id="call_1",
        agent_id=agent_id,
    )

    assert invocation.ok, invocation
    assert invocation.output["ok"] is True
    async with committed() as session:
        story = await session.get(Story, story_id)
        assert story.state == StoryState.IN_PRODUCTION.value
        assert story.angle == "what it means for residents"
        run = await session.get(WorkflowRun, uuid.UUID(invocation.output["workflow_run_id"]))
        assert run.template_name == "newsroom.story_to_article_v2"
        tasks = (
            await session.scalars(select(Task.name).where(Task.workflow_run_id == run.id))
        ).all()
        assert "research" in tasks and "draft" in tasks


async def test_a_story_already_in_production_is_not_commissioned_twice(committed):
    async with committed() as session:
        company, project, candidates, chief = await _desk(session)
        candidates[0].state = StoryState.IN_PRODUCTION.value
        await session.commit()
        story_id, company_id, agent_id = candidates[0].id, company.id, chief.id

    runtime = build_runtime()
    tools = ToolRegistry(committed)
    register_commission(tools, runtime.policy, runtime.workflows)

    invocation = await tools.invoke(
        "commission_story", {"story_id": str(story_id)}, company_id=company_id,
        actor=Actor.agent(agent_id), tool_call_id="call_1", agent_id=agent_id,
    )  # fmt: skip

    assert invocation.ok and invocation.output["ok"] is False
    assert "already IN_PRODUCTION" in invocation.output["reason"]


async def test_commissioning_something_that_is_not_this_newsroom_s(committed):
    async with committed() as session:
        company, project, candidates, chief = await _desk(session)
        await session.commit()
        company_id, agent_id = company.id, chief.id

    runtime = build_runtime()
    tools = ToolRegistry(committed)
    register_commission(tools, runtime.policy, runtime.workflows)

    invocation = await tools.invoke(
        "commission_story", {"story_id": str(uuid.uuid4())}, company_id=company_id,
        actor=Actor.agent(agent_id), tool_call_id="call_1", agent_id=agent_id,
    )  # fmt: skip

    assert invocation.ok and invocation.output["ok"] is False
    assert "no story" in invocation.output["reason"]


# --- how it is asked --------------------------------------------------------------------------


async def test_planning_asks_the_desk_after_the_company(db_session):
    company, project, candidates, chief = await _desk(db_session)
    runtime = build_runtime()
    cycle = Cycle(company_id=company.id, seq=1, stage=CycleStage.PLANNING.value)
    db_session.add(cycle)
    await db_session.flush()

    await EditorialPlanning(runtime.workflows).plan_hook()(db_session, cycle)

    run = await db_session.scalar(
        select(WorkflowRun).where(
            WorkflowRun.cycle_id == cycle.id, WorkflowRun.template_name == TEMPLATE
        )
    )
    assert run is not None and run.project_id == project.id
    (task,) = (await db_session.scalars(select(Task).where(Task.workflow_run_id == run.id))).all()
    assert (task.required_role, task.name) == (ROLE, TASK)


async def test_a_desk_with_no_head_plans_nothing_and_holds_nothing_up(db_session):
    company, project, candidates, _ = await _desk(db_session, with_chief=False)
    runtime = build_runtime()
    planning = EditorialPlanning(runtime.workflows)
    cycle = Cycle(company_id=company.id, seq=1, stage=CycleStage.PLANNING.value)
    db_session.add(cycle)
    await db_session.flush()

    await planning.plan_hook()(db_session, cycle)

    assert (
        await db_session.scalar(select(WorkflowRun.id).where(WorkflowRun.cycle_id == cycle.id))
        is None
    )
    assert await planning.planning_is_done()(db_session, cycle) is True


async def test_planning_waits_for_the_desk_head_s_answer(db_session):
    company, project, candidates, chief = await _desk(db_session)
    runtime = build_runtime()
    planning = EditorialPlanning(runtime.workflows)
    cycle = Cycle(company_id=company.id, seq=1, stage=CycleStage.PLANNING.value)
    db_session.add(cycle)
    await db_session.flush()
    await planning.plan_hook()(db_session, cycle)

    assert await planning.planning_is_done()(db_session, cycle) is False

    run = await db_session.scalar(select(WorkflowRun).where(WorkflowRun.cycle_id == cycle.id))
    for task in (
        await db_session.scalars(select(Task).where(Task.workflow_run_id == run.id))
    ).all():
        task.state = "SUCCEEDED"
    await db_session.flush()

    assert await planning.planning_is_done()(db_session, cycle) is True


async def test_a_paused_head_is_not_asked(db_session):
    company, project, candidates, chief = await _desk(db_session)
    chief.status = AgentStatus.PAUSED.value
    await db_session.flush()
    runtime = build_runtime()
    cycle = Cycle(company_id=company.id, seq=1, stage=CycleStage.PLANNING.value)
    db_session.add(cycle)
    await db_session.flush()

    await EditorialPlanning(runtime.workflows).plan_hook()(db_session, cycle)

    assert (
        await db_session.scalar(select(WorkflowRun.id).where(WorkflowRun.cycle_id == cycle.id))
        is None
    )


async def test_the_company_s_cap_is_the_one_that_refuses(db_session):
    """A newsroom cannot give itself a bigger day: the limit on its own rule is the company's."""
    from autora.company import policy as company_policy
    from autora.domains.newsroom import policy as newsroom_policy

    engine = build_policy_engine()
    rule = next(
        r for r in newsroom_policy.RULES
        if r.action == "instantiate_workflow" and r.role == ROLE
    )  # fmt: skip
    assert rule.limit.check is company_policy.max_workflows

    allowed = engine.decide(
        Actor.agent(uuid.uuid4()), "instantiate_workflow", role=ROLE,
        args={}, facts={"workflows_in_cycle": 0},
    )  # fmt: skip
    refused = engine.decide(
        Actor.agent(uuid.uuid4()), "instantiate_workflow", role=ROLE,
        args={}, facts={"workflows_in_cycle": 99},
    )  # fmt: skip

    assert allowed.outcome == "allow"
    assert refused.outcome == "deny" and "cap" in refused.reason
