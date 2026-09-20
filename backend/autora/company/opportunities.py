"""Deciding which business to be in (logs/ARCHITECTURE_V2_1.md §1–§2, §4; T-611).

Two state machines and nothing else. **Neither has a scheduler**, which is the whole design:
the business loop is much slower than the daily cycle and moves at most one step per cycle,
inside the stages that already exist. Exploring and validating are ordinary projects, so a
budget, a cost trail and kill criteria come for free (``projects.opportunity_id``).

    opportunity: DISCOVERED -> EVALUATING -> VALIDATING -> APPROVED
                          \\-> REJECTED        \\-> REJECTED    (and EXPIRED from any open state)

    proposal:    DRAFT -> SUBMITTED -> APPROVED / REJECTED
                       \\-> SUPERSEDED  (a newer version was submitted)

Two rules are worth stating out loud:

**A submitted proposal is frozen.** Editing one would make "we approved $2000 for this" a
question without an answer, so a change is a new version and the old one is superseded.

**A rejection is kept, not deleted.** The rejected opportunities are the company's memory of
what it already looked at; without them it pays to reach the same conclusion twice.

This module decides nothing by itself. The commands in :mod:`autora.company.verbs` are what the
CEO (or a person) uses, and the policy engine decides who may do which — approving a proposal
and opening a business are a person's, always.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autora.company import events as company_events
from autora.db.models import (
    BusinessProposal,
    Opportunity,
    OpportunitySignal,
    OpportunityState,
    ProposalState,
)
from autora.runtime.actor import Actor
from autora.runtime.events.outbox import emit
from autora.runtime.events.schema import new_event
from autora.runtime.fsm import StateMachine, transitions

OS = OpportunityState
PS = ProposalState

OPPORTUNITY_FSM = StateMachine(
    entity_type="opportunity",
    states=OpportunityState,
    initial=OS.DISCOVERED,
    transitions=transitions(
        {
            OS.DISCOVERED: [OS.EVALUATING, OS.REJECTED, OS.EXPIRED],
            OS.EVALUATING: [OS.VALIDATING, OS.APPROVED, OS.REJECTED, OS.EXPIRED],
            OS.VALIDATING: [OS.APPROVED, OS.REJECTED, OS.EXPIRED],
        }
    ),
)
"""EVALUATING may go straight to APPROVED: a company is allowed to back something without a
validation project, as long as a person approves it. What it may not do is go backwards."""

PROPOSAL_FSM = StateMachine(
    entity_type="business_proposal",
    states=ProposalState,
    initial=PS.DRAFT,
    transitions=transitions(
        {
            PS.DRAFT: [PS.SUBMITTED, PS.SUPERSEDED],
            PS.SUBMITTED: [PS.APPROVED, PS.REJECTED, PS.SUPERSEDED],
        }
    ),
)

OPEN_STATES = (OS.DISCOVERED.value, OS.EVALUATING.value, OS.VALIDATING.value)


class OpportunityError(Exception):
    pass


class DuplicateOpportunity(OpportunityError):
    def __init__(self, key: str):
        self.key = key
        super().__init__(f"the company already has an opportunity {key!r}")


class ProposalFrozen(OpportunityError):
    """A submitted proposal is a decision artifact: it is replaced, never edited."""

    def __init__(self, proposal: BusinessProposal):
        self.proposal = proposal
        super().__init__(f"proposal v{proposal.version} is {proposal.state} and cannot be changed")


# --- opportunities ----------------------------------------------------------------------------


async def discover(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    key: str,
    title: str,
    actor: Actor,
    thesis: str | None = None,
    market: str | None = None,
    expires_at: datetime | None = None,
    run_id: uuid.UUID | None = None,
) -> Opportunity:
    """Record something that might be a business. Does not commit."""
    if await by_key(session, company_id, key) is not None:
        raise DuplicateOpportunity(key)
    opportunity = Opportunity(
        company_id=company_id,
        key=key,
        title=title,
        thesis=thesis,
        market=market,
        state=OS.DISCOVERED.value,
        expires_at=expires_at,
        discovered_by_run_id=run_id,
    )
    session.add(opportunity)
    await session.flush()
    await _emit(
        session,
        opportunity,
        company_events.OpportunityDiscovered(key=key, title=title, thesis=thesis, market=market),
        actor,
    )
    return opportunity


async def add_signal(
    session: AsyncSession,
    opportunity: Opportunity,
    *,
    source: str,
    summary: str,
    actor: Actor,
    metric: str | None = None,
    value: Decimal | None = None,
    observed_at: datetime | None = None,
    evidence_ref: str | None = None,
    run_id: uuid.UUID | None = None,
) -> OpportunitySignal:
    """Attach one observation. The shell is the company's; the meaning is the domain's."""
    signal = OpportunitySignal(
        company_id=opportunity.company_id,
        opportunity_id=opportunity.id,
        source=source,
        summary=summary,
        metric=metric,
        value=value,
        observed_at=observed_at or datetime.now(UTC),
        evidence_ref=evidence_ref,
        recorded_by_run_id=run_id,
    )
    session.add(signal)
    await session.flush()
    await _emit(
        session,
        opportunity,
        company_events.OpportunitySignalRecorded(
            key=opportunity.key, source=source, summary=summary, metric=metric, value=value
        ),
        actor,
    )
    return signal


async def score(
    session: AsyncSession,
    opportunity: Opportunity,
    *,
    value: Decimal,
    actor: Actor,
    reason: str | None = None,
) -> Opportunity:
    """Record a comparison number. Scoring is not deciding: the state does not move."""
    previous = opportunity.score
    opportunity.score = value
    await session.flush()
    await _emit(
        session,
        opportunity,
        company_events.OpportunityScored(
            key=opportunity.key, score=value, previous=previous, reason=reason
        ),
        actor,
    )
    return opportunity


async def advance(
    session: AsyncSession,
    opportunity: Opportunity,
    *,
    to: OpportunityState,
    actor: Actor,
    reason: str | None = None,
    business_unit_id: uuid.UUID | None = None,
) -> Opportunity:
    """Move an opportunity forward. APPROVED requires the business it became."""
    was = opportunity.state
    if to is OS.APPROVED and business_unit_id is None:
        raise OpportunityError("an approved opportunity must name the business it became")
    if to in (OS.APPROVED, OS.REJECTED, OS.EXPIRED):
        opportunity.decided_at = datetime.now(UTC)
        opportunity.decided_by = actor.model_dump(mode="json")
        opportunity.decision_reason = reason
    if business_unit_id is not None:
        opportunity.business_unit_id = business_unit_id
    await OPPORTUNITY_FSM.transition(session, opportunity, to, actor=actor, reason=reason)
    payload: Any
    if to is OS.REJECTED:
        payload = company_events.OpportunityRejected(
            key=opportunity.key, from_state=was, reason=reason or "no reason given"
        )
    elif to is OS.EXPIRED:
        payload = company_events.OpportunityExpired(key=opportunity.key, from_state=was)
    else:
        payload = company_events.OpportunityAdvanced(
            key=opportunity.key, from_state=was, to_state=to.value, reason=reason
        )
    await _emit(session, opportunity, payload, actor)
    return opportunity


async def expire_due(
    session: AsyncSession, company_id: uuid.UUID, *, now: datetime | None = None
) -> list[Opportunity]:
    """Expire the open opportunities whose date has passed. Deterministic, no model involved.

    Registered on a cycle stage like the rest of the loop's housekeeping: an opportunity that
    nobody decided within its own window stops counting as live, and the company stops carrying
    stale conclusions in its snapshot.
    """
    now = now or datetime.now(UTC)
    due = (
        await session.scalars(
            select(Opportunity).where(
                Opportunity.company_id == company_id,
                Opportunity.state.in_(OPEN_STATES),
                Opportunity.expires_at.is_not(None),
                Opportunity.expires_at <= now,
            )
        )
    ).all()
    expired = []
    for opportunity in due:
        await advance(
            session,
            opportunity,
            to=OS.EXPIRED,
            actor=Actor.system("opportunities"),
            reason="nobody decided before it expired",
        )
        expired.append(opportunity)
    return expired


def stage_hook():
    """Registered on the way into REVIEWING, before the rules and the review.

    Expiring is arithmetic on a date, so it belongs with the other deterministic housekeeping —
    and it must happen *before* the CEO reads its snapshot, or the company spends a cycle
    comparing an opportunity whose conclusion has already gone stale.
    """

    async def expire(session: AsyncSession, cycle) -> None:
        await expire_due(session, cycle.company_id)

    return expire


async def by_key(session: AsyncSession, company_id: uuid.UUID, key: str) -> Opportunity | None:
    return await session.scalar(
        select(Opportunity).where(Opportunity.company_id == company_id, Opportunity.key == key)
    )


async def open_opportunities(
    session: AsyncSession, company_id: uuid.UUID, *, limit: int = 20
) -> list[Opportunity]:
    """The ones still in play, best score first — what the CEO compares."""
    rows = await session.scalars(
        select(Opportunity)
        .where(Opportunity.company_id == company_id, Opportunity.state.in_(OPEN_STATES))
        .order_by(Opportunity.score.desc().nullslast(), Opportunity.created_at)
        .limit(limit)
    )
    return list(rows)


# --- proposals ---------------------------------------------------------------------------------


async def draft_proposal(
    session: AsyncSession,
    opportunity: Opportunity,
    *,
    title: str,
    actor: Actor,
    run_id: uuid.UUID | None = None,
    **fields: Any,
) -> BusinessProposal:
    """Write a new proposal for an opportunity. Versions count up per opportunity."""
    last = await session.scalar(
        select(BusinessProposal.version)
        .where(BusinessProposal.opportunity_id == opportunity.id)
        .order_by(BusinessProposal.version.desc())
        .limit(1)
    )
    proposal = BusinessProposal(
        company_id=opportunity.company_id,
        opportunity_id=opportunity.id,
        version=(last or 0) + 1,
        state=PS.DRAFT.value,
        title=title,
        authored_by_run_id=run_id,
        **fields,
    )
    session.add(proposal)
    await session.flush()
    await _emit(
        session,
        opportunity,
        company_events.ProposalDrafted(
            opportunity_key=opportunity.key, version=proposal.version, title=title
        ),
        actor,
        aggregate=("business_proposal", proposal.id),
    )
    return proposal


async def submit(
    session: AsyncSession, proposal: BusinessProposal, *, actor: Actor
) -> BusinessProposal:
    """Freeze a proposal and put it up for decision.

    Any proposal already submitted for the same opportunity is superseded: a person decides
    between proposals by being shown one, so exactly one may be up for decision at a time.
    """
    opportunity = await session.get(Opportunity, proposal.opportunity_id)
    assert opportunity is not None
    # only the ones already up for decision: a sibling draft is an alternative somebody is
    # still writing, and submitting this one is not a reason to throw that away
    earlier = (
        await session.scalars(
            select(BusinessProposal).where(
                BusinessProposal.opportunity_id == proposal.opportunity_id,
                BusinessProposal.id != proposal.id,
                BusinessProposal.state == PS.SUBMITTED.value,
            )
        )
    ).all()
    await PROPOSAL_FSM.transition(session, proposal, PS.SUBMITTED, actor=actor)
    await _emit(
        session,
        opportunity,
        company_events.ProposalSubmitted(
            opportunity_key=opportunity.key,
            version=proposal.version,
            estimated_startup_cost=proposal.estimated_startup_cost,
        ),
        actor,
        aggregate=("business_proposal", proposal.id),
    )
    for older in earlier:
        await PROPOSAL_FSM.transition(
            session, older, PS.SUPERSEDED, actor=actor, reason=f"superseded by v{proposal.version}"
        )
        await _emit(
            session,
            opportunity,
            company_events.ProposalSuperseded(
                opportunity_key=opportunity.key,
                version=older.version,
                superseded_by_version=proposal.version,
            ),
            actor,
            aggregate=("business_proposal", older.id),
        )
    return proposal


async def decide(
    session: AsyncSession,
    proposal: BusinessProposal,
    *,
    outcome: ProposalState,
    actor: Actor,
    reason: str | None = None,
    approval_id: uuid.UUID | None = None,
) -> BusinessProposal:
    """Approve or reject a submitted proposal. Only a person gets here (see the policy)."""
    if outcome not in (PS.APPROVED, PS.REJECTED):
        raise OpportunityError(f"a proposal is approved or rejected, not {outcome}")
    opportunity = await session.get(Opportunity, proposal.opportunity_id)
    assert opportunity is not None
    proposal.decided_at = datetime.now(UTC)
    proposal.decision_reason = reason
    proposal.approval_id = approval_id
    await PROPOSAL_FSM.transition(session, proposal, outcome, actor=actor, reason=reason)
    await _emit(
        session,
        opportunity,
        company_events.ProposalDecided(
            opportunity_key=opportunity.key,
            version=proposal.version,
            outcome=outcome.value,  # type: ignore[arg-type]
            reason=reason,
        ),
        actor,
        aggregate=("business_proposal", proposal.id),
    )
    return proposal


async def latest_proposal(
    session: AsyncSession, opportunity_id: uuid.UUID, *, state: ProposalState | None = None
) -> BusinessProposal | None:
    stmt = select(BusinessProposal).where(BusinessProposal.opportunity_id == opportunity_id)
    if state is not None:
        stmt = stmt.where(BusinessProposal.state == state.value)
    return await session.scalar(stmt.order_by(BusinessProposal.version.desc()).limit(1))


async def proposals_for(session: AsyncSession, opportunity_id: uuid.UUID) -> list[BusinessProposal]:
    rows = await session.scalars(
        select(BusinessProposal)
        .where(BusinessProposal.opportunity_id == opportunity_id)
        .order_by(BusinessProposal.version)
    )
    return list(rows)


# --- plumbing ------------------------------------------------------------------------------------


async def _emit(
    session: AsyncSession,
    opportunity: Opportunity,
    payload: Any,
    actor: Actor,
    *,
    aggregate: tuple[str, uuid.UUID] | None = None,
) -> None:
    kind, ident = aggregate or ("opportunity", opportunity.id)
    await emit(
        session,
        new_event(
            payload,
            company_id=opportunity.company_id,
            actor=actor,
            aggregate_type=kind,
            aggregate_id=ident,
        ),
    )
