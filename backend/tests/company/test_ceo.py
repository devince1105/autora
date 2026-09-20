"""T-605a: the CEO — what it may decide, how it is asked, and what it may not claim.

The three things worth holding onto: it acts only through the command pipeline, its written
plan must match what it actually asked for, and a company whose CEO is missing or slow still
finishes its day.
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from autora.app import build_policy_engine, build_runtime
from autora.company import verbs, verbs_business
from autora.company.agents.ceo import (
    MAX_GOALS,
    PLAN,
    REVIEW,
    ROLE,
    Allocation,
    CyclePlan,
    CycleReview,
    Goal,
    OpportunityDecision,
    Priority,
    ProjectDecision,
    behaviors,
    goals_are_measurable,
    opportunities_are_real_and_open,
    projects_are_real_and_running,
    said_what_it_decided,
    said_what_it_did,
)
from autora.company.agents.roster import hire_agent
from autora.company.commands import CommandBus
from autora.company.cycle import CycleRunner, work_is_finished
from autora.company.executive import (
    OPERATIONS,
    PLAN_TEMPLATE,
    REVIEW_TEMPLATE,
    Executive,
    operations_project,
)
from autora.company.organization import add_business_unit, bootstrap_executive
from autora.company.tools import register_tools
from autora.db.models import (
    Agent,
    AgentStatus,
    BusinessUnitState,
    CommandRecord,
    Cycle,
    CycleStage,
    OpportunityState,
    Project,
    ProjectState,
    Task,
    WorkflowRun,
)
from autora.runtime.actor import Actor
from autora.runtime.approvals import ApprovalService
from autora.runtime.behaviors import RunContext
from autora.runtime.task_manager import TaskManager
from autora.runtime.tools import ToolRegistry
from tests.conftest import unique_company

HUMAN = Actor.human("founder")


async def _company(db_session, *, with_ceo: bool = True):
    company = await unique_company(db_session, "ceo")
    _, ceo_role = await bootstrap_executive(db_session, company.id, actor=HUMAN)
    unit = await add_business_unit(
        db_session, company_id=company.id, key="ai_media", name="AI Media",
        actor=HUMAN, state=BusinessUnitState.ACTIVE,
    )  # fmt: skip
    agent = None
    if with_ceo:
        agent = await hire_agent(
            db_session, company_id=company.id, role=ROLE, display_name="Cee",
            actor=HUMAN, position=ceo_role,
        )  # fmt: skip
    return company, unit, agent


async def _a_run(session, company, project, agent):
    """A real agent run to attribute commands to: the log's run_id is a foreign key, because
    "which run asked for this" has to point at a run that happened."""
    from autora.db.models import AgentRun, Task

    task = Task(
        company_id=company.id, project_id=project.id, name="plan", display_name="Plan",
        required_role=ROLE, state="READY",
    )  # fmt: skip
    session.add(task)
    await session.flush()
    run = AgentRun(
        company_id=company.id, agent_id=agent.id, task_id=task.id, attempt=1, state="CREATED"
    )
    session.add(run)
    await session.flush()
    return run.id


def _bus() -> CommandBus:
    bus = CommandBus(policy=build_policy_engine(), approvals=ApprovalService(TaskManager()))
    verbs.register(bus)
    bus.install()
    return bus


# --- what it is ---------------------------------------------------------------------------------


def test_the_ceo_has_two_jobs_and_one_tool():
    plan, review = behaviors()

    assert (plan.role, plan.task_name) == (ROLE, PLAN)
    assert (review.role, review.task_name) == (ROLE, REVIEW)
    assert plan.tools == review.tools == ("submit_command",)
    assert plan.capability == "reasoning"


def test_the_prompt_says_what_the_ceo_does_not_do():
    """The whole point of the v2 revision: a CEO that picks five news topics is the mistake."""
    plan, review = behaviors()

    assert "do NOT do the businesses' work" in plan.system_prompt
    assert "never choose what to publish" in plan.system_prompt
    assert "submit_command" in plan.system_prompt
    assert "a person must approve" in plan.system_prompt
    # and the review knows auto-pause is not its business
    assert "Automatic pausing by kill criteria is not your job" in review.system_prompt


# --- what it may not claim -----------------------------------------------------------------------


async def test_a_plan_may_not_claim_what_it_never_asked_for(db_session):
    """The cheapest lie is describing a decision you did not make, and the most expensive one
    to find later."""
    company, unit, agent = await _company(db_session)
    project = await operations_project(db_session, company.id, actor=HUMAN)
    ctx = RunContext(
        company_id=company.id, project_id=project.id, task=None, agent=agent, run_id=uuid.uuid4()
    )
    plan = CyclePlan(
        goals=[Goal(title="Publish 3", metric="newsroom.published_articles", target=3)],
        allocations=[
            Allocation(amount=Decimal("20"), business_unit_id=unit.id, rationale="because")
        ],
        rationale="a plan I did not carry out",
    )

    issues = await said_what_it_did(db_session, ctx, plan)

    assert len(issues) == 2
    assert any("no CreateCycleGoal was submitted" in i for i in issues)
    assert any("no matching AllocateBudget" in i for i in issues)


async def test_a_plan_that_matches_what_was_submitted_passes(db_session):
    company, unit, agent = await _company(db_session)
    project = await operations_project(db_session, company.id, actor=HUMAN)
    run_id = await _a_run(db_session, company, project, agent)
    bus = _bus()
    await bus.submit(
        db_session, "CreateCycleGoal",
        {"title": "Publish 3", "metric": "newsroom.published_articles", "target": 3},
        company_id=company.id, actor=Actor.agent(agent.id), role=ROLE,
        idempotency_key=f"k-{uuid.uuid4().hex[:8]}", run_id=run_id,
    )  # fmt: skip
    await bus.submit(
        db_session, "AllocateBudget",
        {"amount": "20", "business_unit_id": str(unit.id)},
        company_id=company.id, actor=Actor.agent(agent.id), role=ROLE,
        idempotency_key=f"k-{uuid.uuid4().hex[:8]}", run_id=run_id,
    )  # fmt: skip
    ctx = RunContext(
        company_id=company.id, project_id=project.id, task=None, agent=agent, run_id=run_id
    )
    plan = CyclePlan(
        goals=[Goal(title="Publish 3", metric="newsroom.published_articles", target=3)],
        allocations=[
            Allocation(amount=Decimal("20"), business_unit_id=unit.id, rationale="the business")
        ],
        rationale="one goal, one envelope",
    )

    # the budget was above the CEO's limit, so it waits for a person — but it *was* asked for,
    # and a plan may report what it asked for
    assert await said_what_it_did(db_session, ctx, plan) == []


async def test_a_refused_command_may_not_be_written_up_as_done(db_session):
    """A CEO whose budget was denied must not report having allocated it."""
    company, unit, agent = await _company(db_session)
    project = await operations_project(db_session, company.id, actor=HUMAN)
    run_id = await _a_run(db_session, company, project, agent)
    bus = _bus()
    refused = await bus.submit(
        db_session, "AllocateBudget", {"amount": "20", "business_unit_id": str(unit.id)},
        company_id=company.id, actor=Actor.agent(agent.id), role="writer",  # not allowed to
        idempotency_key=f"k-{uuid.uuid4().hex[:8]}", run_id=run_id,
    )  # fmt: skip
    assert refused.record.outcome == "refused"
    ctx = RunContext(
        company_id=company.id, project_id=project.id, task=None, agent=agent, run_id=run_id
    )
    plan = CyclePlan(
        allocations=[
            Allocation(amount=Decimal("20"), business_unit_id=unit.id, rationale="claimed")
        ],
        rationale="I said I did it",
    )

    issues = await said_what_it_did(db_session, ctx, plan)

    assert any("no matching AllocateBudget" in i for i in issues)


async def test_a_plan_may_not_name_a_project_that_is_gone(db_session):
    company, unit, agent = await _company(db_session)
    dead = Project(
        company_id=company.id, name="dead", state=ProjectState.KILLED.value, kill_criteria={}
    )
    db_session.add(dead)
    await db_session.flush()
    ctx = RunContext(
        company_id=company.id, project_id=dead.id, task=None, agent=agent, run_id=uuid.uuid4()
    )

    issues = await projects_are_real_and_running(
        db_session,
        ctx,
        CyclePlan(
            priorities=[Priority(project_id=dead.id, rank=1, rationale="x")],
            rationale="y",
        ),
    )

    assert any("KILLED" in i for i in issues)


async def test_a_review_may_not_decide_about_another_company_s_project(db_session):
    company, unit, agent = await _company(db_session)
    other = await unique_company(db_session, "elsewhere")
    theirs = Project(
        company_id=other.id, name="theirs", state=ProjectState.ACTIVE.value, kill_criteria={}
    )
    db_session.add(theirs)
    await db_session.flush()
    ctx = RunContext(
        company_id=company.id, project_id=theirs.id, task=None, agent=agent, run_id=uuid.uuid4()
    )

    issues = await projects_are_real_and_running(
        db_session,
        ctx,
        CycleReview(
            projects=[
                ProjectDecision(project_id=theirs.id, decision="pause", rationale="not mine")
            ],
            summary="s",
        ),
    )

    assert any("not one of this company's" in i for i in issues)


async def test_a_cycle_cannot_have_more_goals_than_it_can_hold(db_session):
    company, unit, agent = await _company(db_session)
    ctx = RunContext(
        company_id=company.id, project_id=uuid.uuid4(), task=None, agent=agent,
        run_id=uuid.uuid4(),
    )  # fmt: skip
    plan = CyclePlan.model_construct(
        goals=[Goal(title=f"g{n}", metric=f"m_{n}", target=1) for n in range(MAX_GOALS + 2)],
        allocations=[],
        priorities=[],
        rationale="too many",
    )

    issues = await goals_are_measurable(db_session, ctx, plan)

    assert any("more than the 3" in i for i in issues)


# --- how it is asked -----------------------------------------------------------------------------


async def test_planning_starts_the_ceo_s_work_and_waits_for_it(db_session):
    company, unit, agent = await _company(db_session)
    runtime = build_runtime()
    executive = Executive(runtime.workflows)
    cycles = CycleRunner()
    cycles.finishes_when(CycleStage.EXECUTING, work_is_finished)
    executive.install(cycles)

    cycle = await cycles.start(db_session, company.id)

    run = await db_session.scalar(select(WorkflowRun).where(WorkflowRun.cycle_id == cycle.id))
    assert run is not None and run.template_name == PLAN_TEMPLATE
    (task,) = (await db_session.scalars(select(Task).where(Task.workflow_run_id == run.id))).all()
    assert (task.required_role, task.name) == (ROLE, PLAN)
    assert task.cycle_id == cycle.id  # the audit chain, from the day to the decision

    # the cycle waits: the CEO has not answered yet
    assert await cycles.tick(db_session, company.id) is None
    assert cycle.stage == CycleStage.PLANNING

    task.state = "SUCCEEDED"
    await db_session.flush()
    moved = await cycles.tick(db_session, company.id)
    assert moved is not None and moved.stage != CycleStage.PLANNING


async def test_the_ceo_s_work_belongs_to_the_company_s_own_project(db_session):
    """Its runs cost money, and money has to land somewhere that can be reported on."""
    company, unit, agent = await _company(db_session)
    runtime = build_runtime()
    cycles = CycleRunner()
    Executive(runtime.workflows).install(cycles)

    cycle = await cycles.start(db_session, company.id)

    run = await db_session.scalar(select(WorkflowRun).where(WorkflowRun.cycle_id == cycle.id))
    project = await db_session.get(Project, run.project_id)
    assert project.name == OPERATIONS
    assert project.business_unit_id is None  # the company's own work, not a business's


async def test_a_company_with_no_ceo_still_finishes_its_day(db_session):
    """The honest shape of a company that has not hired one."""
    company, unit, _ = await _company(db_session, with_ceo=False)
    runtime = build_runtime()
    cycles = CycleRunner()
    cycles.finishes_when(CycleStage.EXECUTING, work_is_finished)
    Executive(runtime.workflows).install(cycles)

    cycle = await cycles.start(db_session, company.id)
    moved = await cycles.tick(db_session, company.id)

    assert moved is not None and moved.stage == CycleStage.DONE
    assert (
        await db_session.scalar(select(WorkflowRun.id).where(WorkflowRun.cycle_id == cycle.id))
        is None
    )


async def test_a_paused_ceo_does_not_get_asked(db_session):
    company, unit, agent = await _company(db_session)
    agent.status = AgentStatus.PAUSED.value
    await db_session.flush()
    runtime = build_runtime()
    cycles = CycleRunner()
    Executive(runtime.workflows).install(cycles)

    cycle = await cycles.start(db_session, company.id)

    assert (
        await db_session.scalar(select(WorkflowRun.id).where(WorkflowRun.cycle_id == cycle.id))
        is None
    )


async def test_entering_planning_twice_asks_once(db_session):
    company, unit, agent = await _company(db_session)
    runtime = build_runtime()
    executive = Executive(runtime.workflows)
    cycles = CycleRunner()
    executive.install(cycles)
    cycle = await cycles.start(db_session, company.id)

    await executive.plan_hook()(db_session, cycle)

    runs = (
        await db_session.scalars(select(WorkflowRun).where(WorkflowRun.cycle_id == cycle.id))
    ).all()
    assert len(runs) == 1


async def test_reviewing_asks_the_other_question(db_session):
    company, unit, agent = await _company(db_session)
    runtime = build_runtime()
    executive = Executive(runtime.workflows)
    cycles = CycleRunner()
    executive.install(cycles)
    cycle = Cycle(company_id=company.id, seq=1, stage=CycleStage.REVIEWING.value)
    db_session.add(cycle)
    await db_session.flush()

    await executive.review_hook()(db_session, cycle)

    run = await db_session.scalar(
        select(WorkflowRun).where(
            WorkflowRun.cycle_id == cycle.id, WorkflowRun.template_name == REVIEW_TEMPLATE
        )
    )
    (task,) = (await db_session.scalars(select(Task).where(Task.workflow_run_id == run.id))).all()
    assert task.name == REVIEW


# --- the only way it can act ----------------------------------------------------------------------

# These use only ``committed``: the event outbox takes a per-company advisory lock to serialise
# event sequence numbers, so a test that built its company inside the rolled-back ``db_session``
# transaction and then acted on it through a real session would wait for itself forever.


@pytest.fixture
async def world(committed):
    async with committed() as session:
        company = await unique_company(session, "ceo-tools")
        _, ceo_role = await bootstrap_executive(session, company.id, actor=HUMAN)
        agent = await hire_agent(
            session, company_id=company.id, role=ROLE, display_name="Cee",
            actor=HUMAN, position=ceo_role,
        )  # fmt: skip
        await session.commit()
    return company, agent


async def test_submit_command_is_how_an_agent_asks(committed, world):
    """It cannot write to the company directly; it asks, and the pipeline decides."""
    company, agent = world
    tools = ToolRegistry(committed)
    register_tools(tools, _bus())

    invocation = await tools.invoke(
        "submit_command",
        {
            "command": "CreateCycleGoal",
            "payload": {"title": "Publish 3", "metric": "published", "target": 3},
            "reason": "the cycle needs one measurable thing",
        },
        company_id=company.id,
        actor=Actor.agent(agent.id),
        tool_call_id="call_1",
        agent_id=agent.id,
    )

    assert invocation.ok, invocation
    assert invocation.output["decision"] == "done"
    async with committed() as session:
        record = await session.scalar(
            select(CommandRecord).where(CommandRecord.company_id == company.id)
        )
        assert record.role == ROLE  # decided as the CEO, not as "some agent"


async def test_a_refusal_comes_back_as_an_answer_not_an_error(committed, world):
    """A tool that raised here would end the run over a perfectly ordinary "no"."""
    company, agent = world
    async with committed() as session:
        theirs = await session.get(Agent, agent.id)
        theirs.role = "writer"  # writers may not set the company's goals
        await session.commit()
    tools = ToolRegistry(committed)
    register_tools(tools, _bus())

    invocation = await tools.invoke(
        "submit_command",
        {"command": "CreateCycleGoal", "payload": {"title": "x", "metric": "m", "target": 1}},
        company_id=company.id,
        actor=Actor.agent(agent.id),
        tool_call_id="call_1",
        agent_id=agent.id,
    )

    assert invocation.ok  # the call worked; the answer was no
    assert invocation.output["decision"] == "refused"
    assert "writer" in invocation.output["reason"]


async def test_asking_for_something_the_company_cannot_do(committed, world):
    company, agent = world
    tools = ToolRegistry(committed)
    register_tools(tools, _bus())

    invocation = await tools.invoke(
        "submit_command",
        {"command": "SellTheCompany", "payload": {}},
        company_id=company.id,
        actor=Actor.agent(agent.id),
        tool_call_id="call_1",
        agent_id=agent.id,
    )

    assert invocation.ok
    assert invocation.output["decision"] == "refused"
    assert "no command" in invocation.output["reason"]


async def test_the_same_tool_call_twice_asks_once(committed, world):
    """A run retried after a crash re-submits the same command and gets the first outcome."""
    company, agent = world
    tools = ToolRegistry(committed)
    register_tools(tools, _bus())
    args = {
        "command": "CreateCycleGoal",
        "payload": {"title": "Publish 3", "metric": "published", "target": 3},
    }

    first = await tools.invoke(
        "submit_command", args, company_id=company.id, actor=Actor.agent(agent.id),
        tool_call_id="call_1", agent_id=agent.id, step_seq=1,
    )  # fmt: skip
    again = await tools.invoke(
        "submit_command", args, company_id=company.id, actor=Actor.agent(agent.id),
        tool_call_id="call_1", agent_id=agent.id, step_seq=1,
    )  # fmt: skip

    assert first.output["goal_id"] == again.output["goal_id"]
    async with committed() as session:
        records = (
            await session.scalars(
                select(CommandRecord).where(CommandRecord.company_id == company.id)
            )
        ).all()
        assert len(records) == 1


# --- deciding what the company might do next (T-611 increment) ---------------------------------


async def _an_opportunity(session, company, *, key="ai_english", state=None):
    from autora.company import opportunities as opp

    opportunity = await opp.discover(
        session,
        company_id=company.id,
        key=key,
        title="AI English learning",
        thesis="Adults pay for lessons.",
        actor=HUMAN,
    )
    if state is not None:
        await opp.advance(session, opportunity, to=state, actor=HUMAN, reason="for the test")
    return opportunity


async def test_the_review_may_decide_about_opportunities(db_session):
    """The CEO's job is which businesses to be in, not only how the current one is doing."""
    company, _, agent = await _company(db_session)
    project = await operations_project(db_session, company.id, actor=HUMAN)
    opportunity = await _an_opportunity(db_session, company)
    run_id = await _a_run(db_session, company, project, agent)
    bus = _bus()
    verbs_business.register(bus)
    await bus.submit(
        db_session, "AdvanceOpportunity",
        {"opportunity_id": str(opportunity.id), "to_state": "EVALUATING"},
        company_id=company.id, actor=Actor.agent(agent.id), role=ROLE,
        idempotency_key=f"k-{uuid.uuid4().hex[:8]}", run_id=run_id,
    )  # fmt: skip
    ctx = RunContext(
        company_id=company.id, project_id=project.id, task=None, agent=agent, run_id=run_id
    )
    review = CycleReview(
        opportunities=[
            OpportunityDecision(
                opportunity_id=opportunity.id,
                decision="evaluate",
                score=Decimal("7"),
                rationale="three competitors and none of them bilingual",
            )
        ],
        summary="Looked at what we might do next.",
    )

    assert await opportunities_are_real_and_open(db_session, ctx, review) == []
    assert await said_what_it_decided(db_session, ctx, review) == []


async def test_a_review_may_not_claim_a_decision_it_never_submitted(db_session):
    company, _, agent = await _company(db_session)
    project = await operations_project(db_session, company.id, actor=HUMAN)
    opportunity = await _an_opportunity(db_session, company)
    run_id = await _a_run(db_session, company, project, agent)
    ctx = RunContext(
        company_id=company.id, project_id=project.id, task=None, agent=agent, run_id=run_id
    )
    review = CycleReview(
        opportunities=[
            OpportunityDecision(
                opportunity_id=opportunity.id, decision="reject", rationale="too expensive"
            )
        ],
        summary="I said no, apparently.",
    )

    [issue] = await said_what_it_decided(db_session, ctx, review)
    assert "no RejectOpportunity was submitted" in issue
    # and watching asks for nothing, so it needs no command
    watched = review.model_copy(
        update={"opportunities": [review.opportunities[0].model_copy(update={"decision": "watch"})]}
    )
    assert await said_what_it_decided(db_session, ctx, watched) == []


async def test_the_wrong_step_is_not_the_step_it_wrote_down(db_session):
    """Moving one to EVALUATING is not moving it to VALIDATING, and the review must not say so."""
    company, _, agent = await _company(db_session)
    project = await operations_project(db_session, company.id, actor=HUMAN)
    opportunity = await _an_opportunity(db_session, company)
    run_id = await _a_run(db_session, company, project, agent)
    bus = _bus()
    verbs_business.register(bus)
    await bus.submit(
        db_session, "AdvanceOpportunity",
        {"opportunity_id": str(opportunity.id), "to_state": "EVALUATING"},
        company_id=company.id, actor=Actor.agent(agent.id), role=ROLE,
        idempotency_key=f"k-{uuid.uuid4().hex[:8]}", run_id=run_id,
    )  # fmt: skip
    ctx = RunContext(
        company_id=company.id, project_id=project.id, task=None, agent=agent, run_id=run_id
    )
    review = CycleReview(
        opportunities=[
            OpportunityDecision(
                opportunity_id=opportunity.id, decision="validate", rationale="let us find out"
            )
        ],
        summary="Two different steps.",
    )

    [issue] = await said_what_it_decided(db_session, ctx, review)
    assert "no AdvanceOpportunity was submitted" in issue


async def test_a_decided_opportunity_is_not_open_for_another_decision(db_session):
    company, _, agent = await _company(db_session)
    project = await operations_project(db_session, company.id, actor=HUMAN)
    rejected = await _an_opportunity(
        db_session, company, key="ai_support", state=OpportunityState.REJECTED
    )
    other = await unique_company(db_session, "elsewhere")
    elsewhere = await _an_opportunity(db_session, other, key="elsewhere")
    ctx = RunContext(
        company_id=company.id, project_id=project.id, task=None, agent=agent, run_id=uuid.uuid4()
    )
    review = CycleReview(
        opportunities=[
            OpportunityDecision(
                opportunity_id=rejected.id, decision="watch", rationale="let us look again"
            ),
            OpportunityDecision(
                opportunity_id=elsewhere.id, decision="watch", rationale="not ours"
            ),
        ],
        summary="Reopening what was closed.",
    )

    issues = await opportunities_are_real_and_open(db_session, ctx, review)
    assert len(issues) == 2
    assert any("was decided already" in i for i in issues)
    assert any("not one of this company's" in i for i in issues)


async def test_the_review_summary_says_what_it_looked_at(db_session):
    company, _, _ = await _company(db_session)
    opportunity = await _an_opportunity(db_session, company)
    [_, review_behavior] = behaviors()
    review = CycleReview(
        projects=[],
        opportunities=[
            OpportunityDecision(
                opportunity_id=opportunity.id, decision="watch", rationale="nothing new"
            )
        ],
        summary="A quiet day.",
    )
    assert "1 opportunity(s) looked at" in review_behavior.summarize(review)
