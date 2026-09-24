"""The finance agent: reads what the company earned and spent, and proposes budgets (T-705).

It exists because the numbers that decide a budget — revenue, cost, members, who is about to
lapse — are written down every cycle and nobody was reading them with money in mind. The CEO
plans the day; this reads the books after the day is measured, and says where the envelopes no
longer fit what is happening.

**It never moves money.** That is the rule it was designed around (``platform/01`` §6: "Finance
Agent 管理收支 conflicts with LLM 不可改財務資料"; ``platform/16`` §3 rejects "a finance agent that
can write the finance tables" outright), and it holds in three places, not one:

- its only tool is ``submit_command``, and the only command it has a use for is
  ``AllocateBudget`` — which the policy sends to a person every time it is the finance role
  asking (``company/policy.py``: ``needs_approval("allocate_budget", "finance")``);
- everything else it might try is denied: the policy lists nothing else for this role, and
  anything not listed is refused — ``record_transaction`` included;
- and even an approved proposal writes a *budget*, an envelope for future spending, never a
  transaction. The ledger is written by the ledger alone.

So a proposal from this agent is a question put to a person, with the numbers it rests on
attached. The worst it can do is waste a few cents asking.

**Every number it cites is checked against the report it came from.** A proposal that says
"revenue fell to 0" is only useful if revenue fell to 0; a validator looks the metric up in the
KPI snapshot the cycle stored and refuses a figure that is not there.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autora.company import reporting as company_reporting
from autora.company.reporting import Reporting
from autora.company.snapshot import SnapshotBuilder
from autora.db.models import (
    Budget,
    BusinessUnit,
    CommandOutcome,
    CommandRecord,
    KpiScope,
    Project,
)
from autora.runtime.behaviors import AgentBehavior, BehaviorRegistry, RunContext

ROLE = "finance"
REVIEW_BUDGETS = "review_budgets"

MAX_PROPOSALS = 3
"""A review that proposes more than a few changes at once is a reorganisation, not a review."""

PROPOSE_COMMAND = "AllocateBudget"


# --- what it produces --------------------------------------------------------------------------


class Evidence(BaseModel):
    """One number a proposal rests on, exactly as the cycle's report stored it."""

    metric: str = Field(pattern=r"^[a-z][a-z0-9_.]*$", max_length=80)
    """The KPI's key, as in the snapshot: ``revenue``, ``cost``, ``members``, ``newsroom.views``."""
    scope: Literal["company", "business_unit", "project"] = "company"
    scope_id: uuid.UUID | None = None
    """The business or project the number is about; none for the company."""
    value: str = Field(max_length=40)
    """As the report has it. Checked: a number the report does not hold is refused."""


class ProposedBudget(BaseModel):
    """A budget the finance agent asked a person to set, and why."""

    approval_id: uuid.UUID | None = None
    """The approval the command opened, as it came back. None means nothing was asked."""
    business_unit_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    amount: Decimal = Field(ge=0)
    period: Literal["cycle", "day", "month"] = "cycle"
    reason: str = Field(min_length=20, max_length=1000)
    evidence: list[Evidence] = Field(min_length=1, max_length=6)


class BudgetReview(BaseModel):
    """What the books say, and what — if anything — should change."""

    summary: str = Field(min_length=1, max_length=1500)
    proposals: list[ProposedBudget] = Field(default_factory=list, max_length=MAX_PROPOSALS)
    unchanged_because: str | None = Field(default=None, max_length=1000)
    """Required when it proposes nothing: "the numbers are fine" is a finding, silence is not."""


# --- what it is told ----------------------------------------------------------------------------

SYSTEM_PROMPT = f"""You are the finance officer of an autonomous company. The cycle has just
been measured. You are given the company's snapshot — its capital, its businesses and projects
with the numbers the report stored for them — and the budgets that are set today.

Your job: read the numbers, and say whether any budget no longer fits what is happening. A
business earning nothing on a large envelope, a project about to run out mid-cycle, costs rising
faster than revenue — those are findings. "Budgets should be optimised" is not.

You cannot move money. You can only ask:
- AllocateBudget {{amount, period, business_unit_id | project_id | neither for the company,
  hard_cap}} — every one you send goes to a person, who decides. It changes an envelope for
  future spending; it spends nothing and records nothing.

Rules:
- At most {MAX_PROPOSALS} proposals. Propose nothing if nothing needs to change, and say why in
  unchanged_because.
- Every proposal cites the numbers it rests on (evidence): the metric's key, the scope it is
  about, and the value exactly as the snapshot gives it. A number that is not in the snapshot is
  not evidence — and it will be checked.
- Report each proposal with the approval_id AllocateBudget returned. Do not report a proposal you
  did not send, and do not send one you do not report.

Be short and concrete: the number, the envelope, the change."""


# --- what it must not get wrong -------------------------------------------------------------


async def _proposals_sent(session: AsyncSession, ctx: RunContext) -> list[CommandRecord]:
    return list(
        (
            await session.scalars(
                select(CommandRecord).where(
                    CommandRecord.run_id == ctx.run_id,
                    CommandRecord.command == PROPOSE_COMMAND,
                    CommandRecord.outcome != CommandOutcome.REFUSED.value,
                )
            )
        ).all()
    )


async def every_proposal_waits_for_a_person(
    session: AsyncSession, ctx: RunContext, output: BaseModel
) -> list[str]:
    """Each proposal it reports is an approval that is open, from a command this run sent.

    And nothing it sent was carried out without one: if the policy ever let a finance
    allocation through on its own, this is where it would show.
    """
    if not isinstance(output, BudgetReview):
        return []
    sent = await _proposals_sent(session, ctx)
    issues = [
        f"{record.command} went through without a person ({record.outcome}); "
        "a finance proposal must wait for approval"
        for record in sent
        if record.outcome != CommandOutcome.AWAITING_APPROVAL.value
    ]
    waiting = {record.approval_id for record in sent if record.approval_id is not None}
    for proposal in output.proposals:
        if proposal.approval_id is None:
            issues.append("a proposal names no approval; AllocateBudget was not sent for it")
        elif proposal.approval_id not in waiting:
            issues.append(f"approval {proposal.approval_id} was not opened by this run")
    return issues


async def said_what_it_proposed(
    session: AsyncSession, ctx: RunContext, output: BaseModel
) -> list[str]:
    """No proposal sent and left out of the report, and no silence without a reason."""
    if not isinstance(output, BudgetReview):
        return []
    sent = await _proposals_sent(session, ctx)
    issues = []
    if len(sent) != len(output.proposals):
        issues.append(
            f"it sent {len(sent)} AllocateBudget command(s) and reports {len(output.proposals)}"
        )
    if not output.proposals and not (output.unchanged_because or "").strip():
        issues.append("it proposes nothing and does not say why; a finding needs a reason")
    return issues


async def the_numbers_are_the_reports(
    session: AsyncSession, ctx: RunContext, output: BaseModel
) -> list[str]:
    """Every figure it cites is the one the cycle's report stored, for the scope it names."""
    if not isinstance(output, BudgetReview):
        return []
    issues = []
    for proposal in output.proposals:
        for cited in proposal.evidence:
            issue = await company_reporting.figure_issue(
                session,
                ctx.company_id,
                metric=cited.metric,
                value=cited.value,
                scope=KpiScope(cited.scope),
                scope_id=cited.scope_id,
            )
            if issue:
                issues.append(issue)
    return issues


async def proposes_for_what_exists(
    session: AsyncSession, ctx: RunContext, output: BaseModel
) -> list[str]:
    """A budget for a business or project of this company, and never for both at once."""
    if not isinstance(output, BudgetReview):
        return []
    issues = []
    for proposal in output.proposals:
        if proposal.business_unit_id and proposal.project_id:
            issues.append("a budget belongs to a business or a project, not both")
        if proposal.business_unit_id:
            unit = await session.get(BusinessUnit, proposal.business_unit_id)
            if unit is None or unit.company_id != ctx.company_id:
                issues.append(f"no business {proposal.business_unit_id} in this company")
        if proposal.project_id:
            project = await session.get(Project, proposal.project_id)
            if project is None or project.company_id != ctx.company_id:
                issues.append(f"no project {proposal.project_id} in this company")
    return issues


# --- what it is given -------------------------------------------------------------------------


def books_context(snapshots: SnapshotBuilder):
    """The CEO's snapshot — the same document, so the two argue from the same numbers — and the
    envelopes set today, which the snapshot does not list and a budget review cannot do without."""

    async def context(session: AsyncSession, ctx: RunContext) -> str | None:
        snapshot = await snapshots.build(session, ctx.company_id)
        budgets = (
            await session.scalars(select(Budget).where(Budget.company_id == ctx.company_id))
        ).all()
        lines = [
            f"- {_scope_of(b)} per {b.period}: {b.amount} {b.currency}"
            + (" (hard cap)" if b.hard_cap else "")
            for b in budgets
        ]
        return (
            "The company right now:\n"
            + snapshot.model_dump_json(indent=2, exclude_none=True)
            + "\n\nBudgets set today:\n"
            + ("\n".join(lines) if lines else "(none)")
        )

    return context


def _scope_of(budget: Budget) -> str:
    if budget.project_id:
        return f"project {budget.project_id}"
    if budget.business_unit_id:
        return f"business {budget.business_unit_id}"
    return "company"


def behaviors(snapshots: SnapshotBuilder | None = None) -> tuple[AgentBehavior, ...]:
    builder = snapshots or SnapshotBuilder(Reporting())
    return (
        AgentBehavior(
            role=ROLE,
            task_name=REVIEW_BUDGETS,
            capability="reasoning",
            system_prompt=SYSTEM_PROMPT,
            output_model=BudgetReview,
            # the one tool, and through it only questions: see the module's docstring
            tools=("submit_command",),
            validators=(
                every_proposal_waits_for_a_person,
                said_what_it_proposed,
                proposes_for_what_exists,
                the_numbers_are_the_reports,
            ),
            max_steps=8,
            repair_limit=2,
            max_output_tokens=4096,
            context=books_context(builder),
            summarize=_summary,
        ),
    )


def register_behaviors(
    registry: BehaviorRegistry, snapshots: SnapshotBuilder | None = None
) -> None:
    for behavior in behaviors(snapshots):
        registry.register(behavior)


def _summary(review: BaseModel) -> str:
    assert isinstance(review, BudgetReview)
    if not review.proposals:
        return "no budget changes proposed"
    return f"{len(review.proposals)} budget change(s) sent for approval"
