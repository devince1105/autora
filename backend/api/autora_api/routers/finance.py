"""The company's money, as an operator sees and moves it from the admin dashboard (D-054).

Read: the balance, today's spend, the cycle, and every budget envelope with what it covers.
Write, two ways, both a person's:

- **a budget** goes through the command bus as ``AllocateBudget``, exactly as the CEO's and the
  CFO's do — the same policy, the same log, and raising one re-queues the work it blocked;
- **capital** is one ``capital_in`` row on the ledger, recorded as the operator's. The CEO plans
  from the balance, so a company with none plans to spend none (cycle 5 allocated NT$0).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from autora.company.verbs import AllocateBudget
from autora.db.models import (
    Budget,
    BusinessUnit,
    Company,
    Cycle,
    Project,
    ProjectState,
    TransactionKind,
    TransactionSource,
)
from autora_api.deps import Operator, RuntimeDep, Session

router = APIRouter(prefix="/api/companies/{company_id}/finance", tags=["finance"])

CAPITAL_CATEGORY = "capital_injection"


class BudgetLine(BaseModel):
    id: uuid.UUID
    scope: Literal["company", "project", "business_unit"]
    scope_id: uuid.UUID | None
    name: str
    """What it covers, as the dashboard names it: the project's or the business's name."""
    period: str
    amount: Decimal
    currency: str
    hard_cap: bool


class ProjectLine(BaseModel):
    id: uuid.UUID
    name: str
    state: str


class FinanceOut(BaseModel):
    currency: str
    balance: Decimal
    spent_today: Decimal
    cycle_seq: int | None
    cycle_stage: str | None
    budgets: list[BudgetLine]
    projects: list[ProjectLine]
    """The ones a budget can be put behind: active or paused."""


class BudgetIn(BaseModel):
    amount: Decimal = Field(ge=0)
    period: Literal["cycle", "day", "month"] = "cycle"
    project_id: uuid.UUID | None = None
    """None: the company-wide envelope."""


class BudgetOut(BaseModel):
    decision: str
    outcome: str | None
    reason: str | None
    released_tasks: int


class CapitalIn(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    memo: str | None = Field(default=None, max_length=200)
    request_id: uuid.UUID
    """The form's own id for this submission: sent twice, recorded once."""


class CapitalOut(BaseModel):
    recorded: bool
    """False when this ``request_id`` was already recorded."""
    balance: Decimal


async def _company(session: Session, company_id: uuid.UUID) -> Company:
    company = await session.get(Company, company_id)
    if company is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"company {company_id} not found")
    return company


@router.get("")
async def get_finance(
    company_id: uuid.UUID, session: Session, operator: Operator, runtime: RuntimeDep
) -> FinanceOut:
    """Operator-only, unlike the dashboard's other reads: this is the company's money."""
    await _company(session, company_id)
    ledger = runtime.ledger
    now = datetime.now(UTC)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    cycle = await session.scalar(
        select(Cycle).where(Cycle.company_id == company_id).order_by(Cycle.seq.desc()).limit(1)
    )
    projects = {
        p.id: p
        for p in await session.scalars(select(Project).where(Project.company_id == company_id))
    }
    units = {
        u.id: u.name
        for u in await session.scalars(
            select(BusinessUnit).where(BusinessUnit.company_id == company_id)
        )
    }
    lines = []
    for budget in await session.scalars(
        select(Budget).where(Budget.company_id == company_id).order_by(Budget.created_at)
    ):
        if budget.project_id is not None:
            project = projects.get(budget.project_id)
            scope, scope_id, name = "project", budget.project_id, project.name if project else "?"
        elif budget.business_unit_id is not None:
            scope, scope_id = "business_unit", budget.business_unit_id
            name = units.get(budget.business_unit_id, "?")
        else:
            scope, scope_id, name = "company", None, "公司"
        lines.append(
            BudgetLine(
                id=budget.id,
                scope=scope,
                scope_id=scope_id,
                name=name,
                period=budget.period,
                amount=budget.amount,
                currency=budget.currency,
                hard_cap=budget.hard_cap,
            )
        )
    open_states = {ProjectState.ACTIVE.value, ProjectState.PAUSED.value}
    return FinanceOut(
        currency=ledger.fx.base,
        balance=await ledger.balance(session, company_id),
        spent_today=await ledger.spent(session, company_id, since=day, until=now),
        cycle_seq=cycle.seq if cycle else None,
        cycle_stage=cycle.stage if cycle else None,
        budgets=lines,
        projects=[
            ProjectLine(id=p.id, name=p.name, state=p.state)
            for p in sorted(projects.values(), key=lambda p: p.created_at)
            if p.state in open_states
        ],
    )


@router.post("/budgets")
async def set_budget(
    company_id: uuid.UUID,
    body: BudgetIn,
    session: Session,
    operator: Operator,
    runtime: RuntimeDep,
) -> BudgetOut:
    """Set one envelope, as the ``AllocateBudget`` command (replacing the one there was)."""
    await _company(session, company_id)
    command = AllocateBudget(amount=body.amount, period=body.period, project_id=body.project_id)
    result = await runtime.commands.submit(
        session,
        "AllocateBudget",
        command.model_dump(mode="json"),
        company_id=company_id,
        actor=operator,
        idempotency_key=f"budget:{uuid.uuid4().hex}",
    )
    await session.commit()
    record = result.record
    return BudgetOut(
        decision=record.decision,
        outcome=record.outcome,
        reason=record.reason,
        released_tasks=len((record.result or {}).get("released_tasks") or []),
    )


@router.post("/capital")
async def add_capital(
    company_id: uuid.UUID,
    body: CapitalIn,
    session: Session,
    operator: Operator,
    runtime: RuntimeDep,
) -> CapitalOut:
    """Money put into the company, in the base currency."""
    await _company(session, company_id)
    row = await runtime.ledger.record(
        session,
        company_id=company_id,
        kind=TransactionKind.CAPITAL_IN,
        category=CAPITAL_CATEGORY,
        amount=body.amount,
        idempotency_key=f"capital:{body.request_id}",
        actor=operator,
        source=TransactionSource.HUMAN,
        memo=body.memo,
    )
    await session.commit()
    return CapitalOut(
        recorded=row is not None, balance=await runtime.ledger.balance(session, company_id)
    )
