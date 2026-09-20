"""T-611: the eight commands of the business loop, on the ordinary pipeline.

The line these tests hold is ARCHITECTURE_V2_1 §6: **a person decides the moment something
becomes irreversible or spends real capital.** Everything before that — looking, scoring,
validating — the CEO does alone, because it is cheap and it can be undone.

The other thing they hold is what ``CreateBusinessUnit`` reads: a proposal id, and nothing
else of substance. The name, the product, the criteria and the money come from the frozen
document, which is what makes the approval mean something.
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from autora.app import build_policy_engine
from autora.company import opportunities as opp
from autora.company import verbs, verbs_business
from autora.company.commands import CommandBus
from autora.company.organization import add_business_unit
from autora.db.models import (
    Approval,
    ApprovalState,
    Budget,
    BusinessProposal,
    BusinessUnit,
    BusinessUnitState,
    EventRecord,
    Opportunity,
    OpportunityState,
    Product,
    ProductState,
    Project,
    ProjectState,
    ProposalState,
    Transaction,
)
from autora.runtime.actor import Actor
from autora.runtime.approvals import ApprovalService
from autora.runtime.task_manager import TaskManager
from tests.conftest import unique_company

CEO = Actor.agent(uuid.uuid4())
HUMAN = Actor.human("founder")


@pytest.fixture
def bus():
    bus = CommandBus(policy=build_policy_engine(), approvals=ApprovalService(TaskManager()))
    verbs.register(bus)
    verbs_business.register(bus)
    bus.install()
    return bus


def _key() -> str:
    return f"k-{uuid.uuid4().hex[:12]}"


async def _world(session):
    company = await unique_company(session, "biz")
    opportunity = await opp.discover(
        session,
        company_id=company.id,
        key="ai_english",
        title="AI English learning",
        thesis="Adults pay for lessons; a model teaches at the margin of zero.",
        actor=CEO,
    )
    return company, opportunity


async def _submitted_proposal(session, opportunity, **fields):
    proposal = await opp.draft_proposal(
        session,
        opportunity,
        title="AI English, subscription",
        actor=CEO,
        business_model="subscription",
        estimated_startup_cost=Decimal("2000"),
        kill_criteria={"auto_pause_if": {"metric": "revenue_usd", "op": "<", "value": 100}},
        proposed_product={"key": "daily_english", "name": "Daily English"},
        **fields,
    )
    await opp.submit(session, proposal, actor=CEO)
    return proposal


async def _ask(bus, session, company, name, args, actor=CEO, role="ceo"):
    return await bus.submit(
        session, name, args, company_id=company.id, actor=actor, role=role,
        idempotency_key=_key(),
    )  # fmt: skip


# --- what the CEO may do alone ------------------------------------------------------------


async def test_the_ceo_scores_and_advances_an_opportunity_by_itself(db_session, bus):
    company, opportunity = await _world(db_session)

    scored = await _ask(
        bus,
        db_session,
        company,
        "ScoreOpportunity",
        {"opportunity_id": str(opportunity.id), "score": "7.5", "reason": "cheap to try"},
    )
    moved = await _ask(
        bus,
        db_session,
        company,
        "AdvanceOpportunity",
        {"opportunity_id": str(opportunity.id), "to_state": "EVALUATING"},
    )

    assert scored.done and scored.record.decision == "allow"
    assert moved.done and opportunity.state == OpportunityState.EVALUATING.value
    assert opportunity.score == Decimal("7.50")


async def test_approving_an_opportunity_is_not_the_ceo_s_to_make(db_session, bus):
    """EVALUATING and VALIDATING are reversible. APPROVED commits the company."""
    company, opportunity = await _world(db_session)
    await _ask(
        bus, db_session, company, "AdvanceOpportunity",
        {"opportunity_id": str(opportunity.id), "to_state": "EVALUATING"},
    )  # fmt: skip

    result = await _ask(
        bus,
        db_session,
        company,
        "AdvanceOpportunity",
        {"opportunity_id": str(opportunity.id), "to_state": "APPROVED"},
    )

    assert not result.done and result.record.decision == "needs_approval"
    assert result.approval is not None and result.approval.state == ApprovalState.PENDING.value
    assert opportunity.state == OpportunityState.EVALUATING.value, "nothing moved while waiting"


async def test_a_rejection_is_the_ceo_s_and_keeps_its_reason(db_session, bus):
    company, opportunity = await _world(db_session)

    result = await _ask(
        bus,
        db_session,
        company,
        "RejectOpportunity",
        {"opportunity_id": str(opportunity.id), "reason": "acquisition cost beats the revenue"},
    )

    assert result.done
    assert opportunity.state == OpportunityState.REJECTED.value
    assert "acquisition cost" in opportunity.decision_reason
    # the command's own record points at the event it caused
    (event_id,) = result.record.event_ids
    event = await db_session.get(EventRecord, event_id)
    assert event.event_type == "OPPORTUNITY_REJECTED"


async def test_advancing_to_rejected_is_sent_the_other_way(db_session, bus):
    """Two verbs that write different rows would let a rejection lose its reason."""
    company, opportunity = await _world(db_session)
    result = await _ask(
        bus,
        db_session,
        company,
        "AdvanceOpportunity",
        {"opportunity_id": str(opportunity.id), "to_state": "REJECTED"},
    )
    assert result.record.outcome == "refused"
    assert "RejectOpportunity" in result.record.reason


# --- exploration money ------------------------------------------------------------------------


async def test_exploration_money_goes_to_exploration_projects(db_session, bus):
    company, opportunity = await _world(db_session)
    exploring = Project(
        company_id=company.id,
        opportunity_id=opportunity.id,
        name="Validate AI English",
        state=ProjectState.ACTIVE.value,
        kill_criteria={"auto_pause_if": {"metric": "cost_usd", "op": ">", "value": 20}},
    )
    ordinary = Project(
        company_id=company.id,
        name="Run the newsroom",
        state=ProjectState.ACTIVE.value,
        kill_criteria={},
    )
    db_session.add_all([exploring, ordinary])
    await db_session.flush()

    funded = await _ask(
        bus,
        db_session,
        company,
        "AllocateExplorationBudget",
        {"project_id": str(exploring.id), "amount": "1.50"},
    )
    wrong = await _ask(
        bus,
        db_session,
        company,
        "AllocateExplorationBudget",
        {"project_id": str(ordinary.id), "amount": "1.00"},
    )

    assert funded.done and funded.result["opportunity_id"] == str(opportunity.id)
    budget = await db_session.scalar(select(Budget).where(Budget.project_id == exploring.id))
    assert budget.amount == Decimal("1.500000")
    assert wrong.record.outcome == "refused"
    assert "AllocateBudget" in wrong.record.reason


async def test_a_large_exploration_asks_a_person(db_session, bus):
    """Exploring is meant to be cheap; a company that can fund a big one alone can fund a
    business by calling it exploration."""
    company, opportunity = await _world(db_session)
    project = Project(
        company_id=company.id,
        opportunity_id=opportunity.id,
        name="Validate",
        state=ProjectState.ACTIVE.value,
        kill_criteria={},
    )
    db_session.add(project)
    await db_session.flush()

    result = await _ask(
        bus,
        db_session,
        company,
        "AllocateExplorationBudget",
        {"project_id": str(project.id), "amount": "500"},
    )

    assert not result.done and result.record.decision == "needs_approval"
    assert await db_session.scalar(select(Budget).where(Budget.project_id == project.id)) is None


# --- opening a business -------------------------------------------------------------------------


async def test_opening_a_business_always_asks_a_person(db_session, bus):
    company, opportunity = await _world(db_session)
    proposal = await _submitted_proposal(db_session, opportunity)

    result = await _ask(
        bus,
        db_session,
        company,
        "CreateBusinessUnit",
        {"proposal_id": str(proposal.id), "capital": "2000"},
    )

    assert not result.done and result.record.decision == "needs_approval"
    assert proposal.state == ProposalState.SUBMITTED.value, "nothing was opened while waiting"
    assert (
        await db_session.scalar(select(BusinessUnit).where(BusinessUnit.company_id == company.id))
        is None
    )


async def test_a_person_approves_and_the_business_opens_exactly_as_proposed(db_session, bus):
    """The one command that turns a document into an organisation, in one transaction."""
    company, opportunity = await _world(db_session)
    await opp.advance(db_session, opportunity, to=OpportunityState.EVALUATING, actor=CEO)
    proposal = await _submitted_proposal(db_session, opportunity)
    asked = await _ask(
        bus,
        db_session,
        company,
        "CreateBusinessUnit",
        {"proposal_id": str(proposal.id), "capital": "2000", "reason": "the numbers hold"},
    )

    await bus.approvals.decide(
        db_session, asked.approval.id, outcome="approve", actor=HUMAN, reason="worth trying"
    )

    unit = await db_session.scalar(
        select(BusinessUnit).where(BusinessUnit.company_id == company.id)
    )
    assert unit is not None
    assert unit.key == "ai_english"  # the opportunity's key, unless one was given
    assert unit.name == proposal.title
    assert unit.state == BusinessUnitState.ACTIVE.value
    assert unit.kill_criteria == proposal.kill_criteria, "the criteria came from the document"

    product = await db_session.scalar(select(Product).where(Product.business_unit_id == unit.id))
    assert product.key == "daily_english" and product.state == ProductState.DRAFT.value

    await db_session.refresh(proposal)
    await db_session.refresh(opportunity)
    assert proposal.state == ProposalState.APPROVED.value
    assert proposal.business_unit_id == unit.id
    assert opportunity.state == OpportunityState.APPROVED.value
    assert opportunity.business_unit_id == unit.id

    capital = await db_session.scalar(
        select(Transaction).where(Transaction.business_unit_id == unit.id)
    )
    assert capital.amount == Decimal("2000.000000") and capital.kind == "transfer"
    assert capital.ref_id == proposal.id, "the money points at the document it was approved for"


async def test_a_proposal_that_was_not_submitted_cannot_open_anything(db_session, bus):
    company, opportunity = await _world(db_session)
    draft = await opp.draft_proposal(db_session, opportunity, title="half an idea", actor=CEO)

    asked = await _ask(
        bus, db_session, company, "CreateBusinessUnit", {"proposal_id": str(draft.id)}
    )
    await bus.approvals.decide(
        db_session, asked.approval.id, outcome="approve", actor=HUMAN, reason="sure"
    )

    assert draft.state == ProposalState.DRAFT.value
    assert (
        await db_session.scalar(select(BusinessUnit).where(BusinessUnit.company_id == company.id))
        is None
    )


async def test_the_same_business_is_not_opened_twice(db_session, bus):
    company, opportunity = await _world(db_session)
    await add_business_unit(
        db_session, company_id=company.id, key="ai_english", name="Already here",
        actor=HUMAN, state=BusinessUnitState.ACTIVE,
    )  # fmt: skip
    proposal = await _submitted_proposal(db_session, opportunity)

    asked = await _ask(
        bus, db_session, company, "CreateBusinessUnit", {"proposal_id": str(proposal.id)}
    )
    await bus.approvals.decide(
        db_session, asked.approval.id, outcome="approve", actor=HUMAN, reason="go"
    )

    units = (
        await db_session.scalars(select(BusinessUnit).where(BusinessUnit.company_id == company.id))
    ).all()
    assert len(units) == 1 and units[0].name == "Already here"
    assert proposal.state == ProposalState.SUBMITTED.value


# --- running and ending a business ---------------------------------------------------------------


async def test_the_ceo_may_top_up_a_business_but_not_by_any_amount(db_session, bus):
    company, _ = await _world(db_session)
    unit = await add_business_unit(
        db_session, company_id=company.id, key="ai_media", name="AI Media",
        actor=HUMAN, state=BusinessUnitState.ACTIVE,
    )  # fmt: skip

    small = await _ask(
        bus,
        db_session,
        company,
        "ScaleBusinessUnit",
        {"business_unit_id": str(unit.id), "amount": "20", "reason": "it is working"},
    )
    large = await _ask(
        bus,
        db_session,
        company,
        "ScaleBusinessUnit",
        {"business_unit_id": str(unit.id), "amount": "5000", "reason": "double down"},
    )

    assert small.done and small.record.decision in ("allow", "limited")
    assert not large.done and large.record.decision == "needs_approval"
    moved = (
        await db_session.scalars(select(Transaction).where(Transaction.business_unit_id == unit.id))
    ).all()
    assert [t.amount for t in moved] == [Decimal("20.000000")]


async def test_pausing_is_the_ceo_s_and_winding_down_is_not(db_session, bus):
    """Pausing stops the bleeding; ending a business is irreversible (§6)."""
    company, _ = await _world(db_session)
    unit = await add_business_unit(
        db_session, company_id=company.id, key="ai_media", name="AI Media",
        actor=HUMAN, state=BusinessUnitState.ACTIVE,
    )  # fmt: skip
    product = Product(
        company_id=company.id,
        business_unit_id=unit.id,
        key="daily_news",
        name="Daily news",
        state=ProductState.LIVE.value,
    )
    db_session.add(product)
    await db_session.flush()

    paused = await _ask(
        bus,
        db_session,
        company,
        "PauseBusinessUnit",
        {"business_unit_id": str(unit.id), "reason": "it is losing money"},
    )
    assert paused.done and unit.state == BusinessUnitState.PAUSED.value
    assert product.state == ProductState.LIVE.value, "a pause is not the end of the product"

    ending = await _ask(
        bus,
        db_session,
        company,
        "WindDownBusinessUnit",
        {"business_unit_id": str(unit.id), "reason": "two quarters below the floor"},
    )
    assert not ending.done and ending.record.decision == "needs_approval"

    await bus.approvals.decide(
        db_session, ending.approval.id, outcome="approve", actor=HUMAN, reason="agreed"
    )
    assert unit.state == BusinessUnitState.WOUND_DOWN.value
    await db_session.refresh(product)
    assert product.state == ProductState.RETIRED.value, "its products end with it"


async def test_every_one_of_them_is_recorded_whatever_happened(db_session, bus):
    """The point of the pipeline: a refusal, a wait and a success all leave the same trail."""
    company, opportunity = await _world(db_session)
    proposal = await _submitted_proposal(db_session, opportunity)
    await _ask(
        bus, db_session, company, "ScoreOpportunity",
        {"opportunity_id": str(opportunity.id), "score": "3"},
    )  # fmt: skip
    await _ask(bus, db_session, company, "CreateBusinessUnit", {"proposal_id": str(proposal.id)})
    await _ask(
        bus, db_session, company, "AdvanceOpportunity",
        {"opportunity_id": str(opportunity.id), "to_state": "NOWHERE"},
    )  # fmt: skip

    from autora.db.models import CommandRecord

    records = (
        await db_session.scalars(
            select(CommandRecord)
            .where(CommandRecord.company_id == company.id)
            .order_by(CommandRecord.created_at)
        )
    ).all()
    assert [(r.command, r.outcome) for r in records] == [
        ("ScoreOpportunity", "done"),
        ("CreateBusinessUnit", "awaiting_approval"),
        ("AdvanceOpportunity", "refused"),
    ]
    pending = await db_session.scalar(select(Approval).where(Approval.company_id == company.id))
    assert pending.state == ApprovalState.PENDING.value
    assert (
        await db_session.scalar(select(Opportunity).where(Opportunity.id == opportunity.id))
        is opportunity
    )


async def test_nothing_here_reaches_into_another_company(db_session, bus):
    company, opportunity = await _world(db_session)
    other, elsewhere = await _world(db_session)

    result = await _ask(
        bus,
        db_session,
        company,
        "ScoreOpportunity",
        {"opportunity_id": str(elsewhere.id), "score": "9"},
    )
    assert result.record.outcome == "refused"
    assert elsewhere.score is None
    assert (
        await db_session.scalar(
            select(BusinessProposal).where(BusinessProposal.company_id == other.id)
        )
        is None
    )
