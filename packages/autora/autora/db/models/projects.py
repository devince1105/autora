"""Projects: the unit of budget, ROI and kill criteria (logs/platform/02_COMPANY_MODEL.md §4)."""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from autora.db.base import Base, IdMixin, TimestampMixin, check_in


class ProjectState(StrEnum):
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    KILLED = "KILLED"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"


class Project(IdMixin, TimestampMixin, Base):
    __tablename__ = "projects"
    __table_args__ = (
        check_in("state", ProjectState),
        # Invariant: a project that was ever approved carries structured kill criteria.
        CheckConstraint(
            "state IN ('PROPOSED', 'REJECTED') OR kill_criteria IS NOT NULL",
            name="kill_criteria_required_once_approved",
        ),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    name: Mapped[str]
    description: Mapped[str | None]
    state: Mapped[str] = mapped_column(server_default=ProjectState.PROPOSED.value)
    kill_criteria: Mapped[dict[str, Any] | None]
    created_by_run_id: Mapped[uuid.UUID | None]
    """agent_runs.id of the proposing run; FK added with agent_runs (T-201)."""
    approved_by: Mapped[dict[str, Any] | None]
    """Actor JSON of the approver."""
    override_reason: Mapped[str | None]
    """Why a human rejected a kill proposal (fed back into the next snapshot)."""
