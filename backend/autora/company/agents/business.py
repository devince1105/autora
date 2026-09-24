"""The business agent: looks at the market and at the company's own numbers, and writes down
what might be a business (T-706).

The company's business loop starts with an opportunity — and until now nothing wrote one. The
CEO evaluates open opportunities, the strategist turns one into a proposal, a person decides
whether to open the business; every step existed, and the first only ever happened by hand.
This agent is that first step (ARCHITECTURE_V2_1 §4: "市場觀察 / 機會發現").

**It notices; it decides nothing.** What it may do is exactly two things, both cheap and
reversible (§6): record a new opportunity (``DiscoverOpportunity``) and add an observation to
one the company is still looking at (``RecordOpportunitySignal``). Scoring, evaluating,
proposing, funding and opening are the CEO's, the strategist's and a person's; the policy gives
this role none of them. The worst it can do is add a line the CEO rejects — and the rejection is
kept, which is the point of keeping them.

**Everything it writes down is something it can show.** Checked when it is written, not after:

- a figure from the company (``source: company``) must be the one the last report stored;
- a page from outside (``source: web``) must be one its own tools captured in this run, cited
  by the id they returned. A search result it never opened is not an observation.

Which outside tools it has is the composition root's to say (``research_tools``): Core does not
know which domain provides a web search. With none, it works from the company's own numbers.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autora.company.opportunities import OPEN_STATES
from autora.company.reporting import Reporting
from autora.company.snapshot import SnapshotBuilder
from autora.company.verbs_business import MAX_DISCOVERED_PER_RUN
from autora.db.models import CommandOutcome, CommandRecord, Opportunity
from autora.runtime.behaviors import AgentBehavior, BehaviorRegistry, RunContext

ROLE = "business"
WATCH_MARKET = "watch_market"

WRITES = ("DiscoverOpportunity", "RecordOpportunitySignal")

MEMORY = 30
"""Decided opportunities shown to it, newest first: what the company already said no (or yes)
to, so it does not pay to find the same thing twice."""


# --- what it produces --------------------------------------------------------------------------


class Finding(BaseModel):
    """One opportunity it wrote something about, as the command came back."""

    opportunity_id: uuid.UUID
    new: bool
    """True when this run discovered it; False when it added to one already open."""
    signal_ids: list[uuid.UUID] = Field(min_length=1, max_length=10)
    why_it_matters: str = Field(min_length=1, max_length=1000)


class MarketWatch(BaseModel):
    """What it looked at, and what — if anything — it wrote down."""

    summary: str = Field(min_length=1, max_length=1500)
    findings: list[Finding] = Field(default_factory=list, max_length=8)
    nothing_new_because: str | None = Field(default=None, max_length=1000)
    """Required when it wrote nothing: "looked, found nothing worth a line" is a finding."""


# --- what it is told ----------------------------------------------------------------------------

_BASE = f"""You are the business developer of an autonomous company. Once a week you look for
what the company could do next: a new business, or evidence about one it is already considering.

You are given the company's snapshot — its businesses, their numbers, the opportunities it is
looking at — and the ones it has already decided about, with the reasons. Do not rediscover an
idea the company rejected unless the reason no longer holds; if so, say what changed.

You can only write things down. You cannot score, evaluate, propose, fund or open anything:
the CEO and a person do that, from what you wrote.
- DiscoverOpportunity {{key, title, thesis, market, signals: [signal, ...]}} — something that
  might be a business. key is lower_snake_case and new; thesis says in a paragraph why this could
  be a business; at least one signal. At most {MAX_DISCOVERED_PER_RUN} in one look.
- RecordOpportunitySignal {{opportunity_id, signal}} — one more observation about an opportunity
  that is still open.

A signal is {{source, summary, metric?, value?, business_unit_id?, evidence_ref?}}, and it is
checked when you send it:
- source "company": a number from the snapshot — metric is its key, value exactly as the report
  has it, business_unit_id when it is one business's number. A figure that is not in the report
  is refused.
"""

_WEB = """- source "web": a page you captured in this run — evidence_ref is the id your capture tool
  returned. A search result you did not capture is not evidence and is refused.

To look outside the company: {tools}. Search for demand, prices, competitors and gaps; capture
the pages you will cite; do not cite what you did not capture.
"""

_NO_WEB = """You have no tools to look outside the company this time: work from its own numbers.
"""

_END = """
Report every opportunity you wrote about, with the signal ids the commands returned — no more,
no fewer. If you wrote nothing, say why in nothing_new_because. Be concrete: the number, the
page, the gap."""


def system_prompt(research_tools: tuple[str, ...]) -> str:
    if research_tools:
        return _BASE + _WEB.format(tools=", ".join(research_tools)) + _END
    return _BASE + _NO_WEB + _END


# --- what it must not get wrong -------------------------------------------------------------


async def _written(session: AsyncSession, ctx: RunContext) -> dict[uuid.UUID, tuple[bool, set]]:
    """What this run's commands wrote: opportunity -> (discovered here, its signal ids)."""
    records = (
        await session.scalars(
            select(CommandRecord).where(
                CommandRecord.run_id == ctx.run_id,
                CommandRecord.command.in_(WRITES),
                CommandRecord.outcome == CommandOutcome.DONE.value,
            )
        )
    ).all()
    written: dict[uuid.UUID, tuple[bool, set]] = {}
    for record in records:
        result = record.result or {}
        opportunity_id = uuid.UUID(result["opportunity_id"])
        new, signals = written.get(opportunity_id, (False, set()))
        written[opportunity_id] = (
            new or record.command == "DiscoverOpportunity",
            signals | {uuid.UUID(s) for s in result.get("signal_ids", [])},
        )
    return written


async def reported_what_it_wrote(
    session: AsyncSession, ctx: RunContext, output: BaseModel
) -> list[str]:
    """The report is the commands: every opportunity written is reported, as written, and
    nothing is reported that was not."""
    if not isinstance(output, MarketWatch):
        return []
    written = await _written(session, ctx)
    reported = {finding.opportunity_id: finding for finding in output.findings}
    issues = [
        f"it wrote about {opportunity_id} and does not report it"
        for opportunity_id in written
        if opportunity_id not in reported
    ]
    for opportunity_id, finding in reported.items():
        if opportunity_id not in written:
            issues.append(f"it reports {opportunity_id}, which this run wrote nothing about")
            continue
        new, signals = written[opportunity_id]
        if finding.new != new:
            issues.append(
                f"{opportunity_id} was {'discovered' if new else 'already open'}, "
                f"not {'discovered' if finding.new else 'already open'}"
            )
        if set(finding.signal_ids) != signals:
            issues.append(f"the signals reported for {opportunity_id} are not the ones written")
    return issues


async def silence_has_a_reason(
    session: AsyncSession, ctx: RunContext, output: BaseModel
) -> list[str]:
    if not isinstance(output, MarketWatch):
        return []
    if not output.findings and not (output.nothing_new_because or "").strip():
        return ["it wrote nothing and does not say why; a finding needs a reason"]
    return []


# --- what it is given -------------------------------------------------------------------------


def market_context(snapshots: SnapshotBuilder):
    """The CEO's snapshot — the open opportunities are in it — and the company's memory of the
    ones it already decided, which the snapshot leaves out and this agent cannot do without."""

    async def context(session: AsyncSession, ctx: RunContext) -> str | None:
        snapshot = await snapshots.build(session, ctx.company_id)
        decided = (
            await session.scalars(
                select(Opportunity)
                .where(
                    Opportunity.company_id == ctx.company_id,
                    Opportunity.state.not_in(OPEN_STATES),
                )
                .order_by(Opportunity.decided_at.desc())
                .limit(MEMORY)
            )
        ).all()
        lines = [
            f"- {o.key} ({o.state}): {o.title}"
            + (f" — {o.decision_reason}" if o.decision_reason else "")
            for o in decided
        ]
        return (
            "The company right now:\n"
            + snapshot.model_dump_json(indent=2, exclude_none=True)
            + "\n\nAlready decided:\n"
            + ("\n".join(lines) if lines else "(nothing yet)")
        )

    return context


def behaviors(
    snapshots: SnapshotBuilder | None = None, research_tools: tuple[str, ...] = ()
) -> tuple[AgentBehavior, ...]:
    builder = snapshots or SnapshotBuilder(Reporting())
    return (
        AgentBehavior(
            role=ROLE,
            task_name=WATCH_MARKET,
            capability="reasoning",
            system_prompt=system_prompt(research_tools),
            output_model=MarketWatch,
            # submit_command for the two writes the policy allows it; the rest only reads
            tools=("submit_command", *research_tools),
            validators=(reported_what_it_wrote, silence_has_a_reason),
            max_steps=16 if research_tools else 6,
            repair_limit=2,
            max_output_tokens=4096,
            context=market_context(builder),
            summarize=_summary,
        ),
    )


def register_behaviors(
    registry: BehaviorRegistry,
    snapshots: SnapshotBuilder | None = None,
    research_tools: tuple[str, ...] = (),
) -> None:
    for behavior in behaviors(snapshots, research_tools):
        registry.register(behavior)


def _summary(watch: BaseModel) -> str:
    assert isinstance(watch, MarketWatch)
    if not watch.findings:
        return "nothing new written down"
    new = sum(1 for finding in watch.findings if finding.new)
    return f"{new} new opportunit{'y' if new == 1 else 'ies'}, {len(watch.findings) - new} updated"
