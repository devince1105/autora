"""The commands of the business loop (logs/ARCHITECTURE_V2_1.md §5–§6, T-611).

Eight verbs, on the same pipeline as every other: parsed, decided by the policy engine,
checked by the handler, recorded in ``commands_log``. They are separate from
:mod:`autora.company.verbs` only because they answer a different question — not "what should
we do today" but "what business should we be in".

Who may do what is declared in :mod:`autora.company.policy`, and the line it draws is §6's:
**a person decides the moment something becomes irreversible or spends real capital.**
Exploring, scoring and validating are cheap and reversible, so the CEO does them alone;
opening a business and winding one down are a person's, always.

The one that matters most is ``CreateBusinessUnit``. It takes a **proposal id** and nothing
else of substance: the name, the product, the kill criteria and the money all come from the
frozen proposal. That is what makes opening a business repeatable and auditable — the approval
points at a document, not at a scattering of arguments somebody typed.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select

from autora.company import events as company_events
from autora.company import opportunities as opportunities_service
from autora.company.commands import CommandBus, CommandSpec, Context, Refused
from autora.company.ledger import Ledger
from autora.company.verbs import AllocateBudget, allocate_budget
from autora.db.models import (
    BusinessProposal,
    BusinessUnit,
    BusinessUnitState,
    Opportunity,
    OpportunityState,
    Product,
    ProductState,
    Project,
    ProposalState,
    TransactionKind,
    TransactionSource,
)
from autora.infra.money import format_money
from autora.runtime.events.outbox import emit
from autora.runtime.events.schema import new_event

CAPITAL_CATEGORY = "capital_allocation"
KEY_PATTERN = r"^[a-z][a-z0-9_]*$"


# --- payloads ---------------------------------------------------------------------------------


class AllocateExplorationBudget(BaseModel):
    """Money for finding out. The project must be one that explores or validates something."""

    project_id: uuid.UUID
    amount: Decimal = Field(ge=0)
    period: str = "cycle"


class ScoreOpportunity(BaseModel):
    """A number for comparing. Recording it decides nothing."""

    opportunity_id: uuid.UUID
    score: Decimal
    reason: str | None = None


class AdvanceOpportunity(BaseModel):
    """Move an opportunity one state forward. APPROVED is a person's, and needs a business."""

    opportunity_id: uuid.UUID
    to_state: str
    reason: str | None = None
    business_unit_id: uuid.UUID | None = None


class RejectOpportunity(BaseModel):
    opportunity_id: uuid.UUID
    reason: str = Field(min_length=1)


class DraftProposal(BaseModel):
    """What the company would do about an opportunity. A document, not a commitment."""

    opportunity_id: uuid.UUID
    title: str = Field(min_length=1, max_length=200)
    business_model: str | None = Field(default=None, max_length=2000)
    target_market: str | None = Field(default=None, max_length=2000)
    target_customer: str | None = Field(default=None, max_length=2000)
    proposed_product: dict[str, Any] | None = None
    expected_revenue_model: str | None = Field(default=None, max_length=2000)
    expected_margin: Decimal | None = Field(default=None, ge=0, le=1)
    estimated_startup_cost: Decimal | None = Field(default=None, ge=0)
    estimated_monthly_cost: Decimal | None = Field(default=None, ge=0)
    required_agents: dict[str, Any] | None = None
    required_capabilities: dict[str, Any] | None = None
    risks: dict[str, Any] | None = None
    validation_plan: dict[str, Any] | None = None
    kill_criteria: dict[str, Any]
    """Required, like a project's: a business nobody knows how to stop is not a proposal."""


class SubmitProposal(BaseModel):
    """Freeze a draft and put it up for decision. Still not a commitment — that is a person."""

    proposal_id: uuid.UUID


class CreateBusinessUnit(BaseModel):
    """Open a business from an approved proposal, and put capital behind it."""

    proposal_id: uuid.UUID
    capital: Decimal = Field(default=Decimal("0"), ge=0)
    key: str | None = Field(default=None, pattern=KEY_PATTERN)
    reason: str | None = None


class ScaleBusinessUnit(BaseModel):
    """More capital for a business that is running (or less, with a negative amount)."""

    business_unit_id: uuid.UUID
    amount: Decimal
    reason: str | None = None


class PauseBusinessUnit(BaseModel):
    business_unit_id: uuid.UUID
    reason: str = Field(min_length=1)


class WindDownBusinessUnit(BaseModel):
    """The end of a business. Irreversible, so only a person ever gets here."""

    business_unit_id: uuid.UUID
    reason: str = Field(min_length=1)


# --- handlers ---------------------------------------------------------------------------------


async def allocate_exploration_budget(
    ctx: Context, command: AllocateExplorationBudget
) -> dict[str, Any]:
    """The same envelope as any project's, with one extra condition: it must be exploration.

    Keeping it a separate verb is what lets a company hold exploration to its own (smaller)
    limit without touching what the CEO may spend on the businesses it already runs.
    """
    project = await ctx.session.get(Project, command.project_id)
    if project is None or project.company_id != ctx.company_id:
        raise Refused(f"no project {command.project_id} in this company")
    if project.opportunity_id is None:
        raise Refused(
            f"project {project.name!r} explores no opportunity; "
            "ordinary work is funded with AllocateBudget"
        )
    result = await allocate_budget(
        ctx,
        AllocateBudget(
            amount=command.amount,
            period=command.period,  # type: ignore[arg-type]
            project_id=command.project_id,
        ),
    )
    return {**result, "opportunity_id": str(project.opportunity_id)}


async def score_opportunity(ctx: Context, command: ScoreOpportunity) -> dict[str, Any]:
    opportunity = await _opportunity(ctx, command.opportunity_id)
    await opportunities_service.score(
        ctx.session, opportunity, value=command.score, actor=ctx.actor, reason=command.reason
    )
    return {"opportunity_id": str(opportunity.id), "score": str(command.score)}


async def advance_opportunity(ctx: Context, command: AdvanceOpportunity) -> dict[str, Any]:
    opportunity = await _opportunity(ctx, command.opportunity_id)
    try:
        to = OpportunityState(command.to_state)
    except ValueError:
        raise Refused(f"{command.to_state!r} is not a state an opportunity has") from None
    if to is OpportunityState.REJECTED:
        raise Refused("use RejectOpportunity, which records why")
    try:
        await opportunities_service.advance(
            ctx.session,
            opportunity,
            to=to,
            actor=ctx.actor,
            reason=command.reason,
            business_unit_id=command.business_unit_id,
        )
    except opportunities_service.OpportunityError as refused:
        raise Refused(str(refused)) from refused
    return {"opportunity_id": str(opportunity.id), "state": opportunity.state}


async def reject_opportunity(ctx: Context, command: RejectOpportunity) -> dict[str, Any]:
    """Say no, and say why. The row stays: it is what stops the company paying to decide twice."""
    opportunity = await _opportunity(ctx, command.opportunity_id)
    await opportunities_service.advance(
        ctx.session,
        opportunity,
        to=OpportunityState.REJECTED,
        actor=ctx.actor,
        reason=command.reason,
    )
    return {"opportunity_id": str(opportunity.id), "state": opportunity.state}


async def draft_proposal(ctx: Context, command: DraftProposal) -> dict[str, Any]:
    """Write a proposal for an open opportunity.

    Cheap and reversible: it is a document, so nobody has to approve it. What it costs is the
    exploration project's budget, like the rest of finding out (ARCHITECTURE_V2_1 §6).
    """
    opportunity = await _opportunity(ctx, command.opportunity_id)
    if opportunity.state not in opportunities_service.OPEN_STATES:
        raise Refused(
            f"opportunity {opportunity.key} is {opportunity.state}; it was decided already"
        )
    if not command.kill_criteria:
        raise Refused("a proposal must say what would make this business not worth running")
    fields = command.model_dump(exclude={"opportunity_id", "title"}, exclude_none=True)
    proposal = await opportunities_service.draft_proposal(
        ctx.session,
        opportunity,
        title=command.title,
        actor=ctx.actor,
        run_id=ctx.run_id,
        **fields,
    )
    return {
        "proposal_id": str(proposal.id),
        "version": proposal.version,
        "opportunity_id": str(opportunity.id),
    }


async def submit_proposal(ctx: Context, command: SubmitProposal) -> dict[str, Any]:
    """Put a draft up for decision. Any proposal already submitted for it is superseded."""
    proposal = await ctx.session.get(BusinessProposal, command.proposal_id)
    if proposal is None or proposal.company_id != ctx.company_id:
        raise Refused(f"no proposal {command.proposal_id} in this company")
    if proposal.state != ProposalState.DRAFT.value:
        raise Refused(f"proposal v{proposal.version} is {proposal.state}; only a draft is put up")
    await opportunities_service.submit(ctx.session, proposal, actor=ctx.actor)
    return {"proposal_id": str(proposal.id), "version": proposal.version, "state": proposal.state}


async def create_business_unit(ctx: Context, command: CreateBusinessUnit) -> dict[str, Any]:
    """Open a business from a submitted proposal, exactly as the proposal describes it.

    In one transaction: the business, its first product, the proposal marked approved, the
    opportunity marked approved and pointed at the business, and the capital moved in as a
    transfer. Either the whole thing happened or none of it did.
    """
    proposal = await ctx.session.get(BusinessProposal, command.proposal_id)
    if proposal is None or proposal.company_id != ctx.company_id:
        raise Refused(f"no proposal {command.proposal_id} in this company")
    if proposal.state != ProposalState.SUBMITTED.value:
        raise Refused(
            f"proposal v{proposal.version} is {proposal.state}; only a submitted one can be "
            "approved (a draft is still being written, a decided one is already answered)"
        )
    opportunity = await ctx.session.get(Opportunity, proposal.opportunity_id)
    assert opportunity is not None
    key = command.key or opportunity.key
    clash = await ctx.session.scalar(
        select(BusinessUnit).where(
            BusinessUnit.company_id == ctx.company_id, BusinessUnit.key == key
        )
    )
    if clash is not None:
        raise Refused(f"the company already has a business {key!r}")

    unit = BusinessUnit(
        company_id=ctx.company_id,
        key=key,
        name=proposal.title,
        mission=proposal.business_model,
        state=BusinessUnitState.ACTIVE.value,
        kill_criteria=proposal.kill_criteria,
    )
    ctx.session.add(unit)
    await ctx.session.flush()
    await emit(
        ctx.session,
        new_event(
            company_events.BusinessUnitCreated(
                key=unit.key,
                name=unit.name,
                state=unit.state,
                proposal_id=proposal.id,
                capital=command.capital or None,
            ),
            company_id=ctx.company_id,
            actor=ctx.actor,
            aggregate_type="business_unit",
            aggregate_id=unit.id,
        ),
    )

    product_id = await _first_product(ctx, unit, proposal)
    proposal.business_unit_id = unit.id
    await opportunities_service.decide(
        ctx.session,
        proposal,
        outcome=ProposalState.APPROVED,
        actor=ctx.actor,
        reason=command.reason,
    )
    await opportunities_service.advance(
        ctx.session,
        opportunity,
        to=OpportunityState.APPROVED,
        actor=ctx.actor,
        reason=command.reason or f"approved proposal v{proposal.version}",
        business_unit_id=unit.id,
    )
    if command.capital > 0:
        await Ledger().record(
            ctx.session,
            company_id=ctx.company_id,
            kind=TransactionKind.TRANSFER,
            category=CAPITAL_CATEGORY,
            amount=command.capital,
            business_unit_id=unit.id,
            idempotency_key=f"business_unit:{unit.id}:capital",
            actor=ctx.actor,
            source=TransactionSource.HUMAN,
            ref_type="business_proposal",
            ref_id=proposal.id,
            memo=f"opening capital for {unit.name}",
        )
    return {
        "business_unit_id": str(unit.id),
        "key": unit.key,
        "product_id": product_id,
        "proposal_version": proposal.version,
        "capital": str(command.capital),
    }


async def scale_business_unit(ctx: Context, command: ScaleBusinessUnit) -> dict[str, Any]:
    unit = await _unit(ctx, command.business_unit_id)
    if unit.state != BusinessUnitState.ACTIVE.value:
        raise Refused(f"{unit.name} is {unit.state}: capital goes to a business that is running")
    if command.amount == 0:
        raise Refused("scaling by nothing is not a decision")
    await emit(
        ctx.session,
        new_event(
            company_events.BusinessUnitScaled(
                key=unit.key, name=unit.name, amount=command.amount, reason=command.reason
            ),
            company_id=ctx.company_id,
            actor=ctx.actor,
            aggregate_type="business_unit",
            aggregate_id=unit.id,
        ),
    )
    moved = await Ledger().record(
        ctx.session,
        company_id=ctx.company_id,
        kind=TransactionKind.TRANSFER,
        category=CAPITAL_CATEGORY,
        amount=abs(command.amount),
        business_unit_id=unit.id,
        idempotency_key=f"business_unit:{unit.id}:scale:{ctx.idempotency_key}",
        actor=ctx.actor,
        source=TransactionSource.HUMAN if ctx.actor.kind == "human" else TransactionSource.SYSTEM,
        memo=command.reason or f"capital change for {unit.name}",
    )
    return {
        "business_unit_id": str(unit.id),
        "amount": str(command.amount),
        "transaction_id": str(moved.id) if moved else None,
    }


async def pause_business_unit(ctx: Context, command: PauseBusinessUnit) -> dict[str, Any]:
    """Stop a business spending. Not the end of it — only a person ends a business."""
    unit = await _unit(ctx, command.business_unit_id)
    if unit.state != BusinessUnitState.ACTIVE.value:
        raise Refused(f"{unit.name} is already {unit.state}")
    unit.state = BusinessUnitState.PAUSED.value
    await ctx.session.flush()
    await emit(
        ctx.session,
        new_event(
            company_events.BusinessUnitPaused(
                key=unit.key,
                name=unit.name,
                reason=command.reason,
                trigger="ceo" if ctx.actor.kind == "agent" else "human",
            ),
            company_id=ctx.company_id,
            actor=ctx.actor,
            aggregate_type="business_unit",
            aggregate_id=unit.id,
        ),
    )
    return {"business_unit_id": str(unit.id), "state": unit.state}


async def wind_down_business_unit(ctx: Context, command: WindDownBusinessUnit) -> dict[str, Any]:
    """Close a business for good. Its products retire with it."""
    unit = await _unit(ctx, command.business_unit_id)
    if unit.state == BusinessUnitState.WOUND_DOWN.value:
        raise Refused(f"{unit.name} is already wound down")
    unit.state = BusinessUnitState.WOUND_DOWN.value
    products = (
        await ctx.session.scalars(
            select(Product).where(
                Product.business_unit_id == unit.id, Product.state != ProductState.RETIRED.value
            )
        )
    ).all()
    for product in products:
        product.state = ProductState.RETIRED.value
    await ctx.session.flush()
    await emit(
        ctx.session,
        new_event(
            company_events.BusinessUnitWoundDown(
                key=unit.key, name=unit.name, reason=command.reason
            ),
            company_id=ctx.company_id,
            actor=ctx.actor,
            aggregate_type="business_unit",
            aggregate_id=unit.id,
        ),
    )
    return {
        "business_unit_id": str(unit.id),
        "state": unit.state,
        "products_retired": len(products),
    }


# --- plumbing ------------------------------------------------------------------------------------


async def _opportunity(ctx: Context, opportunity_id: uuid.UUID) -> Opportunity:
    opportunity = await ctx.session.get(Opportunity, opportunity_id)
    if opportunity is None or opportunity.company_id != ctx.company_id:
        raise Refused(f"no opportunity {opportunity_id} in this company")
    return opportunity


async def _unit(ctx: Context, business_unit_id: uuid.UUID) -> BusinessUnit:
    unit = await ctx.session.get(BusinessUnit, business_unit_id)
    if unit is None or unit.company_id != ctx.company_id:
        raise Refused(f"no business unit {business_unit_id} in this company")
    return unit


async def _first_product(
    ctx: Context, unit: BusinessUnit, proposal: BusinessProposal
) -> str | None:
    """The product the proposal described, if it described one. A business may open without."""
    described = proposal.proposed_product or {}
    key = described.get("key")
    if not key:
        return None
    product = Product(
        company_id=ctx.company_id,
        business_unit_id=unit.id,
        key=key,
        name=described.get("name") or proposal.title,
        state=ProductState.DRAFT.value,
    )
    ctx.session.add(product)
    await ctx.session.flush()
    await emit(
        ctx.session,
        new_event(
            company_events.ProductCreated(
                key=product.key,
                name=product.name,
                business_unit_id=unit.id,
                state=product.state,
            ),
            company_id=ctx.company_id,
            actor=ctx.actor,
            aggregate_type="product",
            aggregate_id=product.id,
        ),
    )
    return str(product.id)


def register(bus: CommandBus) -> None:
    for spec in (
        CommandSpec(
            "AllocateExplorationBudget",
            AllocateExplorationBudget,
            "allocate_exploration_budget",
            allocate_exploration_budget,
            summary=lambda c: f"{format_money(c.amount)} per {c.period} to explore",
        ),
        CommandSpec(
            "ScoreOpportunity",
            ScoreOpportunity,
            "score_opportunity",
            score_opportunity,
            summary=lambda c: f"Score {c.opportunity_id} at {c.score}",
        ),
        CommandSpec(
            "AdvanceOpportunity",
            AdvanceOpportunity,
            "advance_opportunity",
            advance_opportunity,
            summary=lambda c: f"Move {c.opportunity_id} to {c.to_state}",
        ),
        CommandSpec(
            "RejectOpportunity",
            RejectOpportunity,
            "reject_opportunity",
            reject_opportunity,
            summary=lambda c: f"Reject {c.opportunity_id}: {c.reason}",
        ),
        CommandSpec(
            "DraftProposal",
            DraftProposal,
            "draft_proposal",
            draft_proposal,
            summary=lambda c: f"Draft a proposal: {c.title}",
        ),
        CommandSpec(
            "SubmitProposal",
            SubmitProposal,
            "submit_proposal",
            submit_proposal,
            summary=lambda c: f"Put proposal {c.proposal_id} up for decision",
        ),
        CommandSpec(
            "CreateBusinessUnit",
            CreateBusinessUnit,
            "create_business_unit",
            create_business_unit,
            summary=lambda c: (
                f"Open a business from proposal {c.proposal_id} with {format_money(c.capital)}"
            ),
        ),
        CommandSpec(
            "ScaleBusinessUnit",
            ScaleBusinessUnit,
            "scale_business_unit",
            scale_business_unit,
            summary=lambda c: f"Move {format_money(c.amount)} of capital in {c.business_unit_id}",
        ),
        CommandSpec(
            "PauseBusinessUnit",
            PauseBusinessUnit,
            "pause_business_unit",
            pause_business_unit,
            summary=lambda c: f"Pause business {c.business_unit_id}: {c.reason}",
        ),
        CommandSpec(
            "WindDownBusinessUnit",
            WindDownBusinessUnit,
            "wind_down_business_unit",
            wind_down_business_unit,
            summary=lambda c: f"Wind down business {c.business_unit_id}: {c.reason}",
        ),
    ):
        bus.register(spec)
