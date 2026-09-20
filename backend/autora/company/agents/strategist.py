"""The strategist: turns an opportunity into a proposal somebody can decide about (T-611).

The company's business loop had a hole in it. Opportunities could be discovered, scored and
advanced; a proposal could be approved and become a business — but **nothing wrote the
proposal**, so the loop only closed when a person typed one. This is the agent that closes it.

It belongs to the company, not to a domain (ARCHITECTURE_V2_1 §3): "what would we sell, to
whom, for how much, and what would make this not worth doing" is the same question in any
industry. Delete every domain and the strategist still works, on whatever the company happens
to have noticed.

**It writes a document; it never commits anything.** Drafting and submitting are its own
(cheap, reversible); opening the business from what it wrote is a person's, always. That
asymmetry is the whole of §6, and it is why this agent can be trusted with a budget of a few
cents: the worst it can do is waste them writing something nobody approves.

**It may only use what the snapshot gave it.** A proposal's numbers come from the opportunity's
signals — what was actually observed — and a validator checks that it did not simply assert a
market size nobody measured.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autora.company.opportunities import OPEN_STATES
from autora.company.reporting import Reporting
from autora.company.snapshot import SnapshotBuilder
from autora.db.models import BusinessProposal, CommandOutcome, CommandRecord, Opportunity
from autora.runtime.behaviors import AgentBehavior, BehaviorRegistry, RunContext

ROLE = "strategist"
PROPOSE = "propose"

MIN_RISKS = 1
"""A proposal with no risks in it has not been thought about, it has been hoped about."""


# --- what it produces --------------------------------------------------------------------------


class ProposalDraft(BaseModel):
    """What the strategist decided to propose, and what it based it on."""

    opportunity_id: uuid.UUID
    proposal_id: uuid.UUID | None = None
    """The proposal it actually wrote, as the command returned it. None means it wrote none."""
    title: str = Field(min_length=1, max_length=200)
    business_model: str = Field(min_length=1, max_length=2000)
    evidence: list[str] = Field(default_factory=list)
    """The signals it read, in its own words. Empty is allowed and means it had none."""
    risks: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1, max_length=2000)
    submitted: bool = False
    """Whether it put the proposal up for decision, or left it as a draft on purpose."""


# --- what it is told ----------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a strategist at an autonomous company. You are given one
opportunity — something the company might do — and the evidence it has gathered about it. Your
job is to turn that into one business proposal a person can decide about.

You are given the company's snapshot. Everything you know is in it. If the evidence does not
support a number, do not write the number: "three competitors, all above $20/month" is a
finding; "a $2bn market" is not, unless a signal says so.

Write one proposal, through these commands:
- DraftProposal {opportunity_id, title, business_model, target_market, target_customer,
  proposed_product, expected_revenue_model, expected_margin, estimated_startup_cost,
  estimated_monthly_cost, risks, validation_plan, kill_criteria} — kill_criteria is required:
  say what would make this business not worth running, as a number somebody can check later
- SubmitProposal {proposal_id} — put your draft up for decision

Your proposal is a document, not a commitment. Nobody has to approve you for writing it, and
you cannot open a business: a person decides that, and they decide on exactly what you wrote.
So write what you would want to be held to.

Be concrete and be short. One proposal, its risks, and the number that would end it."""


# --- what it must not get wrong -------------------------------------------------------------


async def the_opportunity_is_open(
    session: AsyncSession, ctx: RunContext, output: BaseModel
) -> list[str]:
    if not isinstance(output, ProposalDraft):
        return []
    opportunity = await session.get(Opportunity, output.opportunity_id)
    if opportunity is None or opportunity.company_id != ctx.company_id:
        return [f"opportunity {output.opportunity_id} is not one of this company's"]
    if opportunity.state not in OPEN_STATES:
        return [f"opportunity {opportunity.key} is {opportunity.state}; it was decided already"]
    return []


async def wrote_the_proposal_it_describes(
    session: AsyncSession, ctx: RunContext, output: BaseModel
) -> list[str]:
    """The proposal must exist, belong to that opportunity, and have been written by this run.

    Same property as the CEO's "say what you did", and it matters more here: a strategist that
    reports a proposal it never wrote would leave a person waiting to decide about a document
    that does not exist.
    """
    if not isinstance(output, ProposalDraft):
        return []
    if output.proposal_id is None:
        return ["the draft names no proposal; DraftProposal was never submitted in this run"]
    proposal = await session.get(BusinessProposal, output.proposal_id)
    if proposal is None or proposal.company_id != ctx.company_id:
        return [f"proposal {output.proposal_id} is not one of this company's"]
    if proposal.opportunity_id != output.opportunity_id:
        return ["the proposal belongs to another opportunity than the one it names"]
    if proposal.authored_by_run_id != ctx.run_id:
        return ["that proposal was written by another run; this one wrote none"]
    issues = []
    if output.submitted and proposal.state != "SUBMITTED":
        issues.append(
            f"it says it submitted the proposal but it is {proposal.state}; "
            "SubmitProposal was not submitted in this run"
        )
    return issues


async def a_proposal_says_what_would_end_it(
    session: AsyncSession, ctx: RunContext, output: BaseModel
) -> list[str]:
    """Kill criteria and risks: the two things a hopeful proposal leaves out."""
    if not isinstance(output, ProposalDraft) or output.proposal_id is None:
        return []
    proposal = await session.get(BusinessProposal, output.proposal_id)
    if proposal is None:
        return []
    issues = []
    if not proposal.kill_criteria:
        issues.append("the proposal has no kill criteria; nobody could tell when to stop")
    if len(output.risks) < MIN_RISKS:
        issues.append("a proposal with no risks in it has been hoped about, not thought about")
    return issues


async def submitted_a_draft_command(
    session: AsyncSession, ctx: RunContext, output: BaseModel
) -> list[str]:
    """Nothing is claimed that the pipeline did not actually do (refusals do not count)."""
    if not isinstance(output, ProposalDraft):
        return []
    records = (
        await session.scalars(
            select(CommandRecord).where(
                CommandRecord.run_id == ctx.run_id,
                CommandRecord.command == "DraftProposal",
                CommandRecord.outcome != CommandOutcome.REFUSED.value,
            )
        )
    ).all()
    return [] if records else ["no DraftProposal went through in this run"]


# --- what it is given -------------------------------------------------------------------------


def opportunity_context(snapshots: SnapshotBuilder):
    """The company, as the CEO sees it. The opportunity it is working on is in there, with its
    signals and what exploring it has cost so far — so the strategist and the CEO argue from
    the same document rather than from two views that can drift."""

    async def context(session: AsyncSession, ctx: RunContext) -> str | None:
        snapshot = await snapshots.build(session, ctx.company_id)
        return "The company right now:\n" + snapshot.model_dump_json(indent=2, exclude_none=True)

    return context


def behaviors(snapshots: SnapshotBuilder | None = None) -> tuple[AgentBehavior, ...]:
    builder = snapshots or SnapshotBuilder(Reporting())
    return (
        AgentBehavior(
            role=ROLE,
            task_name=PROPOSE,
            capability="reasoning",
            system_prompt=SYSTEM_PROMPT,
            output_model=ProposalDraft,
            tools=("submit_command",),
            validators=(
                the_opportunity_is_open,
                submitted_a_draft_command,
                wrote_the_proposal_it_describes,
                a_proposal_says_what_would_end_it,
            ),
            max_steps=6,
            repair_limit=2,
            max_output_tokens=4096,
            context=opportunity_context(builder),
            summarize=_summary,
        ),
    )


def register_behaviors(
    registry: BehaviorRegistry, snapshots: SnapshotBuilder | None = None
) -> None:
    for behavior in behaviors(snapshots):
        registry.register(behavior)


def _summary(draft: BaseModel) -> str:
    assert isinstance(draft, ProposalDraft)
    state = "submitted for decision" if draft.submitted else "left as a draft"
    return f"{draft.title} ({state}), {len(draft.risks)} risk(s) named"
