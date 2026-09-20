"""T-602: the ledger. One table for money, and settling a cycle is safe to repeat.

The invariant these tests exist for is P-7: a cycle's expenses equal that cycle's model calls,
and one article's cost is answerable on its own.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from autora.company.ledger import MODEL_COST, Ledger, LedgerError, cycle_cost_key
from autora.db.models import (
    Cycle,
    EventRecord,
    ModelCall,
    Project,
    Transaction,
    TransactionKind,
    TransactionSource,
    WorkflowRun,
)
from tests.conftest import unique_company

NOW = datetime(2026, 9, 21, 20, 0, tzinfo=UTC)
K = TransactionKind


def _ledger() -> Ledger:
    return Ledger(clock=lambda: NOW)


async def _world(session, *, projects: int = 1):
    company = await unique_company(session, "ledger")
    rows = [Project(company_id=company.id, name=f"p{n}") for n in range(projects)]
    cycle = Cycle(
        company_id=company.id, seq=1, stage="MEASURING", started_at=NOW - timedelta(hours=14)
    )  # fmt: skip
    session.add_all([*rows, cycle])
    await session.flush()
    return company, rows, cycle


def _call(company, cycle, project, cost, *, status="ok", workflow_run_id=None):
    return ModelCall(
        company_id=company.id, project_id=project.id if project else None,
        cycle_id=cycle.id if cycle else None, workflow_run_id=workflow_run_id,
        role="writer", capability="drafting", alias="frontier", provider="nvidia",
        model_id="m", status=status, cost_usd=Decimal(cost),
    )  # fmt: skip


async def _expenses(session, company_id):
    return (
        await session.scalars(
            select(Transaction)
            .where(Transaction.company_id == company_id, Transaction.category == MODEL_COST)
            .order_by(Transaction.amount)
        )
    ).all()


# --- settling -----------------------------------------------------------------------------


async def test_a_cycle_settles_into_one_expense_per_project(db_session):
    company, (first, second), cycle = await _world(db_session, projects=2)
    db_session.add_all([
        _call(company, cycle, first, "0.40"),
        _call(company, cycle, first, "0.10"),
        _call(company, cycle, second, "0.25"),
    ])  # fmt: skip
    await db_session.flush()

    settlement = await _ledger().settle_cycle(db_session, cycle)

    assert settlement.total == Decimal("0.750000")
    assert not settlement.already_settled
    rows = await _expenses(db_session, company.id)
    assert [(r.project_id, r.amount) for r in rows] == [
        (second.id, Decimal("0.250000")),
        (first.id, Decimal("0.500000")),
    ]
    assert all(r.kind == K.EXPENSE and r.ref_type == "cycle" and r.ref_id == cycle.id for r in rows)
    assert all(r.occurred_at == NOW for r in rows)  # the cycle has not ended: settled now


async def test_settling_twice_writes_nothing_the_second_time(db_session):
    """The maintenance loop will call this again. It must be boring when it does."""
    company, (project,), cycle = await _world(db_session)
    db_session.add(_call(company, cycle, project, "0.30"))
    await db_session.flush()
    ledger = _ledger()

    first = await ledger.settle_cycle(db_session, cycle)
    again = await ledger.settle_cycle(db_session, cycle)

    assert first and not again
    assert again.already_settled
    assert len(await _expenses(db_session, company.id)) == 1
    assert await ledger.spent(db_session, company.id) == Decimal("0.300000")


async def test_a_call_made_after_the_settlement_is_picked_up_by_the_next_one(db_session):
    """Settling is per cycle, so a late call of the *same* cycle is not silently lost — it is
    reported, because its project's key is taken."""
    company, (project,), cycle = await _world(db_session)
    db_session.add(_call(company, cycle, project, "0.30"))
    await db_session.flush()
    ledger = _ledger()
    await ledger.settle_cycle(db_session, cycle)

    db_session.add(_call(company, cycle, project, "0.05"))
    await db_session.flush()
    again = await ledger.settle_cycle(db_session, cycle)

    assert again.already_settled and not again.transactions
    assert await ledger.spent(db_session, company.id) == Decimal("0.300000")
    # the meter still knows the truth, and it no longer matches the books
    assert await ledger.metered(db_session, cycle_id=cycle.id) == Decimal("0.350000")


async def test_failed_calls_and_free_calls_cost_nothing(db_session):
    company, (project,), cycle = await _world(db_session)
    db_session.add_all([
        _call(company, cycle, project, "0.50", status="error"),  # refused: no tokens, no cost
        _call(company, cycle, project, "0"),                      # the free endpoint
    ])  # fmt: skip
    await db_session.flush()

    settlement = await _ledger().settle_cycle(db_session, cycle)

    assert not settlement and settlement.total == 0
    assert await _expenses(db_session, company.id) == []


async def test_work_with_no_project_settles_at_company_level(db_session):
    """The CEO planning a cycle belongs to no project, and must still be paid for."""
    company, _, cycle = await _world(db_session, projects=0)
    db_session.add(_call(company, cycle, None, "0.12"))
    await db_session.flush()

    await _ledger().settle_cycle(db_session, cycle)

    (row,) = await _expenses(db_session, company.id)
    assert row.project_id is None
    assert row.idempotency_key == cycle_cost_key(cycle.id, None)


async def test_a_settled_cycle_is_dated_when_it_ended(db_session):
    company, (project,), cycle = await _world(db_session)
    cycle.stage, cycle.ended_at = "DONE", NOW - timedelta(hours=1)
    db_session.add(_call(company, cycle, project, "0.20"))
    await db_session.flush()

    await _ledger().settle_cycle(db_session, cycle)

    (row,) = await _expenses(db_session, company.id)
    assert row.occurred_at == NOW - timedelta(hours=1)


async def test_another_cycle_s_calls_are_not_settled_here(db_session):
    company, (project,), cycle = await _world(db_session)
    other = Cycle(company_id=company.id, seq=2, stage="PLANNING", started_at=NOW)
    db_session.add(other)
    await db_session.flush()
    db_session.add_all([
        _call(company, cycle, project, "0.10"),
        _call(company, other, project, "0.90"),
        _call(company, None, project, "5.00"),  # made before cycles existed
    ])  # fmt: skip
    await db_session.flush()

    settlement = await _ledger().settle_cycle(db_session, cycle)

    assert settlement.total == Decimal("0.100000")


async def test_the_stage_hook_settles_on_the_way_into_measuring(db_session):
    company, (project,), cycle = await _world(db_session)
    db_session.add(_call(company, cycle, project, "0.30"))
    await db_session.flush()

    await _ledger().stage_hook()(db_session, cycle)

    assert len(await _expenses(db_session, company.id)) == 1


# --- P-7 ----------------------------------------------------------------------------------


async def test_what_a_cycle_cost_equals_what_it_was_charged(db_session):
    """P-7, stated as the two sides being computed independently and compared."""
    company, (first, second), cycle = await _world(db_session, projects=2)
    db_session.add_all([
        _call(company, cycle, first, "0.123456"),
        _call(company, cycle, second, "0.876544"),
        _call(company, cycle, first, "0.000001"),
        _call(company, cycle, None, "0.05"),
        _call(company, cycle, first, "9.99", status="error"),
    ])  # fmt: skip
    await db_session.flush()
    ledger = _ledger()

    settlement = await ledger.settle_cycle(db_session, cycle)

    metered = await ledger.metered(db_session, cycle_id=cycle.id)
    booked = await ledger.spent(db_session, company.id, category=MODEL_COST)
    assert metered == booked == settlement.total == Decimal("1.050001")


async def test_what_one_article_cost_end_to_end(db_session):
    """AC-S8: the sum over the workflow that produced it, not over the day it happened on."""
    company, (project,), cycle = await _world(db_session)
    article = WorkflowRun(
        company_id=company.id, project_id=project.id, cycle_id=cycle.id,
        template_name="newsroom.story_to_article_v2", state="SUCCEEDED",
    )  # fmt: skip
    another = WorkflowRun(
        company_id=company.id, project_id=project.id, cycle_id=cycle.id,
        template_name="newsroom.story_to_article_v2", state="RUNNING",
    )  # fmt: skip
    db_session.add_all([article, another])
    await db_session.flush()
    db_session.add_all([
        _call(company, cycle, project, "0.30", workflow_run_id=article.id),
        _call(company, cycle, project, "0.12", workflow_run_id=article.id),
        _call(company, cycle, project, "1.00", workflow_run_id=another.id),
        _call(company, cycle, project, "0.90", workflow_run_id=article.id, status="error"),
    ])  # fmt: skip
    await db_session.flush()

    assert await _ledger().cost_of(db_session, workflow_run_id=article.id) == Decimal("0.420000")


# --- recording and reading ------------------------------------------------------------------


async def test_money_in_and_out_reaches_the_balance(db_session):
    company, (project,), _ = await _world(db_session)
    ledger = _ledger()
    for kind, amount in (
        (K.CAPITAL_IN, "1000"),
        (K.REVENUE, "12.50"),
        (K.EXPENSE, "3"),
        (K.CAPITAL_OUT, "100"),
        (K.TRANSFER, "500"),  # inside the company: moves nothing in or out
    ):
        await ledger.record(
            db_session,
            company_id=company.id,
            kind=kind,
            category="ops",
            amount=Decimal(amount),
            idempotency_key=f"{kind}-1",
            project_id=project.id,
        )

    assert await ledger.balance(db_session, company.id) == Decimal("909.500000")
    assert await ledger.spent(db_session, company.id) == Decimal("103.000000")
    assert await ledger.spent(db_session, company.id, category="nothing") == 0


async def test_the_same_key_posts_once(db_session):
    company, _, _ = await _world(db_session, projects=0)
    ledger = _ledger()
    key = "webhook:evt_1"

    first = await ledger.record(
        db_session, company_id=company.id, kind=K.REVENUE, category="sponsorship",
        amount=Decimal("50"), idempotency_key=key, source=TransactionSource.INTEGRATION,
    )  # fmt: skip
    again = await ledger.record(
        db_session, company_id=company.id, kind=K.REVENUE, category="sponsorship",
        amount=Decimal("50"), idempotency_key=key, source=TransactionSource.INTEGRATION,
    )  # fmt: skip

    assert first is not None and again is None
    assert await ledger.balance(db_session, company.id) == Decimal("50.000000")


async def test_a_transaction_has_to_move_money(db_session):
    company, _, _ = await _world(db_session, projects=0)
    with pytest.raises(LedgerError, match="must move money"):
        await _ledger().record(
            db_session, company_id=company.id, kind=K.EXPENSE, category="ops",
            amount=Decimal("0"), idempotency_key="zero",
        )  # fmt: skip


async def test_every_posting_says_so_in_an_event(db_session):
    company, (project,), cycle = await _world(db_session)
    db_session.add(_call(company, cycle, project, "0.30"))
    await db_session.flush()
    ledger = _ledger()

    await ledger.settle_cycle(db_session, cycle)
    await ledger.record(
        db_session, company_id=company.id, kind=K.REVENUE, category="sponsorship",
        amount=Decimal("20"), idempotency_key="rev-1",
    )  # fmt: skip

    events = (
        await db_session.scalars(
            select(EventRecord)
            .where(EventRecord.company_id == company.id)
            .order_by(EventRecord.seq)
        )
    ).all()
    assert [e.event_type for e in events] == ["EXPENSE_RECORDED", "REVENUE_RECORDED"]
    expense, revenue = events
    assert expense.payload["category"] == MODEL_COST
    assert Decimal(str(expense.payload["amount"])) == Decimal("0.300000")
    assert expense.payload["project_id"] == str(project.id)
    assert revenue.payload["project_id"] is None
    assert all(e.actor["id"] == "ledger" for e in events)


async def test_a_balance_can_be_asked_for_as_of_a_moment(db_session):
    company, _, _ = await _world(db_session, projects=0)
    ledger = _ledger()
    for n, (kind, amount, when) in enumerate(
        ((K.CAPITAL_IN, "100", NOW - timedelta(days=2)), (K.EXPENSE, "30", NOW))
    ):
        await ledger.record(
            db_session, company_id=company.id, kind=kind, category="ops",
            amount=Decimal(amount), idempotency_key=f"k{n}", occurred_at=when,
        )  # fmt: skip

    assert await ledger.balance(db_session, company.id, at=NOW - timedelta(days=1)) == Decimal(
        "100.000000"
    )
    assert await ledger.balance(db_session, company.id) == Decimal("70.000000")


async def test_spend_can_be_narrowed_to_a_project_and_a_window(db_session):
    company, (first, second), _ = await _world(db_session, projects=2)
    ledger = _ledger()
    for n, (project, amount, when) in enumerate(
        (
            (first, "5", NOW - timedelta(days=3)),
            (first, "7", NOW),
            (second, "11", NOW),
        )
    ):
        await ledger.record(
            db_session, company_id=company.id, kind=K.EXPENSE, category="ads",
            amount=Decimal(amount), idempotency_key=f"ads{n}", project_id=project.id,
            occurred_at=when,
        )  # fmt: skip

    assert await ledger.spent(db_session, company.id, project_id=first.id) == Decimal("12.000000")
    assert await ledger.spent(
        db_session, company.id, project_id=first.id, since=NOW - timedelta(days=1)
    ) == Decimal("7.000000")
    assert await ledger.spent(db_session, company.id, until=NOW - timedelta(days=1)) == Decimal(
        "5.000000"
    )


async def test_the_ledger_is_the_only_writer_and_the_rows_never_change(db_session):
    """Not a rule the ledger enforces — the database does (migration 0002)."""
    company, _, _ = await _world(db_session, projects=0)
    row = await _ledger().record(
        db_session, company_id=company.id, kind=K.EXPENSE, category="ops",
        amount=Decimal("1"), idempotency_key="fixed",
    )  # fmt: skip
    await db_session.flush()

    row.amount = Decimal("2")
    with pytest.raises(Exception, match="append-only|immutable|not allowed|transactions"):
        await db_session.flush()
    await db_session.rollback()


async def test_an_unknown_cycle_settles_to_nothing(db_session):
    company, _, _ = await _world(db_session, projects=0)
    ghost = Cycle(company_id=company.id, seq=9, stage="MEASURING", started_at=NOW)
    db_session.add(ghost)
    await db_session.flush()

    settlement = await _ledger().settle_cycle(db_session, ghost)

    assert not settlement and settlement.total == 0 and not settlement.already_settled


async def test_a_company_with_no_money_has_a_zero_balance(db_session):
    assert await _ledger().balance(db_session, uuid.uuid4()) == 0
