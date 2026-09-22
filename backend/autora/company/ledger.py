"""The ledger: the only module that writes ``transactions`` (platform/06 §3, T-602).

Everything the company spends or earns is one row in one table, and the sign lives in ``kind``,
never in the amount. Expenses and revenue are views over it, so "what is the balance" has
exactly one answer and nobody can arrive at a different one by counting somewhere else.

**Settling a cycle.** Model calls are recorded as they happen, on ``model_calls`` — that is the
meter. Once a day, when the cycle reaches MEASURING, the meter is read and turned into money:
one expense per project, for what that project's calls cost during that cycle. Two things make
this safe to run again (and a maintenance loop *will* run it again):

- the transaction's ``idempotency_key`` is ``cycle:<id>:project:<id>:model_cost``, and the
  database has a unique index on it, so a second settlement of the same cycle writes nothing;
- the amount is computed from the calls themselves, not added to a running total.

**Why not post a transaction per call?** Because a call is not a decision. Hundreds of calls a
day would bury the ledger in rows nobody reads, and P-7 only asks that the cycle's expenses
equal the cycle's model calls — which is exactly what one row per project per cycle gives, while
staying joinable back to the individual calls through ``ref_type``/``ref_id``.

Money that is not a model call (a human recording a subscription, a payment webhook in a later
phase) comes in through ``record`` with its own idempotency key. Tool costs will join
``settle_cycle`` when tools start recording what they cost; the grouping is already by category.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from autora.company import events as company_events
from autora.db.models import (
    Cycle,
    ModelCall,
    ModelCallStatus,
    Transaction,
    TransactionKind,
    TransactionSource,
)
from autora.infra.money import METER_CURRENCY, Fx
from autora.runtime.actor import Actor
from autora.runtime.events.outbox import emit
from autora.runtime.events.schema import new_event

log = logging.getLogger(__name__)

MODEL_COST = "model_cost"
"""Category of the expense a cycle's model calls settle into."""

MONEY = Decimal("0.000001")
"""Six decimal places, the precision ``transactions.amount`` and ``model_calls.cost_usd`` keep."""
# (autora.infra.money.MONEY is the same value; this name stays because callers import it here.)

SPENDING = (TransactionKind.EXPENSE, TransactionKind.CAPITAL_OUT)
EARNING = (TransactionKind.REVENUE, TransactionKind.CAPITAL_IN)
"""``transfer`` is inside the company and moves no money in or out, so it is in neither."""


class LedgerError(Exception):
    pass


def cycle_cost_key(cycle_id: uuid.UUID, project_id: uuid.UUID | None) -> str:
    """The idempotency key that makes settling a cycle repeatable."""
    project = project_id or "none"
    return f"cycle:{cycle_id}:project:{project}:{MODEL_COST}"


@dataclass(frozen=True)
class Settlement:
    """What settling a cycle wrote (or would have written, when it was already settled)."""

    cycle_id: uuid.UUID
    transactions: tuple[Transaction, ...]
    total: Decimal
    """What the meter said, in its own currency (USD). The rows hold it converted to the base,
    with this amount kept as each row's ``source_amount`` (D-023)."""
    already_settled: bool = False

    def __bool__(self) -> bool:
        return bool(self.transactions)


@dataclass
class Ledger:
    """Writes to ``transactions``; reads balances and spend. Does not commit."""

    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    fx: Fx = field(default_factory=Fx.from_settings)

    @property
    def actor(self) -> Actor:
        return Actor.system("ledger")

    # --- writing ----------------------------------------------------------------------------

    async def record(
        self,
        session: AsyncSession,
        *,
        company_id: uuid.UUID,
        kind: TransactionKind,
        category: str,
        amount: Decimal,
        idempotency_key: str,
        actor: Actor | None = None,
        project_id: uuid.UUID | None = None,
        business_unit_id: uuid.UUID | None = None,
        product_id: uuid.UUID | None = None,
        customer_id: uuid.UUID | None = None,
        occurred_at: datetime | None = None,
        source: TransactionSource = TransactionSource.SYSTEM,
        ref_type: str | None = None,
        ref_id: uuid.UUID | None = None,
        memo: str | None = None,
        currency: str | None = None,
    ) -> Transaction | None:
        """Post one transaction, or return None when its key was already used.

        The key is how a caller says "this is the same fact, not another one": settling a cycle
        twice, or a webhook delivered twice, must not double the money.

        ``currency`` is what the money arrived in, the base when omitted. Anything else is
        converted here and the row keeps the original and the rate (D-023): the ledger has one
        currency, and this is the only door into it.
        """
        converted = self.fx.to_base(Decimal(amount), currency or self.fx.base)
        amount = converted.amount
        if amount <= 0:
            raise LedgerError(f"a transaction must move money, got {amount}")
        existing = await session.scalar(
            select(Transaction).where(Transaction.idempotency_key == idempotency_key)
        )
        if existing is not None:
            return None
        transaction = Transaction(
            company_id=company_id,
            project_id=project_id,
            business_unit_id=business_unit_id,
            product_id=product_id,
            customer_id=customer_id,
            kind=kind.value,
            category=category,
            amount=amount,
            currency=self.fx.base,
            source_amount=converted.source_amount,
            source_currency=converted.source_currency,
            fx_rate=converted.rate,
            ref_type=ref_type,
            ref_id=ref_id,
            occurred_at=occurred_at or self.clock(),
            source=source.value,
            idempotency_key=idempotency_key,
            memo=memo,
        )
        session.add(transaction)
        await session.flush()
        payload = (
            company_events.RevenueRecorded if kind in EARNING else company_events.ExpenseRecorded
        )
        await emit(
            session,
            new_event(
                payload(
                    transaction_id=transaction.id,
                    project_id=project_id,
                    category=category,
                    amount=amount,
                    currency=self.fx.base,
                ),
                company_id=company_id,
                actor=actor or self.actor,
                aggregate_type="transaction",
                aggregate_id=transaction.id,
            ),
        )
        return transaction

    async def settle_cycle(self, session: AsyncSession, cycle: Cycle) -> Settlement:
        """Turn the cycle's model calls into one expense per project. Does not commit.

        Only successful calls are settled: a call the provider refused cost nothing, and a
        failed attempt that did consume tokens is recorded as ``ok`` with its cost like any
        other. Calls with no project (the CEO planning) settle into one company-level row.
        """
        per_project = (
            await session.execute(
                select(ModelCall.project_id, func.sum(ModelCall.cost_usd))
                .where(
                    ModelCall.cycle_id == cycle.id,
                    ModelCall.status == ModelCallStatus.OK.value,
                )
                .group_by(ModelCall.project_id)
            )
        ).all()
        written: list[Transaction] = []
        skipped = False
        total = Decimal(0)
        for project_id, cost in per_project:
            cost = Decimal(cost or 0).quantize(MONEY)
            if cost <= 0:
                continue  # a free endpoint prices calls at 0; there is no expense to record
            total += cost
            transaction = await self.record(
                session,
                company_id=cycle.company_id,
                kind=TransactionKind.EXPENSE,
                category=MODEL_COST,
                amount=cost,
                idempotency_key=cycle_cost_key(cycle.id, project_id),
                project_id=project_id,
                occurred_at=cycle.ended_at or self.clock(),
                ref_type="cycle",
                ref_id=cycle.id,
                memo=f"model calls of cycle {cycle.seq}",
                currency=METER_CURRENCY,
            )
            if transaction is None:
                skipped = True
            else:
                written.append(transaction)
        if skipped:
            log.info("cycle %s was already settled, in part or in full", cycle.id)
        return Settlement(cycle.id, tuple(written), total, already_settled=skipped)

    def stage_hook(self):
        """Settle the cycle on the way into MEASURING (registered on the CycleRunner)."""

        async def settle(session: AsyncSession, cycle: Cycle) -> None:
            await self.settle_cycle(session, cycle)

        return settle

    # --- reading ----------------------------------------------------------------------------

    async def balance(
        self, session: AsyncSession, company_id: uuid.UUID, *, at: datetime | None = None
    ) -> Decimal:
        """What the company has: everything that came in, less everything that went out.

        A sum, never a conversion: every row is already in the base currency (D-023)."""
        rows = (
            await session.execute(
                select(Transaction.kind, func.sum(Transaction.amount))
                .where(
                    Transaction.company_id == company_id,
                    *([Transaction.occurred_at <= at] if at else []),
                )
                .group_by(Transaction.kind)
            )
        ).all()
        totals = {kind: Decimal(amount or 0) for kind, amount in rows}
        earned = sum((totals.get(k.value, Decimal(0)) for k in EARNING), Decimal(0))
        spent = sum((totals.get(k.value, Decimal(0)) for k in SPENDING), Decimal(0))
        return (earned - spent).quantize(MONEY)

    async def spent(
        self,
        session: AsyncSession,
        company_id: uuid.UUID,
        *,
        project_id: uuid.UUID | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        category: str | None = None,
    ) -> Decimal:
        """What has been spent (settled expenses) in a window, optionally for one project.

        This reads the ledger, so it lags the meter: what this cycle has spent so far, before
        it is settled, is the cost guard's question, not the ledger's (``runtime/cost``).
        """
        conditions = [
            Transaction.company_id == company_id,
            Transaction.kind.in_([k.value for k in SPENDING]),
        ]
        if project_id is not None:
            conditions.append(Transaction.project_id == project_id)
        if since is not None:
            conditions.append(Transaction.occurred_at >= since)
        if until is not None:
            conditions.append(Transaction.occurred_at <= until)
        if category is not None:
            conditions.append(Transaction.category == category)
        total = await session.scalar(select(func.sum(Transaction.amount)).where(*conditions))
        return Decimal(total or 0).quantize(MONEY)

    async def cost_of(self, session: AsyncSession, *, workflow_run_id: uuid.UUID) -> Decimal:
        """What one piece of work cost end to end — for the newsroom, one article (AC-S8).

        Straight from the meter, not the ledger: a workflow is finer than a cycle, and the
        ledger deliberately does not keep a row per call. So it is in the meter's USD, like
        every per-run and per-task cost the office shows (D-023).
        """
        total = await session.scalar(
            select(func.sum(ModelCall.cost_usd)).where(
                ModelCall.workflow_run_id == workflow_run_id,
                ModelCall.status == ModelCallStatus.OK.value,
            )
        )
        return Decimal(total or 0).quantize(MONEY)

    async def metered(self, session: AsyncSession, *, cycle_id: uuid.UUID) -> Decimal:
        """What the cycle's model calls cost, read from the meter (USD).

        P-7 is the statement that this equals the cycle's settled expenses — their
        ``source_amount``, the USD before conversion; the test asserts it against a run rather
        than trusting either side.
        """
        total = await session.scalar(
            select(func.sum(ModelCall.cost_usd)).where(
                ModelCall.cycle_id == cycle_id,
                ModelCall.status == ModelCallStatus.OK.value,
            )
        )
        return Decimal(total or 0).quantize(MONEY)
