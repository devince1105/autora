"""Building and reading the organisation (logs/ARCHITECTURE_V2.md, T-600).

The tables are in :mod:`autora.db.models.organization`; this module owns the rules that span
rows and so cannot be a database constraint:

- a sub-department belongs to the same business unit as its parent (a team cannot work for a
  different business than the department it is part of);
- a department tree has no cycles;
- a role's key is unique in the company, because it is the key the runtime dispatches on;
- an agent's ``role``, ``role_id`` and ``department_id`` agree — the string, the position and
  the home say the same thing.

The last one is why hiring goes through :func:`assign`: nothing stops a caller from writing a
role string and a department that contradict each other, so one function is the place that
refuses to.

Reading the organisation is :func:`org_chart`, which returns the tree the 3D office and the API
both render. It is one query per table, not a recursive CTE: an org chart is tens of rows, and
assembling it in Python keeps the shape obvious.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autora.company import events as company_events
from autora.db.models import (
    Agent,
    AgentStatus,
    BusinessUnit,
    BusinessUnitState,
    Department,
    Product,
    ProductState,
    Role,
)
from autora.runtime.actor import Actor
from autora.runtime.events.outbox import emit
from autora.runtime.events.schema import new_event


class OrganizationError(Exception):
    pass


class DuplicateKey(OrganizationError):
    def __init__(self, what: str, key: str):
        super().__init__(f"this company already has a {what} called {key!r}")


class UnknownUnit(OrganizationError):
    pass


class Contradiction(OrganizationError):
    """The organisation would disagree with itself (the invariants above)."""


# --- building -------------------------------------------------------------------------------


async def add_business_unit(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    key: str,
    name: str,
    actor: Actor,
    mission: str | None = None,
    state: BusinessUnitState = BusinessUnitState.PROPOSED,
    kill_criteria: dict[str, Any] | None = None,
) -> BusinessUnit:
    """Open a line of business. Does not commit."""
    if await _by_key(session, BusinessUnit, company_id, key) is not None:
        raise DuplicateKey("business unit", key)
    unit = BusinessUnit(
        company_id=company_id,
        key=key,
        name=name,
        mission=mission,
        state=state.value,
        kill_criteria=kill_criteria,
    )
    session.add(unit)
    await session.flush()
    await emit(
        session,
        new_event(
            company_events.BusinessUnitCreated(key=key, name=name, state=state.value),
            company_id=company_id,
            actor=actor,
            aggregate_type="business_unit",
            aggregate_id=unit.id,
        ),
    )
    return unit


async def add_department(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    key: str,
    name: str,
    actor: Actor,
    business_unit_id: uuid.UUID | None = None,
    parent_department_id: uuid.UUID | None = None,
    purpose: str | None = None,
    office_zone_key: str | None = None,
) -> Department:
    """Add a function, or a team inside one. Does not commit.

    With a parent, the business unit is inherited and may not be contradicted: a team inside
    the newsroom works for AI Media, whatever the caller passes.
    """
    if await _by_key(session, Department, company_id, key) is not None:
        raise DuplicateKey("department", key)
    if parent_department_id is not None:
        parent = await session.get(Department, parent_department_id)
        if parent is None or parent.company_id != company_id:
            raise UnknownUnit(f"no department {parent_department_id} in this company")
        if business_unit_id is not None and business_unit_id != parent.business_unit_id:
            raise Contradiction(
                f"{key!r} is inside {parent.key!r}, which belongs to business unit "
                f"{parent.business_unit_id}, so it cannot belong to {business_unit_id}"
            )
        business_unit_id = parent.business_unit_id
    if business_unit_id is not None:
        await _unit(session, company_id, business_unit_id)
    department = Department(
        company_id=company_id,
        business_unit_id=business_unit_id,
        parent_department_id=parent_department_id,
        key=key,
        name=name,
        purpose=purpose,
        office_zone_key=office_zone_key or key,
    )
    session.add(department)
    await session.flush()
    await emit(
        session,
        new_event(
            company_events.DepartmentCreated(
                key=key,
                name=name,
                business_unit_id=business_unit_id,
                parent_department_id=parent_department_id,
            ),
            company_id=company_id,
            actor=actor,
            aggregate_type="department",
            aggregate_id=department.id,
        ),
    )
    return department


async def add_role(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    key: str,
    title: str,
    department_id: uuid.UUID,
    actor: Actor,
    reports_to_role_id: uuid.UUID | None = None,
    is_lead: bool = False,
    responsibilities: str | None = None,
    defaults: Mapping[str, Any] | None = None,
) -> Role:
    """Define a position. Does not commit.

    ``key`` is the runtime's key. This function does not check that a behavior is registered
    for it — a company may define a position before anything can perform it (the API refuses to
    *hire* into a role the runtime cannot run, which is the point where it matters).
    """
    if await _by_key(session, Role, company_id, key) is not None:
        raise DuplicateKey("role", key)
    department = await session.get(Department, department_id)
    if department is None or department.company_id != company_id:
        raise UnknownUnit(f"no department {department_id} in this company")
    role = Role(
        company_id=company_id,
        department_id=department_id,
        key=key,
        title=title,
        reports_to_role_id=reports_to_role_id,
        is_lead=is_lead,
        responsibilities=responsibilities,
        defaults=dict(defaults or {}),
    )
    session.add(role)
    await session.flush()
    return role


async def add_product(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    key: str,
    name: str,
    business_unit_id: uuid.UUID,
    actor: Actor,
    description: str | None = None,
    state: ProductState = ProductState.DRAFT,
    public_url: str | None = None,
) -> Product:
    """Register something the company offers the market. Does not commit."""
    if await _by_key(session, Product, company_id, key) is not None:
        raise DuplicateKey("product", key)
    await _unit(session, company_id, business_unit_id)
    product = Product(
        company_id=company_id,
        business_unit_id=business_unit_id,
        key=key,
        name=name,
        description=description,
        state=state.value,
        public_url=public_url,
    )
    session.add(product)
    await session.flush()
    await emit(
        session,
        new_event(
            company_events.ProductCreated(
                key=key, name=name, business_unit_id=business_unit_id, state=state.value
            ),
            company_id=company_id,
            actor=actor,
            aggregate_type="product",
            aggregate_id=product.id,
        ),
    )
    return product


EXECUTIVE = "executive"
CEO_ROLE = "ceo"


async def bootstrap_executive(
    session: AsyncSession, company_id: uuid.UUID, *, actor: Actor
) -> tuple[Department, Role]:
    """Give a company the one department every company has, and the position that leads it.

    A company runs businesses through departments, so it needs at least one that belongs to no
    business: the one that decides which businesses to run. The position is defined here; the
    agent that holds it arrives with the CEO agent (T-605), and a company with an empty chair
    is a real state — it simply has nobody deciding yet.
    """
    department = await department_by_key(session, company_id, EXECUTIVE)
    if department is None:
        department = await add_department(
            session,
            company_id=company_id,
            key=EXECUTIVE,
            name="Executive",
            actor=actor,
            purpose="Capital, portfolio, priorities and risk.",
            office_zone_key="ceo",
        )
    role = await role_by_key(session, company_id, CEO_ROLE)
    if role is None:
        role = await add_role(
            session,
            company_id=company_id,
            key=CEO_ROLE,
            title="CEO",
            department_id=department.id,
            actor=actor,
            is_lead=True,
            responsibilities=(
                "Decides which businesses the company is in and what they are worth spending "
                "on. Does not do the businesses' work."
            ),
        )
    return department, role


# --- putting agents in it -------------------------------------------------------------------


async def assign(session: AsyncSession, agent: Agent, role: Role, *, actor: Actor) -> Agent:
    """Give an agent its position, and the home that comes with it. Does not commit.

    The three ways an agent says what it does — the ``role`` string the runtime dispatches on,
    the position it holds and the department it works in — are set here together, so they
    cannot drift apart. Moving an agent between departments is moving it to another role.

    Emits ``AGENT_ASSIGNED``. That event is not decoration: the realtime reducer rebuilds an
    agent only from ``AGENT_CREATED``, so without it a running office would keep drawing the
    agent at its old desk until the next full snapshot.
    """
    if role.company_id != agent.company_id:
        raise Contradiction("an agent cannot hold a role of another company")
    was_department, was_role = agent.department_id, agent.role
    agent.role = role.key
    agent.role_id = role.id
    agent.department_id = role.department_id
    await session.flush()
    if (was_department, was_role) != (agent.department_id, agent.role):
        department = await session.get(Department, role.department_id)
        await emit(
            session,
            new_event(
                company_events.AgentAssigned(
                    role=role.key,
                    role_id=role.id,
                    department_id=role.department_id,
                    department_key=department.key if department else None,
                    previous_role=was_role,
                    previous_department_id=was_department,
                ),
                company_id=agent.company_id,
                actor=actor,
                aggregate_type="agent",
                aggregate_id=agent.id,
                agent_id=agent.id,
            ),
        )
    return agent


# --- reading --------------------------------------------------------------------------------


@dataclass(frozen=True)
class DepartmentNode:
    """A department with what is inside it: its teams, its positions, and who holds them."""

    department: Department
    roles: tuple[Role, ...] = ()
    agents: tuple[Agent, ...] = ()
    teams: tuple[DepartmentNode, ...] = ()

    @property
    def headcount(self) -> int:
        return len(self.agents) + sum(team.headcount for team in self.teams)


@dataclass(frozen=True)
class UnitNode:
    """A business unit with the departments that serve only it, and what it sells."""

    unit: BusinessUnit | None
    """None for the company itself: the shared functions that serve every business."""
    departments: tuple[DepartmentNode, ...] = ()
    products: tuple[Product, ...] = ()


@dataclass(frozen=True)
class OrgChart:
    company_id: uuid.UUID
    shared: UnitNode
    """Departments with no business unit: Executive, Finance, Engineering."""
    units: tuple[UnitNode, ...] = ()
    unplaced: tuple[Agent, ...] = field(default=())
    """Agents with no department yet — hired before the organisation existed, or by a caller
    that skipped ``assign``. They still work; they are simply not on the chart."""

    @property
    def headcount(self) -> int:
        return sum(
            node.headcount for unit in (self.shared, *self.units) for node in unit.departments
        ) + len(self.unplaced)


async def org_chart(session: AsyncSession, company_id: uuid.UUID) -> OrgChart:
    """The whole organisation, assembled. Four queries, no recursion in SQL."""
    units = list(
        await session.scalars(
            select(BusinessUnit)
            .where(BusinessUnit.company_id == company_id)
            .order_by(BusinessUnit.key)
        )
    )
    departments = list(
        await session.scalars(
            select(Department).where(Department.company_id == company_id).order_by(Department.key)
        )
    )
    roles = list(
        await session.scalars(select(Role).where(Role.company_id == company_id).order_by(Role.key))
    )
    agents = list(
        await session.scalars(
            select(Agent)
            .where(Agent.company_id == company_id, Agent.status != AgentStatus.RETIRED.value)
            .order_by(Agent.display_name)
        )
    )
    products = list(
        await session.scalars(
            select(Product).where(Product.company_id == company_id).order_by(Product.key)
        )
    )

    roles_of: dict[uuid.UUID, list[Role]] = {}
    for role in roles:
        roles_of.setdefault(role.department_id, []).append(role)
    agents_of: dict[uuid.UUID | None, list[Agent]] = {}
    for agent in agents:
        agents_of.setdefault(agent.department_id, []).append(agent)
    children: dict[uuid.UUID | None, list[Department]] = {}
    for department in departments:
        children.setdefault(department.parent_department_id, []).append(department)

    def node(department: Department) -> DepartmentNode:
        return DepartmentNode(
            department=department,
            roles=tuple(roles_of.get(department.id, ())),
            agents=tuple(agents_of.get(department.id, ())),
            teams=tuple(node(team) for team in children.get(department.id, ())),
        )

    tops = children.get(None, [])
    products_of: dict[uuid.UUID, list[Product]] = {}
    for product in products:
        products_of.setdefault(product.business_unit_id, []).append(product)

    return OrgChart(
        company_id=company_id,
        shared=UnitNode(
            unit=None,
            departments=tuple(node(d) for d in tops if d.business_unit_id is None),
        ),
        units=tuple(
            UnitNode(
                unit=unit,
                departments=tuple(node(d) for d in tops if d.business_unit_id == unit.id),
                products=tuple(products_of.get(unit.id, ())),
            )
            for unit in units
        ),
        unplaced=tuple(agents_of.get(None, ())),
    )


async def role_by_key(session: AsyncSession, company_id: uuid.UUID, key: str) -> Role | None:
    return await _by_key(session, Role, company_id, key)


async def department_by_key(
    session: AsyncSession, company_id: uuid.UUID, key: str
) -> Department | None:
    return await _by_key(session, Department, company_id, key)


async def business_unit_by_key(
    session: AsyncSession, company_id: uuid.UUID, key: str
) -> BusinessUnit | None:
    return await _by_key(session, BusinessUnit, company_id, key)


async def product_by_key(session: AsyncSession, company_id: uuid.UUID, key: str) -> Product | None:
    return await _by_key(session, Product, company_id, key)


async def active_units(session: AsyncSession, company_id: uuid.UUID) -> Sequence[BusinessUnit]:
    """The businesses that are running — the ones a cycle plans for."""
    return list(
        await session.scalars(
            select(BusinessUnit)
            .where(
                BusinessUnit.company_id == company_id,
                BusinessUnit.state == BusinessUnitState.ACTIVE.value,
            )
            .order_by(BusinessUnit.key)
        )
    )


async def _by_key(session: AsyncSession, model, company_id: uuid.UUID, key: str):
    return await session.scalar(
        select(model).where(model.company_id == company_id, model.key == key)
    )


async def _unit(
    session: AsyncSession, company_id: uuid.UUID, business_unit_id: uuid.UUID
) -> BusinessUnit:
    unit = await session.get(BusinessUnit, business_unit_id)
    if unit is None or unit.company_id != company_id:
        raise UnknownUnit(f"no business unit {business_unit_id} in this company")
    return unit
