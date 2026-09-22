"""T-209: cost guard (run / task / project / company budgets)."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select, update

from autora.db.models import (
    Agent,
    Budget,
    CostReservation,
    Cycle,
    EventRecord,
    ModelCall,
    Project,
    Task,
)
from autora.runtime.cost import BudgetExceeded, DbCostGuard, estimate_cost
from autora.runtime.models import (
    CallContext,
    Message,
    ModelBinding,
    ModelGateway,
    ModelRequest,
    ModelRouter,
    Price,
    Usage,
)
from autora.runtime.models.providers.fake import FakeModelProvider, FakeTurn
from tests.conftest import running_agent_run

PRICE = Price(input=Decimal("10"), output=Decimal("50"))
BINDING = ModelBinding(provider="fake", model_id="fake-a", price=PRICE)
# max_output_tokens=1000 -> 0.05 output + a few input tokens ≈ 0.0502
MAX_OUT = 1000


@pytest.fixture
async def world(committed):
    async with committed() as session:
        run = await running_agent_run(session, "cost")
        task = await session.get(Task, run.task_id)
        await session.commit()
    ctx = CallContext(
        company_id=run.company_id,
        project_id=task.project_id,
        agent_id=run.agent_id,
        task_id=run.task_id,
        run_id=run.id,
        role="researcher",
        task_name="research",
    )
    return {"run": run, "task": task, "ctx": ctx}


def _request(ctx):
    return ModelRequest(
        capability="research_extraction",
        context=ctx,
        messages=[Message.user("Find today's AI stories")],
        max_output_tokens=MAX_OUT,
    )


async def _set(committed, model, id_, **values):
    async with committed() as session:
        await session.execute(update(model).where(model.id == id_).values(**values))
        await session.commit()


async def _add(committed, *rows):
    async with committed() as session:
        session.add_all(rows)
        await session.commit()


def _call(ctx, cost, *, project_id=None, created_at=None):
    return ModelCall(
        company_id=ctx.company_id,
        project_id=project_id or ctx.project_id,
        task_id=ctx.task_id,
        run_id=ctx.run_id,
        role="researcher",
        capability="research_extraction",
        alias="frontier",
        provider="fake",
        model_id="fake-a",
        status="ok",
        cost_usd=Decimal(cost),
        **({"created_at": created_at} if created_at else {}),
    )


async def _exhausted_events(committed, ctx):
    async with committed() as session:
        return (
            await session.scalars(
                select(EventRecord.payload).where(
                    EventRecord.company_id == ctx.company_id,
                    EventRecord.event_type == "BUDGET_EXHAUSTED",
                )
            )
        ).all()


def test_estimate_is_conservative():
    ctx = CallContext(company_id="01900000-0000-7000-8000-000000000001", role="r")
    estimate = estimate_cost(_request(ctx), BINDING)
    output_only = PRICE.cost(Usage(output_tokens=MAX_OUT))
    assert estimate > output_only
    assert estimate < output_only + Decimal("0.01")


async def test_no_limits_still_reserves(committed, world):
    guard = DbCostGuard(committed)
    reservation = await guard.reserve(_request(world["ctx"]), BINDING)
    assert reservation is not None and reservation.amount > 0


async def test_free_model_needs_no_reservation(committed, world):
    free = ModelBinding(provider="fake", model_id="fake-free", price=Price(input=0, output=0))
    assert await DbCostGuard(committed).reserve(_request(world["ctx"]), free) is None


async def test_run_budget_refuses_and_records_event(committed, world):
    await _set(committed, Agent, world["run"].agent_id, budget={"per_run_usd": "0.04"})
    with pytest.raises(BudgetExceeded) as exc:
        await DbCostGuard(committed).reserve(_request(world["ctx"]), BINDING)
    assert exc.value.scope == "run" and exc.value.limit == Decimal("0.04")

    [event] = await _exhausted_events(committed, world["ctx"])
    assert event["scope"] == "run" and event["limit"] == "0.04"
    assert Decimal(event["requested"]) == exc.value.requested


async def test_task_budget_counts_recorded_spend(committed, world):
    ctx = world["ctx"]
    await _set(committed, Task, ctx.task_id, budget_usd=Decimal("0.10"))
    guard = DbCostGuard(committed)
    await guard.reserve(_request(ctx), BINDING)  # 0.05 in flight, fits
    await _add(committed, _call(ctx, "0.02"))  # recorded

    with pytest.raises(BudgetExceeded) as exc:
        await guard.reserve(_request(ctx), BINDING)  # 0.05 + 0.02 + 0.05 > 0.10
    assert exc.value.scope == "task"
    assert exc.value.spent > Decimal("0.07")


async def test_released_and_stale_reservations_do_not_count(committed, world):
    ctx = world["ctx"]
    await _set(committed, Task, ctx.task_id, budget_usd=Decimal("0.11"))
    guard = DbCostGuard(committed)
    first = await guard.reserve(_request(ctx), BINDING)
    await guard.reserve(_request(ctx), BINDING)
    with pytest.raises(BudgetExceeded):
        await guard.reserve(_request(ctx), BINDING)

    async with committed() as session:
        await guard.release(session, first)
        await session.commit()
    await guard.reserve(_request(ctx), BINDING)  # released capacity is usable again

    later = DbCostGuard(committed, clock=lambda: datetime.now(UTC) + timedelta(minutes=16))
    await later.reserve(_request(ctx), BINDING)  # crashed-worker reservations have expired


async def test_project_daily_hard_cap_ignores_yesterday_and_soft_caps(committed, world):
    ctx = world["ctx"]
    await _add(
        committed,
        # budgets are TWD (D-023); the guard compares them in the meter's USD, at 32:
        Budget(company_id=ctx.company_id, project_id=ctx.project_id, period="day",
               amount=Decimal("3.20")),  # = $0.10
        Budget(company_id=ctx.company_id, project_id=ctx.project_id, period="month",
               amount=Decimal("0.32"), hard_cap=False),  # = $0.01
        _call(ctx, "5.00", created_at=datetime.now(UTC) - timedelta(days=1)),
    )  # fmt: skip
    guard = DbCostGuard(committed)
    await guard.reserve(_request(ctx), BINDING)  # yesterday's $5 and the soft cap don't count

    await _add(committed, _call(ctx, "0.03"))
    with pytest.raises(BudgetExceeded) as exc:
        await guard.reserve(_request(ctx), BINDING)
    assert exc.value.scope == "project"


async def test_a_budget_is_compared_in_the_meter_s_currency_not_by_its_number(committed, world):
    """D-023: NT$1.50 is a bigger number than a $0.05 call and a smaller amount of money.
    Comparing the numbers as they stand would let the call through."""
    ctx = world["ctx"]
    assert Decimal("0.04") < estimate_cost(_request(ctx), BINDING) < Decimal("0.06")
    await _add(committed, Budget(company_id=ctx.company_id, period="day", amount=Decimal("1.50")))
    with pytest.raises(BudgetExceeded) as exc:
        await DbCostGuard(committed).reserve(_request(ctx), BINDING)
    assert exc.value.scope == "company"

    await _set_budget(committed, ctx.company_id, Decimal("2.00"))  # = $0.0625, room for one
    assert await DbCostGuard(committed).reserve(_request(ctx), BINDING) is not None


async def _set_budget(committed, company_id, amount):
    async with committed() as session:
        budget = await session.scalar(select(Budget).where(Budget.company_id == company_id))
        budget.amount = amount
        await session.commit()


async def test_company_cap_spans_projects(committed, world):
    ctx = world["ctx"]
    async with committed() as session:
        other = Project(company_id=ctx.company_id, name="Other project")
        session.add(other)
        await session.commit()
    await _add(
        committed,
        Budget(company_id=ctx.company_id, period="day", amount=Decimal("2.56")),  # = $0.08
        _call(ctx, "0.04", project_id=other.id),
    )
    with pytest.raises(BudgetExceeded) as exc:
        await DbCostGuard(committed).reserve(_request(ctx), BINDING)
    assert exc.value.scope == "company"


async def test_concurrent_reservations_cannot_oversubscribe(committed, world):
    ctx = world["ctx"]
    await _set(committed, Task, ctx.task_id, budget_usd=Decimal("0.07"))  # room for one call
    guard = DbCostGuard(committed)
    results = await asyncio.gather(
        *(guard.reserve(_request(ctx), BINDING) for _ in range(5)), return_exceptions=True
    )
    granted = [r for r in results if not isinstance(r, Exception)]
    refused = [r for r in results if isinstance(r, BudgetExceeded)]
    assert (len(granted), len(refused)) == (1, 4)


# --- with the gateway --------------------------------------------------------------------


def _gateway(committed, fake):
    router = ModelRouter(aliases={"frontier": BINDING}, routes={"*.*": "frontier"})
    return ModelGateway(
        router=router,
        providers={"fake": fake},
        session_factory=committed,
        cost_guard=DbCostGuard(committed),
    )


async def test_refused_call_never_reaches_the_provider(committed, world):
    ctx = world["ctx"]
    await _set(committed, Agent, ctx.agent_id, budget={"per_run_usd": "0.01"})
    fake = FakeModelProvider()
    fake.script("researcher", "research", 1, FakeTurn(text="should not happen"))

    with pytest.raises(BudgetExceeded):
        await _gateway(committed, fake).complete(_request(ctx))

    assert fake.requests == []
    async with committed() as session:
        calls = await session.scalar(
            select(func.count()).select_from(ModelCall).where(ModelCall.run_id == ctx.run_id)
        )
    assert calls == 0


async def test_successful_call_settles_its_reservation(committed, world):
    ctx = world["ctx"]
    fake = FakeModelProvider()
    fake.script(
        "researcher", "research", 1, FakeTurn(text="ok", input_tokens=500, output_tokens=40)
    )
    response = await _gateway(committed, fake).complete(_request(ctx))

    async with committed() as session:
        [reservation] = (
            await session.scalars(
                select(CostReservation).where(CostReservation.run_id == ctx.run_id)
            )
        ).all()
    assert reservation.model_call_id == response.model_call_id
    assert reservation.actual_cost == response.cost_usd == Decimal("0.007000")
    assert reservation.settled_at is not None and reservation.released_at is None
    assert reservation.amount > reservation.actual_cost, "estimate reserves the worst case"


async def test_failed_call_releases_its_reservation(committed, world):
    ctx = world["ctx"]
    fake = FakeModelProvider()
    fake.script("researcher", "research", 1, FakeTurn(error="Overloaded", fallback_allowed=False))
    with pytest.raises(Exception, match="Overloaded"):
        await _gateway(committed, fake).complete(_request(ctx))
    async with committed() as session:
        [reservation] = (
            await session.scalars(
                select(CostReservation).where(CostReservation.run_id == ctx.run_id)
            )
        ).all()
    assert reservation.released_at is not None and reservation.settled_at is None


async def test_a_cycle_budget_is_spent_against_the_cycle_not_the_calendar_day(committed, world):
    """T-601: the open cycle's start is the window. A call made earlier today, before the cycle
    began, belongs to the previous cycle and must not eat this one's budget."""
    ctx = world["ctx"]
    now = datetime.now(UTC)
    async with committed() as session:
        session.add(
            Cycle(
                company_id=ctx.company_id,
                seq=1,
                stage="EXECUTING",
                started_at=now - timedelta(minutes=30),
            )
        )
        await session.commit()
    await _add(
        committed,
        Budget(company_id=ctx.company_id, period="cycle", amount=Decimal("2.56")),  # = $0.08
        _call(ctx, "0.50", created_at=now - timedelta(hours=3)),  # before this cycle began
    )

    await DbCostGuard(committed).reserve(_request(ctx), BINDING)  # the old call does not count

    await _add(committed, _call(ctx, "0.04", created_at=now - timedelta(minutes=5)))
    with pytest.raises(BudgetExceeded) as exc:
        await DbCostGuard(committed).reserve(_request(ctx), BINDING)
    assert exc.value.scope == "company"


async def test_without_an_open_cycle_a_cycle_budget_falls_back_to_the_day(committed, world):
    ctx = world["ctx"]
    now = datetime.now(UTC)
    await _add(
        committed,
        Budget(company_id=ctx.company_id, period="cycle", amount=Decimal("2.56")),  # = $0.08
        _call(ctx, "0.04", created_at=now.replace(hour=0, minute=1)),
    )
    with pytest.raises(BudgetExceeded) as exc:
        await DbCostGuard(committed).reserve(_request(ctx), BINDING)
    assert exc.value.scope == "company"
