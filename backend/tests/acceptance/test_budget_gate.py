"""AC-13: the budget gate, end to end (尾-3).

Every piece of this chain already had its own test. Nobody had ever run the whole chain, which
is what the acceptance criterion actually asks for:

    a cap too small to pay for the next call
      -> the call is refused before it happens        (BUDGET_EXHAUSTED)
      -> the run ends as aborted, not failed          (AGENT_RUN_ABORTED{budget})
      -> the task waits instead of dying              (BLOCKED_BUDGET, agent WAITING{budget})
      -> money arrives through the command pipeline   (AllocateBudget)
      -> the work goes back in the queue by itself    (no human, no restart)
      -> and finishes.

**A free model cannot exhaust a budget.** Simulation prices everything at zero, so the guard's
estimate is zero and no cap is ever passed. This test prices the simulated model through
MODEL_PRICES (`router_from_settings`) — the gate is then exercised for real, without spending
real money on proving that spending stops.

Two places where the acceptance text and the system disagree, and the system is right:

- AC-13 says "agent FAILED{BudgetExceeded}". It is not: running out of money is not the agent
  failing at its work, so the agent goes WAITING{budget} and the run is ABORTED. Same reasoning
  as D-020 (a provider outage is not the agent's failure). The test asserts what the system
  does, and the acceptance text is corrected to match.
- AC-13 says "加預算後恢復" as if someone re-queues the work. Nobody does: allocating the budget
  releases the blocked task itself (補-3).
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from autora.app import build_runtime, build_worker
from autora.db.models import (
    AgentActivity,
    Budget,
    EventRecord,
    ModelCall,
    Task,
    TaskState,
    WorkflowRun,
)
from autora.runtime.actor import Actor
from tests.echo_fixtures import start_echo

OPERATOR = Actor.human("acceptance-operator")

PRICE = {"input": 10.0, "output": 50.0}
"""Dollars per million tokens. Enough that one call's estimate is worth cents, not fractions."""

CAP = Decimal("0.0032")
"""A cap the first call cannot fit under: small enough that nothing has to be spent first.
In TWD, like every budget (D-023) — $0.0001 at 32."""
CAP_USD = Decimal("0.0001")
"""The same cap as the guard sees it. The refusal is in the meter's currency, and says so."""


@pytest.fixture
def priced_settings(e2e_settings):
    """Simulation, but the simulated model costs money (see the module docstring)."""
    return e2e_settings.model_copy(update={"model_prices": {"fake-frontier": PRICE}})


async def _events(committed, company_id: uuid.UUID, kind: str) -> list[EventRecord]:
    async with committed() as session:
        return list(
            await session.scalars(
                select(EventRecord)
                .where(EventRecord.company_id == company_id, EventRecord.event_type == kind)
                .order_by(EventRecord.seq)
            )
        )


async def test_the_budget_gate_stops_the_work_and_money_starts_it_again(
    committed, priced_settings, echo_company
):
    """AC-13, the whole chain, with no human in it after the money arrives."""
    company_id = echo_company.company.id
    project_id = echo_company.project.id
    runtime = build_runtime(priced_settings)

    async with committed() as session:
        session.add(
            Budget(
                company_id=company_id,
                project_id=project_id,
                period="day",
                amount=CAP,
                hard_cap=True,
            )
        )
        await session.commit()

    run, _ = await start_echo(committed, echo_company)
    worker = build_worker(
        priced_settings, session_factory=committed, company_ids=frozenset({company_id}),
        runtime=runtime,
    )  # fmt: skip
    await worker.run_until_idle()

    # 1. the call was refused before it happened: no model call was made, and the refusal is
    #    in the timeline with the numbers that justify it
    refusals = await _events(committed, company_id, "BUDGET_EXHAUSTED")
    assert len(refusals) == 1, "the guard should refuse once, not once per retry"
    refusal = refusals[0].payload
    assert refusal["scope"] == "project"
    assert (Decimal(str(refusal["limit"])), refusal["currency"]) == (CAP_USD, "USD")
    assert Decimal(str(refusal["requested"])) > CAP_USD, "the call that did not fit"
    async with committed() as session:
        calls = list(
            await session.scalars(select(ModelCall).where(ModelCall.company_id == company_id))
        )
    assert calls == [], "a refused call must not reach the provider"

    # 2. the work waits; it has not failed, and neither has the agent
    async with committed() as session:
        tasks = list(await session.scalars(select(Task).where(Task.workflow_run_id == run.id)))
        blocked = [t for t in tasks if t.state == TaskState.BLOCKED_BUDGET.value]
        assert len(blocked) == 1, [t.state for t in tasks]
        doing = await session.get(AgentActivity, echo_company.agents["researcher"].id)
        assert doing.state == "WAITING", doing.state
        assert doing.detail["reason"] == "budget"
    aborted = await _events(committed, company_id, "AGENT_RUN_ABORTED")
    assert [e.payload["reason"] for e in aborted] == ["budget"]
    assert not await _events(committed, company_id, "TASK_FAILED"), "waiting is not failing"

    # 3. money arrives the way money arrives: a command, decided and recorded
    async with committed() as session:
        result = await runtime.commands.submit(
            session,
            "AllocateBudget",
            {"amount": "160", "period": "day", "project_id": str(project_id)},  # = $5
            company_id=company_id,
            actor=OPERATOR,
            role="human",
            idempotency_key=f"budget-{uuid.uuid4()}",
        )
        await session.commit()
    assert result.done, result.record.reason
    assert result.result["released_tasks"] == [str(blocked[0].id)], "the money released the work"

    # 4. and the company finishes the job by itself
    await worker.run_until_idle()
    async with committed() as session:
        finished = await session.get(WorkflowRun, run.id)
        tasks = list(await session.scalars(select(Task).where(Task.workflow_run_id == run.id)))
        calls = list(
            await session.scalars(select(ModelCall).where(ModelCall.company_id == company_id))
        )
    assert finished.state == "SUCCEEDED", [t.state for t in tasks]
    assert all(t.state == TaskState.SUCCEEDED.value for t in tasks)
    assert calls, "after the cap was raised the calls actually happened"
