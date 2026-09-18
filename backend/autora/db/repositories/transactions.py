"""Ledger persistence. Only the Ledger service (company layer, T-602) should call ``record``.

Transactions are append-only (trigger in migration 0002). There is intentionally no update or
delete function here.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from autora.db.models import Transaction, TransactionKind

_INFLOW = (TransactionKind.REVENUE, TransactionKind.CAPITAL_IN)
_OUTFLOW = (TransactionKind.EXPENSE, TransactionKind.CAPITAL_OUT)


async def record(session: AsyncSession, tx: Transaction) -> tuple[Transaction, bool]:
    """Insert ``tx`` unless its ``idempotency_key`` already exists.

    Returns ``(row, created)``. On a duplicate key the existing row is returned unchanged,
    so retries (worker crash, webhook redelivery) never double-count money.
    """
    values = {
        c.key: getattr(tx, c.key)
        for c in Transaction.__table__.columns
        if c.key not in ("created_at",) and getattr(tx, c.key) is not None
    }
    inserted_id = await session.scalar(
        insert(Transaction)
        .values(**values)
        .on_conflict_do_nothing(index_elements=[Transaction.idempotency_key])
        .returning(Transaction.id)
    )
    row = await session.scalar(
        select(Transaction).where(Transaction.idempotency_key == tx.idempotency_key)
    )
    assert row is not None
    return row, inserted_id is not None


async def balance(
    session: AsyncSession, company_id: uuid.UUID, *, currency: str = "USD"
) -> Decimal:
    """Cash position: inflows minus outflows. Transfers are internal and net to zero."""
    signed = case(
        (Transaction.kind.in_(_INFLOW), Transaction.amount),
        (Transaction.kind.in_(_OUTFLOW), -Transaction.amount),
        else_=0,
    )
    total = await session.scalar(
        select(func.coalesce(func.sum(signed), 0)).where(
            Transaction.company_id == company_id, Transaction.currency == currency
        )
    )
    return Decimal(total)
