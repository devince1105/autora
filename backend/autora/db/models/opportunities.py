"""How the company decides which business to be in (logs/ARCHITECTURE_V2_1.md §1–§2, T-611).

Three tables between "somebody noticed something" and "the company opened a business":

- ``opportunities`` — **something that might be a business.** It exists before any money is
  spent on it, which is why it cannot be a state of ``business_units``: that table answers
  "what businesses do we run", and a row for every idea anyone ever had would make that
  question need a state filter forever. A rejected opportunity is kept on purpose — "we looked
  at AI customer support in 2026-09 and decided the acquisition cost did not work" is the kind
  of thing a company pays for twice if it forgets it.
- ``opportunity_signals`` — **what was observed.** Evidence accumulates on an opportunity over
  many cycles. The shell is the company's (source, summary, a number when there is one, a
  snapshot reference); the *contents* are written by whatever domain observed them, and nothing
  here interprets them.
- ``business_proposals`` — **what we would actually do about it.** One opportunity can have
  several: a subscription app, a B2B licence, paid content. The CEO compares proposals, not
  opportunities. A submitted proposal is frozen, because an approval has to point at the exact
  thing that was approved — and because opening a business reads its shape from here rather
  than from a scattering of parameters.

None of this creates agent work. Exploration and validation are ordinary **projects**
(``projects.opportunity_id``), so budgets, cost attribution and kill criteria are the ones the
company already has, and this layer needs no scheduler of its own.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Numeric, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from autora.db.base import Base, CreatedAtMixin, IdMixin, TimestampMixin, check_in, check_regex

KEY_PATTERN = "^[a-z][a-z0-9_]*$"


class OpportunityState(StrEnum):
    DISCOVERED = "DISCOVERED"
    EVALUATING = "EVALUATING"
    VALIDATING = "VALIDATING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class ProposalState(StrEnum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"


class Opportunity(IdMixin, TimestampMixin, Base):
    """Something that might be a business, and the company's memory of deciding about it."""

    __tablename__ = "opportunities"
    __table_args__ = (
        UniqueConstraint("company_id", "key"),
        check_regex("key", KEY_PATTERN),
        check_in("state", OpportunityState),
        CheckConstraint(
            "state NOT IN ('APPROVED', 'REJECTED', 'EXPIRED') OR decided_at IS NOT NULL",
            name="decided_opportunity_has_a_date",
        ),
        CheckConstraint(
            "state <> 'APPROVED' OR business_unit_id IS NOT NULL",
            name="approved_opportunity_became_a_business",
        ),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    key: Mapped[str]
    title: Mapped[str]
    thesis: Mapped[str | None]
    """Why this might be a business, in a paragraph."""
    market: Mapped[str | None]
    state: Mapped[str] = mapped_column(server_default=OpportunityState.DISCOVERED.value)
    score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    """The last evaluation's score. For comparing, never the decision itself."""
    discovered_by_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", use_alter=True)
    )
    decided_at: Mapped[datetime | None]
    decided_by: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    """Actor JSON of whoever decided, the same shape as a project's approver."""
    decision_reason: Mapped[str | None]
    expires_at: Mapped[datetime | None]
    """Opportunities go stale: a market moves and an old conclusion should stop counting."""
    business_unit_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("business_units.id"))
    """What it became, once approved."""


class OpportunitySignal(IdMixin, CreatedAtMixin, Base):
    """One observation attached to an opportunity. The company stores it; a domain means it."""

    __tablename__ = "opportunity_signals"

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("opportunities.id", ondelete="CASCADE"), index=True
    )
    source: Mapped[str]
    """Where it came from: a search, an existing business's numbers, a person."""
    summary: Mapped[str]
    metric: Mapped[str | None]
    value: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    observed_at: Mapped[datetime]
    evidence_ref: Mapped[str | None]
    """Blob or document reference for the snapshot behind this, as the newsroom does it."""
    recorded_by_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", use_alter=True)
    )


class BusinessProposal(IdMixin, TimestampMixin, Base):
    """What the company would do about an opportunity — a decision artifact, not a plan of work.

    Frozen on submission: a submitted proposal is never edited, it is superseded by a new
    version. That is what makes "we approved $2000 for *this*" a question with an answer.
    """

    __tablename__ = "business_proposals"
    __table_args__ = (
        UniqueConstraint("opportunity_id", "version"),
        check_in("state", ProposalState),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "state NOT IN ('APPROVED', 'REJECTED') OR decided_at IS NOT NULL",
            name="decided_proposal_has_a_date",
        ),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    opportunity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("opportunities.id"), index=True)
    version: Mapped[int] = mapped_column(server_default="1")
    state: Mapped[str] = mapped_column(server_default=ProposalState.DRAFT.value)
    title: Mapped[str]
    business_model: Mapped[str | None]
    target_market: Mapped[str | None]
    target_customer: Mapped[str | None]
    proposed_product: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    """What it would sell: {"key": ..., "name": ..., "kind": ...}. Read when the business opens."""
    expected_revenue_model: Mapped[str | None]
    expected_margin: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    estimated_startup_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    estimated_monthly_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    required_agents: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    required_capabilities: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    risks: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    validation_plan: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    kill_criteria: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    """The business's own criteria, fixed before anyone knows whether they will be met."""
    authored_by_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", use_alter=True)
    )
    approval_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("approvals.id"))
    """The person's decision this proposal was approved by."""
    decided_at: Mapped[datetime | None]
    decision_reason: Mapped[str | None]
    validation_project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"))
    business_unit_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("business_units.id"))
    """The business this proposal opened, once approved and acted on."""
