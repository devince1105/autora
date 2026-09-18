"""Company identity, goals and governance policies (logs/platform/02_COMPANY_MODEL.md)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import ForeignKey, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from autora.db.base import Base, IdMixin, TimestampMixin, check_in, check_regex


class CompanyType(StrEnum):
    NEWSROOM = "newsroom"
    SAAS = "saas"
    RESEARCH = "research"
    ECOMMERCE = "ecommerce"
    SOFTWARE_STUDIO = "software_studio"


class CompanyStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class GoalLevel(StrEnum):
    ANNUAL = "annual"
    QUARTER = "quarter"
    CYCLE = "cycle"


class GoalStatus(StrEnum):
    ACTIVE = "active"
    ACHIEVED = "achieved"
    MISSED = "missed"
    CANCELLED = "cancelled"


class Company(IdMixin, TimestampMixin, Base):
    __tablename__ = "companies"
    __table_args__ = (
        UniqueConstraint("slug"),
        check_regex("slug", "^[a-z0-9][a-z0-9-]{1,62}$"),
        check_in("type", CompanyType),
        check_in("status", CompanyStatus),
    )

    slug: Mapped[str]
    name: Mapped[str]
    type: Mapped[str]
    mission: Mapped[str | None]
    strategy_doc: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(server_default=CompanyStatus.ACTIVE.value)


class CompanyGoal(IdMixin, TimestampMixin, Base):
    __tablename__ = "company_goals"
    __table_args__ = (
        check_in("level", GoalLevel),
        check_in("status", GoalStatus),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    parent_goal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("company_goals.id"))
    level: Mapped[str]
    title: Mapped[str]
    """Human-readable goal, e.g. "Publish 3 high-quality bilingual articles"."""
    metric: Mapped[str]
    """Machine-readable KPI key the Reporting service measures, e.g. "published_articles"."""
    target: Mapped[Decimal]
    current: Mapped[Decimal | None]
    deadline: Mapped[datetime | None]
    status: Mapped[str] = mapped_column(server_default=GoalStatus.ACTIVE.value)


class CompanyPolicy(IdMixin, TimestampMixin, Base):
    """Structured governance rule, e.g. key="newsroom.primary_lang", value="zh-TW".

    Values are JSON, never natural language. Loosening a policy is a HUMAN action
    (logs/platform/07_PERMISSION_MODEL.md); that is enforced by the command layer.
    """

    __tablename__ = "company_policies"
    __table_args__ = (
        UniqueConstraint("company_id", "key"),
        check_regex("key", "^[a-z][a-z0-9_]*(\\.[a-z][a-z0-9_]*)*$"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"))
    key: Mapped[str]
    value: Mapped[Any] = mapped_column(JSONB)
    """Any JSON value: string, number, bool, list or object."""
    updated_by: Mapped[dict[str, Any] | None]
    """Actor JSON ({kind, id}) of the last writer."""
