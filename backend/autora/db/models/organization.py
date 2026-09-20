"""The company's businesses and its organisation (logs/ARCHITECTURE_V2.md, T-600).

Four tables sit between a company and its agents, and each answers exactly one question:

- ``business_units`` — **what business are we in?** A unit has a market, products, customers and
  a P&L of its own, and its own criteria for whether it should continue.
- ``departments`` — **who does what kind of work?** A department is a function and an agent's
  home. ``business_unit_id`` is nullable, and that one nullable column is what lets the same
  table hold both kinds of department: a shared company function (Finance, Engineering) and a
  function that belongs to one business (AI Media's newsroom).
- ``roles`` — **what is this agent's job?** A catalogue of positions. ``roles.key`` is the same
  string the runtime already dispatches on (``agents.role``, ``tasks.required_role``), which is
  why the runtime needs no changes: the organisation is built *above* the key, never inside it.
  (It cannot be inside it: model routes are ``"<role>.<capability>"`` and reject a second dot.)
- ``products`` — **what do we offer the market?** Revenue and customers attach here.

A team is not a fifth table: ``departments.parent_department_id`` makes depth a property of the
data rather than of the schema, so a company with three agents has one department and a company
with thirty grows sub-departments in the same table.
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from autora.db.base import Base, IdMixin, TimestampMixin, check_in, check_regex

KEY_PATTERN = "^[a-z][a-z0-9_]*$"
"""Stable identifiers, the same shape as ``agents.role``: lowercase, no dots (see the router)."""


class BusinessUnitState(StrEnum):
    PROPOSED = "PROPOSED"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    WOUND_DOWN = "WOUND_DOWN"


class ProductState(StrEnum):
    DRAFT = "DRAFT"
    LIVE = "LIVE"
    RETIRED = "RETIRED"


class BusinessUnit(IdMixin, TimestampMixin, Base):
    """One business the company runs (AI Media, AI Education, AI SaaS).

    Not a department: ask "what does it sell, to whom, for how much?" — if that has an answer it
    is a business unit; if the answer is "it does a kind of work for the company" it is a
    department. Finance serves every unit and sells nothing, so it is a department.
    """

    __tablename__ = "business_units"
    __table_args__ = (
        UniqueConstraint("company_id", "key"),
        check_regex("key", KEY_PATTERN),
        check_in("state", BusinessUnitState),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    key: Mapped[str]
    name: Mapped[str]
    mission: Mapped[str | None]
    state: Mapped[str] = mapped_column(server_default=BusinessUnitState.PROPOSED.value)
    kill_criteria: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    """Same shape as a project's: when is this business no longer worth running?"""


class Department(IdMixin, TimestampMixin, Base):
    """A function, and the home of the agents who perform it.

    ``business_unit_id``: NULL for a company-wide function, set for a function that belongs to
    one business. ``parent_department_id``: NULL at the top, set for what an org chart would
    call a team. A sub-department belongs to the same business unit as its parent — enforced in
    :mod:`autora.company.organization`, not by a constraint, because it spans rows.
    """

    __tablename__ = "departments"
    __table_args__ = (
        UniqueConstraint("company_id", "key"),
        check_regex("key", KEY_PATTERN),
        CheckConstraint("id <> parent_department_id", name="not_its_own_parent"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    business_unit_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("business_units.id"))
    parent_department_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("departments.id"))
    key: Mapped[str]
    name: Mapped[str]
    purpose: Mapped[str | None]
    office_zone_key: Mapped[str | None]
    """Which part of the 3D floor this department occupies. A visual setting, not a position:
    the seats inside it are still assigned by the floor plan (3d-office/04)."""


class Role(IdMixin, TimestampMixin, Base):
    """A position in the organisation — and the contract between it and the runtime.

    ``key`` is the string the runtime already runs on: a behavior is registered for it, a task
    asks for it in ``required_role``, a policy rule names it, a model route starts with it. This
    table does not change any of that; it says where that key sits in the company and what an
    agent hired into it should start with.
    """

    __tablename__ = "roles"
    __table_args__ = (
        UniqueConstraint("company_id", "key"),
        check_regex("key", KEY_PATTERN),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    department_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("departments.id"), index=True)
    key: Mapped[str]
    """== ``agents.role`` == ``tasks.required_role``. The organisation's one link to execution."""
    title: Mapped[str]
    reports_to_role_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("roles.id"))
    is_lead: Mapped[bool] = mapped_column(server_default="false")
    """Heads its department. The CEO leads Executive; the Editor-in-Chief leads the newsroom."""
    responsibilities: Mapped[str | None]
    defaults: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    """What an agent hired into this role starts with: {capabilities, tools, permissions,
    model_policy, budget}. Copied onto the agent at hiring, not read at run time — an agent's
    own settings stay its own once it exists."""


class Product(IdMixin, TimestampMixin, Base):
    """What the company offers the market. Revenue and customers attach here.

    A product is not a project: a project is temporary work with a budget and kill criteria
    ("build the newsletter"), a product is a lasting offering ("Daily English World").
    """

    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("company_id", "key"),
        check_regex("key", KEY_PATTERN),
        check_in("state", ProductState),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    business_unit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("business_units.id"), index=True)
    key: Mapped[str]
    name: Mapped[str]
    description: Mapped[str | None]
    state: Mapped[str] = mapped_column(server_default=ProductState.DRAFT.value)
    public_url: Mapped[str | None]
