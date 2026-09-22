"""T-611: the agent that writes the proposal, and what it may not claim.

The loop's last hole was that nobody wrote proposals. What matters about the agent that fills
it is narrow: it writes a document, it never commits the company, and it cannot report a
proposal it did not write.
"""

import uuid

import pytest
from sqlalchemy import select

from autora.app import build_policy_engine
from autora.company import opportunities as opp
from autora.company import verbs, verbs_business
from autora.company.agents.roster import hire_agent
from autora.company.agents.strategist import (
    PROPOSE,
    ROLE,
    ProposalDraft,
    a_proposal_says_what_would_end_it,
    behaviors,
    submitted_a_draft_command,
    the_opportunity_is_open,
    wrote_the_proposal_it_describes,
)
from autora.company.commands import CommandBus
from autora.company.exploration import (
    DEFAULT_EXPLORATION_CAP,
    PROPOSAL_TEMPLATE,
    Exploration,
    exploration_project,
)
from autora.company.organization import add_role, bootstrap_executive
from autora.db.models import (
    BusinessProposal,
    Cycle,
    CycleStage,
    OpportunityState,
    Project,
    ProposalState,
    WorkflowRun,
)
from autora.runtime.actor import Actor
from autora.runtime.approvals import ApprovalService
from autora.runtime.behaviors import RunContext
from autora.runtime.task_manager import TaskManager
from tests.conftest import unique_company

HUMAN = Actor.human("founder")
KILL = {"auto_pause_if": {"metric": "revenue", "op": "<", "value": 1}}


def _bus() -> CommandBus:
    bus = CommandBus(policy=build_policy_engine(), approvals=ApprovalService(TaskManager()))
    verbs.register(bus)
    verbs_business.register(bus)
    bus.install()
    return bus


async def _world(session, *, with_strategist: bool = True):
    company = await unique_company(session, "strategist")
    department, _ = await bootstrap_executive(session, company.id, actor=HUMAN)
    agent = None
    if with_strategist:
        role = await add_role(
            session, company_id=company.id, department_id=department.id,
            key=ROLE, title="Strategist", actor=HUMAN,
        )  # fmt: skip
        agent = await hire_agent(
            session, company_id=company.id, role=ROLE, display_name="Sol",
            actor=HUMAN, position=role,
        )  # fmt: skip
    opportunity = await opp.discover(
        session, company_id=company.id, key="ai_english", title="AI English learning",
        thesis="Adults pay for lessons.", actor=HUMAN,
    )  # fmt: skip
    await opp.advance(session, opportunity, to=OpportunityState.EVALUATING, actor=HUMAN)
    return company, agent, opportunity


async def _a_run(session, company, agent, project, *, finished: bool = False):
    """A real run to attribute a proposal to. An agent may only have one open at a time — the
    database says so — so a second one in the same test finishes the first."""
    from datetime import UTC, datetime

    from autora.db.models import AgentRun, Task

    task = Task(
        company_id=company.id, project_id=project.id, name=PROPOSE, display_name="Propose",
        required_role=ROLE, state="READY",
    )  # fmt: skip
    session.add(task)
    await session.flush()
    run = AgentRun(
        company_id=company.id,
        agent_id=agent.id,
        task_id=task.id,
        attempt=1,
        state="COMPLETED" if finished else "CREATED",
        finished_at=datetime.now(UTC) if finished else None,
    )
    session.add(run)
    await session.flush()
    return run.id


async def _drafted(session, company, agent, opportunity, run_id, *, bus=None):
    bus = bus or _bus()
    result = await bus.submit(
        session,
        "DraftProposal",
        {
            "opportunity_id": str(opportunity.id),
            "title": "AI English, as a subscription",
            "business_model": "monthly subscription",
            "kill_criteria": KILL,
        },
        company_id=company.id,
        actor=Actor.agent(agent.id),
        role=ROLE,
        idempotency_key=f"k-{uuid.uuid4().hex[:8]}",
        run_id=run_id,
    )
    return result


# --- what it is ----------------------------------------------------------------------------


def test_the_strategist_writes_and_cannot_commit_anything():
    [propose] = behaviors()
    assert propose.role == ROLE and propose.task_name == PROPOSE
    assert propose.tools == ("submit_command",)
    # what it may ask for is the pipeline's business, but the prompt must not promise more
    assert "cannot open a business" in propose.system_prompt
    assert "kill_criteria is required" in propose.system_prompt


async def test_it_may_draft_and_submit_but_a_person_opens_the_business(db_session):
    company, agent, opportunity = await _world(db_session)
    project = await exploration_project(db_session, opportunity, actor=HUMAN)
    run_id = await _a_run(db_session, company, agent, project)
    bus = _bus()

    drafted = await _drafted(db_session, company, agent, opportunity, run_id, bus=bus)
    assert drafted.done, drafted.record.reason
    proposal_id = uuid.UUID(drafted.result["proposal_id"])

    submitted = await bus.submit(
        db_session, "SubmitProposal", {"proposal_id": str(proposal_id)},
        company_id=company.id, actor=Actor.agent(agent.id), role=ROLE,
        idempotency_key=f"k-{uuid.uuid4().hex[:8]}", run_id=run_id,
    )  # fmt: skip
    assert submitted.done

    opening = await bus.submit(
        db_session, "CreateBusinessUnit", {"proposal_id": str(proposal_id), "capital": "10"},
        company_id=company.id, actor=Actor.agent(agent.id), role=ROLE,
        idempotency_key=f"k-{uuid.uuid4().hex[:8]}", run_id=run_id,
    )  # fmt: skip
    assert opening.record.decision == "deny", "a strategist may not open a business"

    proposal = await db_session.get(BusinessProposal, proposal_id)
    assert proposal.state == ProposalState.SUBMITTED.value
    assert proposal.authored_by_run_id == run_id, "the proposal remembers which run wrote it"


async def test_a_proposal_must_say_what_would_end_the_business(db_session):
    company, agent, opportunity = await _world(db_session)
    project = await exploration_project(db_session, opportunity, actor=HUMAN)
    run_id = await _a_run(db_session, company, agent, project)

    refused = await _bus().submit(
        db_session, "DraftProposal",
        {"opportunity_id": str(opportunity.id), "title": "Hope", "kill_criteria": {}},
        company_id=company.id, actor=Actor.agent(agent.id), role=ROLE,
        idempotency_key=f"k-{uuid.uuid4().hex[:8]}", run_id=run_id,
    )  # fmt: skip
    assert refused.record.outcome == "refused"
    assert "not worth running" in refused.record.reason


async def test_a_decided_opportunity_gets_no_more_proposals(db_session):
    company, agent, opportunity = await _world(db_session)
    project = await exploration_project(db_session, opportunity, actor=HUMAN)
    run_id = await _a_run(db_session, company, agent, project)
    await opp.advance(
        db_session, opportunity, to=OpportunityState.REJECTED, actor=HUMAN, reason="no"
    )

    refused = await _drafted(db_session, company, agent, opportunity, run_id)
    assert refused.record.outcome == "refused"
    assert "decided already" in refused.record.reason


# --- what it may not claim --------------------------------------------------------------------


async def test_it_may_not_report_a_proposal_it_did_not_write(db_session):
    company, agent, opportunity = await _world(db_session)
    project = await exploration_project(db_session, opportunity, actor=HUMAN)
    other = await _a_run(db_session, company, agent, project, finished=True)
    drafted = await _drafted(db_session, company, agent, opportunity, other)
    mine = await _a_run(db_session, company, agent, project)
    ctx = RunContext(
        company_id=company.id, project_id=project.id, task=None, agent=agent, run_id=mine
    )
    draft = ProposalDraft(
        opportunity_id=opportunity.id,
        proposal_id=uuid.UUID(drafted.result["proposal_id"]),
        title="AI English",
        business_model="subscription",
        risks=["nobody has paid yet"],
        rationale="I wrote this, honestly",
    )

    assert await wrote_the_proposal_it_describes(db_session, ctx, draft) == [
        "that proposal was written by another run; this one wrote none"
    ]
    assert await submitted_a_draft_command(db_session, ctx, draft) == [
        "no DraftProposal went through in this run"
    ]


async def test_it_may_not_say_it_submitted_what_is_still_a_draft(db_session):
    company, agent, opportunity = await _world(db_session)
    project = await exploration_project(db_session, opportunity, actor=HUMAN)
    run_id = await _a_run(db_session, company, agent, project)
    drafted = await _drafted(db_session, company, agent, opportunity, run_id)
    ctx = RunContext(
        company_id=company.id, project_id=project.id, task=None, agent=agent, run_id=run_id
    )
    draft = ProposalDraft(
        opportunity_id=opportunity.id,
        proposal_id=uuid.UUID(drafted.result["proposal_id"]),
        title="AI English",
        business_model="subscription",
        risks=["nobody has paid yet"],
        rationale="ready for a decision",
        submitted=True,
    )

    [issue] = await wrote_the_proposal_it_describes(db_session, ctx, draft)
    assert "SubmitProposal was not submitted" in issue
    # and a proposal with no risks named has been hoped about, not thought about
    hopeful = draft.model_copy(update={"submitted": False, "risks": []})
    assert await a_proposal_says_what_would_end_it(db_session, ctx, hopeful) == [
        "a proposal with no risks in it has been hoped about, not thought about"
    ]


async def test_it_may_not_write_about_another_company_s_opportunity(db_session):
    company, agent, _ = await _world(db_session)
    _, _, elsewhere = await _world(db_session)
    ctx = RunContext(
        company_id=company.id, project_id=None, task=None, agent=agent, run_id=uuid.uuid4()
    )
    draft = ProposalDraft(
        opportunity_id=elsewhere.id,
        title="Somebody else's idea",
        business_model="subscription",
        risks=["not ours"],
        rationale="borrowed",
    )
    [issue] = await the_opportunity_is_open(db_session, ctx, draft)
    assert "not one of this company's" in issue


# --- when the work is started -----------------------------------------------------------------


async def _cycle(session, company, stage=CycleStage.EXECUTING):
    cycle = Cycle(company_id=company.id, seq=1, stage=stage.value)
    session.add(cycle)
    await session.flush()
    return cycle


@pytest.fixture
def exploration():
    from autora.app import build_runtime

    runtime = build_runtime()
    return Exploration(runtime.workflows)


async def test_one_proposal_a_cycle_for_the_best_opportunity_that_has_none(db_session, exploration):
    """§4: the slow loop moves one step a day, and the company chooses which."""
    company, _, first = await _world(db_session)
    second = await opp.discover(
        db_session, company_id=company.id, key="ai_support", title="AI support",
        actor=HUMAN,
    )  # fmt: skip
    await opp.advance(db_session, second, to=OpportunityState.EVALUATING, actor=HUMAN)
    from decimal import Decimal

    await opp.score(db_session, second, value=Decimal("9"), actor=HUMAN)
    await opp.score(db_session, first, value=Decimal("2"), actor=HUMAN)
    cycle = await _cycle(db_session, company)

    run = await exploration.start_one(db_session, cycle)
    assert run is not None and run.template_name == PROPOSAL_TEMPLATE
    project = await db_session.get(Project, run.project_id)
    assert project.opportunity_id == second.id, "the best-scored one goes first"
    assert project.kill_criteria["auto_pause_if"]["value"] == DEFAULT_EXPLORATION_CAP

    # the same cycle again starts nothing: one step a day
    assert await exploration.start_one(db_session, cycle) is None
    runs = (
        await db_session.scalars(select(WorkflowRun).where(WorkflowRun.cycle_id == cycle.id))
    ).all()
    assert len(runs) == 1


async def test_an_opportunity_that_already_has_a_live_proposal_is_left_alone(
    db_session, exploration
):
    company, agent, opportunity = await _world(db_session)
    project = await exploration_project(db_session, opportunity, actor=HUMAN)
    run_id = await _a_run(db_session, company, agent, project)
    await _drafted(db_session, company, agent, opportunity, run_id)

    assert await exploration.start_one(db_session, await _cycle(db_session, company)) is None


async def test_nothing_is_started_for_a_company_with_no_strategist(db_session, exploration):
    company, _, _ = await _world(db_session, with_strategist=False)
    assert await exploration.start_one(db_session, await _cycle(db_session, company)) is None


async def test_a_discovered_opportunity_is_not_proposed_for_yet(db_session, exploration):
    """Writing a proposal is for something the CEO decided is worth looking at properly."""
    company, _, opportunity = await _world(db_session)
    fresh = await opp.discover(
        db_session, company_id=company.id, key="untouched", title="Untouched", actor=HUMAN
    )
    await opp.advance(
        db_session, opportunity, to=OpportunityState.REJECTED, actor=HUMAN, reason="no"
    )

    assert fresh.state == OpportunityState.DISCOVERED.value
    assert await exploration.start_one(db_session, await _cycle(db_session, company)) is None
