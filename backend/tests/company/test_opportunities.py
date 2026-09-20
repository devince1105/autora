"""T-611: the company's memory of what it might do, and what it decided about it.

Two state machines with no scheduler between them. What these tests hold onto:

- a rejected opportunity is kept, with the reason — that is the point of the table;
- a submitted proposal cannot be edited, only superseded by a newer version;
- an approved opportunity names the business it became, and the database refuses one that
  does not.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, StatementError

from autora.company import opportunities as opp
from autora.db.models import (
    BusinessProposal,
    BusinessUnit,
    EventRecord,
    Opportunity,
    OpportunitySignal,
    OpportunityState,
    Project,
    ProjectState,
    ProposalState,
    StateTransition,
)
from autora.runtime.actor import Actor
from autora.runtime.fsm import IllegalTransition
from tests.conftest import unique_company

CEO = Actor.agent(uuid.uuid4())
OPERATOR = Actor.human("operator")


async def _company(session):
    return await unique_company(session, "opportunities")


async def _discover(session, company, key="ai_english", **kw):
    return await opp.discover(
        session,
        company_id=company.id,
        key=key,
        title="AI English learning",
        thesis="People pay for lessons; a model can teach at the margin of zero.",
        market="Taiwan, adult learners",
        actor=CEO,
        **kw,
    )


async def _events(session, company_id, *types):
    rows = await session.scalars(
        select(EventRecord.event_type)
        .where(EventRecord.company_id == company_id, EventRecord.event_type.in_(types))
        .order_by(EventRecord.seq)
    )
    return list(rows)


# --- opportunities -------------------------------------------------------------------------


async def test_an_opportunity_exists_before_anything_is_spent_on_it(db_session):
    company = await _company(db_session)
    opportunity = await _discover(db_session, company)

    assert opportunity.state == OpportunityState.DISCOVERED.value
    assert opportunity.score is None and opportunity.decided_at is None
    assert await _events(db_session, company.id, "OPPORTUNITY_DISCOVERED") == [
        "OPPORTUNITY_DISCOVERED"
    ]
    # and the company knows it already looked at this one
    with pytest.raises(opp.DuplicateOpportunity):
        await _discover(db_session, company)


async def test_signals_accumulate_and_the_company_does_not_read_them(db_session):
    company = await _company(db_session)
    opportunity = await _discover(db_session, company)
    for i, (source, summary) in enumerate(
        [("search", "three competitors, all above $20/mo"), ("our own data", "readers ask for it")]
    ):
        await opp.add_signal(
            db_session,
            opportunity,
            source=source,
            summary=summary,
            metric="competitors" if i == 0 else None,
            value=Decimal("3") if i == 0 else None,
            actor=CEO,
        )

    signals = (
        await db_session.scalars(
            select(OpportunitySignal).where(OpportunitySignal.opportunity_id == opportunity.id)
        )
    ).all()
    assert len(signals) == 2
    assert {s.source for s in signals} == {"search", "our own data"}
    assert signals[0].observed_at is not None
    # the state did not move: evidence is not a decision
    assert opportunity.state == OpportunityState.DISCOVERED.value


async def test_scoring_compares_but_does_not_decide(db_session):
    company = await _company(db_session)
    opportunity = await _discover(db_session, company)
    await opp.score(db_session, opportunity, value=Decimal("7.5"), actor=CEO, reason="cheap entry")
    await opp.score(db_session, opportunity, value=Decimal("4"), actor=CEO, reason="on reflection")

    assert opportunity.score == Decimal("4.00")
    assert opportunity.state == OpportunityState.DISCOVERED.value
    scored = (
        await db_session.scalars(
            select(EventRecord)
            .where(EventRecord.company_id == company.id)
            .where(EventRecord.event_type == "OPPORTUNITY_SCORED")
            .order_by(EventRecord.seq)
        )
    ).all()
    # the column keeps two decimals; the event carries the number as it was given
    assert [e.payload["score"] for e in scored] == ["7.5", "4"]
    # the change is visible, not just the latest number (as a number: the column keeps two
    # decimals, the payload keeps what it was handed)
    assert Decimal(scored[1].payload["previous"]) == Decimal("7.5")


async def test_it_moves_forward_only(db_session):
    company = await _company(db_session)
    opportunity = await _discover(db_session, company)
    await opp.advance(db_session, opportunity, to=OpportunityState.EVALUATING, actor=CEO)
    await opp.advance(db_session, opportunity, to=OpportunityState.VALIDATING, actor=CEO)

    with pytest.raises(IllegalTransition):
        await opp.advance(db_session, opportunity, to=OpportunityState.EVALUATING, actor=CEO)
    assert opportunity.state == OpportunityState.VALIDATING.value
    audit = (
        await db_session.scalars(
            select(StateTransition)
            .where(StateTransition.entity_id == opportunity.id)
            .order_by(StateTransition.at)
        )
    ).all()
    assert [(t.from_state, t.to_state) for t in audit] == [
        ("DISCOVERED", "EVALUATING"),
        ("EVALUATING", "VALIDATING"),
    ]


async def test_a_rejection_is_kept_with_its_reason(db_session):
    """The company pays twice for what it forgets it already decided."""
    company = await _company(db_session)
    opportunity = await _discover(db_session, company, key="ai_support")
    await opp.advance(db_session, opportunity, to=OpportunityState.EVALUATING, actor=CEO)
    await opp.advance(
        db_session,
        opportunity,
        to=OpportunityState.REJECTED,
        actor=CEO,
        reason="acquisition cost is higher than a year of revenue",
    )

    assert opportunity.state == OpportunityState.REJECTED.value
    assert opportunity.decided_at is not None
    assert opportunity.decided_by["kind"] == "agent"
    assert "acquisition cost" in opportunity.decision_reason
    [event] = (
        await db_session.scalars(
            select(EventRecord).where(
                EventRecord.company_id == company.id,
                EventRecord.event_type == "OPPORTUNITY_REJECTED",
            )
        )
    ).all()
    assert event.payload["from_state"] == "EVALUATING"
    # and it is out of the way of the live ones
    assert await opp.open_opportunities(db_session, company.id) == []
    assert await opp.by_key(db_session, company.id, "ai_support") is opportunity


async def test_an_approved_opportunity_must_name_the_business_it_became(db_session):
    company = await _company(db_session)
    opportunity = await _discover(db_session, company)
    await opp.advance(db_session, opportunity, to=OpportunityState.EVALUATING, actor=OPERATOR)

    with pytest.raises(opp.OpportunityError):
        await opp.advance(db_session, opportunity, to=OpportunityState.APPROVED, actor=OPERATOR)

    unit = BusinessUnit(company_id=company.id, key="ai_english", name="AI English", state="ACTIVE")
    db_session.add(unit)
    await db_session.flush()
    await opp.advance(
        db_session,
        opportunity,
        to=OpportunityState.APPROVED,
        actor=OPERATOR,
        business_unit_id=unit.id,
        reason="the validation project hit its numbers",
    )
    assert opportunity.business_unit_id == unit.id


async def test_the_database_refuses_an_approved_opportunity_with_no_business(db_session):
    """The service checks it; so does the table, because a bug must not be able to write it."""
    company = await _company(db_session)
    opportunity = await _discover(db_session, company)
    opportunity.state = OpportunityState.APPROVED.value
    opportunity.decided_at = datetime.now(UTC)
    with pytest.raises((IntegrityError, StatementError)):
        await db_session.flush()


async def test_an_opportunity_nobody_decided_expires(db_session):
    company = await _company(db_session)
    stale = await _discover(
        db_session, company, key="stale", expires_at=datetime.now(UTC) - timedelta(days=1)
    )
    fresh = await _discover(
        db_session, company, key="fresh", expires_at=datetime.now(UTC) + timedelta(days=30)
    )
    forever = await _discover(db_session, company, key="forever")

    expired = await opp.expire_due(db_session, company.id)

    assert [o.key for o in expired] == ["stale"]
    assert stale.state == OpportunityState.EXPIRED.value
    assert stale.decided_by["id"] == "opportunities"  # the clock decided, not a person
    assert {o.key for o in await opp.open_opportunities(db_session, company.id)} == {
        fresh.key,
        forever.key,
    }
    # running it again changes nothing: it is already out
    assert await opp.expire_due(db_session, company.id) == []


async def test_the_open_ones_come_back_best_first(db_session):
    company = await _company(db_session)
    low = await _discover(db_session, company, key="low")
    high = await _discover(db_session, company, key="high")
    unscored = await _discover(db_session, company, key="unscored")
    await opp.score(db_session, low, value=Decimal("2"), actor=CEO)
    await opp.score(db_session, high, value=Decimal("9"), actor=CEO)

    ranked = await opp.open_opportunities(db_session, company.id)
    assert [o.key for o in ranked] == ["high", "low", "unscored"]
    assert unscored.score is None  # unscored last, not zero: they are not the same thing


# --- proposals ------------------------------------------------------------------------------


async def test_one_opportunity_can_have_several_proposals(db_session):
    company = await _company(db_session)
    opportunity = await _discover(db_session, company)
    app = await opp.draft_proposal(
        db_session,
        opportunity,
        title="Subscription app",
        actor=CEO,
        business_model="subscription",
        estimated_startup_cost=Decimal("2000"),
    )
    licence = await opp.draft_proposal(
        db_session, opportunity, title="B2B licence", actor=CEO, business_model="licence"
    )

    assert (app.version, licence.version) == (1, 2)
    assert [p.title for p in await opp.proposals_for(db_session, opportunity.id)] == [
        "Subscription app",
        "B2B licence",
    ]
    assert all(p.state == ProposalState.DRAFT.value for p in (app, licence))


async def test_submitting_freezes_this_one_and_supersedes_the_others(db_session):
    company = await _company(db_session)
    opportunity = await _discover(db_session, company)
    first = await opp.draft_proposal(db_session, opportunity, title="v1", actor=CEO)
    second = await opp.draft_proposal(db_session, opportunity, title="v2", actor=CEO)

    await opp.submit(db_session, first, actor=CEO)
    assert first.state == ProposalState.SUBMITTED.value
    # the other draft is untouched: it is an alternative, not a competitor yet
    assert second.state == ProposalState.DRAFT.value

    await opp.submit(db_session, second, actor=CEO)
    assert second.state == ProposalState.SUBMITTED.value
    assert first.state == ProposalState.SUPERSEDED.value, "two live proposals is one too many"
    assert await _events(db_session, company.id, "PROPOSAL_SUPERSEDED") == ["PROPOSAL_SUPERSEDED"]
    latest = await opp.latest_proposal(db_session, opportunity.id, state=ProposalState.SUBMITTED)
    assert latest.id == second.id


async def test_a_decided_proposal_cannot_be_decided_again(db_session):
    company = await _company(db_session)
    opportunity = await _discover(db_session, company)
    proposal = await opp.draft_proposal(
        db_session, opportunity, title="Subscription app", actor=CEO
    )
    await opp.submit(db_session, proposal, actor=CEO)
    await opp.decide(
        db_session,
        proposal,
        outcome=ProposalState.APPROVED,
        actor=OPERATOR,
        reason="worth $2000 to find out",
    )

    assert proposal.state == ProposalState.APPROVED.value
    assert proposal.decided_at is not None and "worth $2000" in proposal.decision_reason
    with pytest.raises(IllegalTransition):
        await opp.decide(
            db_session, proposal, outcome=ProposalState.REJECTED, actor=OPERATOR, reason="changed"
        )
    [decided] = (
        await db_session.scalars(
            select(EventRecord).where(
                EventRecord.company_id == company.id,
                EventRecord.event_type == "PROPOSAL_DECIDED",
            )
        )
    ).all()
    assert decided.payload["outcome"] == "APPROVED"
    assert decided.aggregate_type == "business_proposal"


async def test_a_draft_cannot_be_approved_without_being_submitted(db_session):
    company = await _company(db_session)
    opportunity = await _discover(db_session, company)
    proposal = await opp.draft_proposal(db_session, opportunity, title="v1", actor=CEO)
    with pytest.raises(IllegalTransition):
        await opp.decide(
            db_session, proposal, outcome=ProposalState.APPROVED, actor=OPERATOR, reason="skip"
        )


async def test_exploring_is_an_ordinary_project(db_session):
    """§4: the business loop needs no new execution mechanism, only one nullable column."""
    company = await _company(db_session)
    opportunity = await _discover(db_session, company)
    project = Project(
        company_id=company.id,
        opportunity_id=opportunity.id,
        name="Validate AI English",
        state=ProjectState.ACTIVE.value,
        kill_criteria={"auto_pause_if": {"metric": "cost_usd", "op": ">", "value": 50}},
    )
    db_session.add(project)
    await db_session.flush()

    found = await db_session.scalar(select(Project).where(Project.opportunity_id == opportunity.id))
    assert found is project
    assert found.kill_criteria["auto_pause_if"]["value"] == 50  # the same stop-loss as any project
    # and an ordinary project points at no opportunity at all
    ordinary = Project(
        company_id=company.id,
        name="Run the newsroom",
        state="ACTIVE",
        kill_criteria={},  # an active project must carry criteria, opportunity or not
    )
    db_session.add(ordinary)
    await db_session.flush()
    assert ordinary.opportunity_id is None


async def test_proposals_and_opportunities_are_per_company(db_session):
    one = await _company(db_session)
    two = await _company(db_session)
    await _discover(db_session, one, key="shared_key")
    await _discover(db_session, two, key="shared_key")  # the same idea in two companies is fine

    rows = (
        await db_session.scalars(select(Opportunity).where(Opportunity.company_id == one.id))
    ).all()
    assert len(rows) == 1
    assert (
        await db_session.scalar(
            select(BusinessProposal).where(BusinessProposal.company_id == one.id)
        )
        is None
    )


# --- what the CEO sees, and what the cycle does on its own -------------------------------------


async def test_the_snapshot_carries_what_the_company_might_do(db_session):
    """T-611: the opportunities reach the decision, with their proposals and what they cost."""
    from autora.company.ledger import Ledger
    from autora.company.reporting import Reporting
    from autora.company.snapshot import SnapshotBuilder

    company = await _company(db_session)
    weak = await _discover(db_session, company, key="weak")
    strong = await _discover(db_session, company, key="strong")
    await opp.score(db_session, weak, value=Decimal("2"), actor=CEO)
    await opp.score(db_session, strong, value=Decimal("9"), actor=CEO)
    await opp.add_signal(
        db_session, strong, source="search", summary="three competitors", actor=CEO
    )
    proposal = await opp.draft_proposal(
        db_session,
        strong,
        title="Subscription app",
        actor=CEO,
        business_model="subscription",
        estimated_startup_cost=Decimal("2000"),
    )
    await opp.submit(db_session, proposal, actor=CEO)
    exploring = Project(
        company_id=company.id,
        opportunity_id=strong.id,
        name="Find out",
        state=ProjectState.ACTIVE.value,
        kill_criteria={},
    )
    ordinary = Project(
        company_id=company.id, name="Keep the lights on", state="ACTIVE", kill_criteria={}
    )
    db_session.add_all([exploring, ordinary])
    await db_session.flush()

    snapshot = await SnapshotBuilder(Reporting(), Ledger()).build(db_session, company.id)

    assert [o.key for o in snapshot.opportunities] == ["strong", "weak"]  # best first
    first = snapshot.opportunities[0]
    assert first.signals == 1 and first.score == Decimal("9.00")
    assert [(p.version, p.state) for p in first.proposals] == [(1, "SUBMITTED")]
    assert [p.name for p in first.exploring] == ["Find out"]
    # the exploration project is shown once, under what it explores
    assert [p.name for p in snapshot.company_work] == ["Keep the lights on"]


async def test_a_rejected_opportunity_leaves_the_snapshot_but_not_the_table(db_session):
    from autora.company.ledger import Ledger
    from autora.company.reporting import Reporting
    from autora.company.snapshot import SnapshotBuilder

    company = await _company(db_session)
    opportunity = await _discover(db_session, company, key="ai_support")
    await opp.advance(
        db_session, opportunity, to=OpportunityState.REJECTED, actor=CEO, reason="too expensive"
    )

    snapshot = await SnapshotBuilder(Reporting(), Ledger()).build(db_session, company.id)
    assert snapshot.opportunities == []
    assert (await opp.by_key(db_session, company.id, "ai_support")).decision_reason


async def test_the_cycle_expires_stale_opportunities_by_itself(db_session):
    """No scheduler of its own: the daily cycle is what moves this, and it costs no model call."""
    from autora.db.models import Cycle, CycleStage, ModelCall

    company = await _company(db_session)
    stale = await _discover(
        db_session, company, key="stale", expires_at=datetime.now(UTC) - timedelta(hours=1)
    )
    cycle = Cycle(company_id=company.id, seq=1, stage=CycleStage.REVIEWING.value)
    db_session.add(cycle)
    await db_session.flush()

    await opp.stage_hook()(db_session, cycle)

    assert stale.state == OpportunityState.EXPIRED.value
    calls = await db_session.scalar(select(ModelCall).where(ModelCall.company_id == company.id))
    assert calls is None, "expiring an opportunity is a date comparison, not a decision"
