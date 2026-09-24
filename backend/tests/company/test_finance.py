"""T-705: the finance agent reads the books and proposes budgets — and has no way to move money.

The acceptance criterion is a negative one — *no path by which it writes a transaction* — so the
tests that matter most ask about every path, not the ones we thought of: every action the policy
engine knows is decided for the finance role, and none of them lets it act on its own. Then the
one thing it may do, asking for a budget, is followed all the way: it waits for a person, and even
approved it writes a budget and never a transaction.
"""

import ast
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select

from autora.app import build_policy_engine
from autora.company import simulation as company_simulation
from autora.company import verbs, verbs_business
from autora.company.agents.finance import (
    PROPOSE_COMMAND,
    REVIEW_BUDGETS,
    ROLE,
    BudgetReview,
    behaviors,
    every_proposal_waits_for_a_person,
    proposes_for_what_exists,
    said_what_it_proposed,
    the_numbers_are_the_reports,
)
from autora.company.agents.roster import hire_agent
from autora.company.commands import CommandBus
from autora.company.executive import operations_project
from autora.company.finance import TEMPLATE, FinanceDesk
from autora.company.finance import register_templates as register_finance_templates
from autora.company.organization import (
    FINANCE_ROLE,
    add_business_unit,
    bootstrap_executive,
    role_by_key,
)
from autora.db.models import (
    AgentRun,
    Budget,
    BusinessUnitState,
    CommandOutcome,
    CommandRecord,
    Cycle,
    CycleStage,
    KpiScope,
    KpiSnapshot,
    Task,
    Transaction,
    WorkflowRun,
)
from autora.runtime.actor import Actor
from autora.runtime.approvals import ApprovalService
from autora.runtime.behaviors import RunContext
from autora.runtime.dag import TemplateRegistry, WorkflowEngine
from autora.runtime.task_manager import TaskManager
from tests.conftest import unique_company

HUMAN = Actor.human("founder")
NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


def _bus() -> CommandBus:
    bus = CommandBus(policy=build_policy_engine(), approvals=ApprovalService(TaskManager()))
    verbs.register(bus)
    verbs_business.register(bus)
    bus.install()
    return bus


async def _world(session, *, hire=True):
    company = await unique_company(session, "finance")
    await bootstrap_executive(session, company.id, actor=HUMAN)
    unit = await add_business_unit(
        session, company_id=company.id, key="ai_media", name="AI Media",
        actor=HUMAN, state=BusinessUnitState.ACTIVE,
    )  # fmt: skip
    agent = None
    if hire:
        agent = await hire_agent(
            session, company_id=company.id, role=ROLE, display_name="Fen", actor=HUMAN,
            position=await role_by_key(session, company.id, FINANCE_ROLE),
        )  # fmt: skip
    return company, unit, agent


async def _run(session, company, agent, *, finished=False) -> RunContext:
    """A finance run, as the runner would have it. An agent has one open run at a time (the
    database says so), so a second in the same test needs the first one finished."""
    project = await operations_project(session, company.id, actor=HUMAN)
    task = Task(
        company_id=company.id, project_id=project.id, name=REVIEW_BUDGETS,
        display_name="Review budgets", required_role=ROLE, state="READY",
    )  # fmt: skip
    session.add(task)
    await session.flush()
    run = AgentRun(
        company_id=company.id, agent_id=agent.id, task_id=task.id, attempt=1,
        state="COMPLETED" if finished else "CREATED", finished_at=NOW if finished else None,
    )  # fmt: skip
    session.add(run)
    await session.flush()
    return RunContext(
        company_id=company.id, project_id=project.id, task=task, agent=agent, run_id=run.id
    )


async def _report(session, company, metrics, *, unit=None):
    scope = KpiScope.BUSINESS_UNIT if unit else KpiScope.COMPANY
    session.add(
        KpiSnapshot(
            company_id=company.id, business_unit_id=unit.id if unit else None, scope=scope.value,
            metrics=metrics, period_start=NOW - timedelta(days=1), period_end=NOW,
        )
    )  # fmt: skip
    await session.flush()


async def _ask_for_budget(session, bus, company, agent, ctx, *, amount="320", unit=None):
    payload = {"amount": amount, "period": "cycle"}
    if unit is not None:
        payload["business_unit_id"] = str(unit.id)
    return await bus.submit(
        session, PROPOSE_COMMAND, payload, company_id=company.id, actor=Actor.agent(agent.id),
        role=ROLE, idempotency_key=f"k-{uuid.uuid4().hex[:8]}", run_id=ctx.run_id,
    )  # fmt: skip


async def _count(session, model, company) -> int:
    return await session.scalar(
        select(func.count()).select_from(model).where(model.company_id == company.id)
    )


# --- the acceptance criterion: no path to write a transaction ------------------------------------


def test_it_has_one_tool_and_the_prompt_promises_no_power_it_does_not_have():
    [review] = behaviors()
    assert (review.role, review.task_name) == (ROLE, REVIEW_BUDGETS)
    assert review.tools == ("submit_command",)
    assert "You cannot move money" in review.system_prompt
    assert "goes to a person" in review.system_prompt


def test_no_action_the_policy_knows_lets_the_finance_role_act_on_its_own():
    """Every action, a domain's and any "any agent may" rule included: asked, not assumed."""
    engine = build_policy_engine()
    finance = Actor.agent(uuid.uuid4())
    alone = {
        action: engine.decide(finance, action, role=ROLE).outcome
        for action in engine._actions  # noqa: SLF001 - the point is to ask about all of them
    }
    # the one it may use freely is the tool itself; what the tool asks for is decided again
    assert alone.pop("submit_command") == "allow"
    allowed = sorted(action for action, outcome in alone.items() if outcome == "allow")
    assert allowed == [], f"the finance role may act alone on: {allowed}"
    assert alone["record_transaction"] == "deny"
    assert alone["allocate_budget"] == "needs_approval"
    assert alone["payment"] == "needs_approval"


def test_no_registered_command_writes_a_transaction():
    """There is no command that records a transaction at all: the ledger writes the ledger."""
    bus = _bus()
    assert "RecordTransaction" not in bus.specs
    actions = {spec.action for spec in bus.specs.values()}
    assert "record_transaction" not in actions


def test_the_finance_code_does_not_reach_the_ledger():
    """Nothing it runs could post a row: it imports neither the ledger nor the transaction model."""
    root = Path(__file__).resolve().parents[2] / "autora" / "company"
    for path in (root / "agents" / "finance.py", root / "finance.py"):
        tree = ast.parse(path.read_text())
        names = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        } | {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        assert not names & {"Ledger", "Transaction", "autora.company.ledger"}, path.name


async def test_a_budget_it_asks_for_waits_for_a_person_and_even_approved_writes_no_transaction(
    db_session,
):
    company, unit, agent = await _world(db_session)
    ctx = await _run(db_session, company, agent)
    bus = _bus()
    transactions = await _count(db_session, Transaction, company)

    asked = await _ask_for_budget(db_session, bus, company, agent, ctx, amount="320", unit=unit)
    assert asked.record.outcome == CommandOutcome.AWAITING_APPROVAL.value
    assert asked.approval is not None
    assert await db_session.scalar(select(Budget).where(Budget.business_unit_id == unit.id)) is None

    await bus.approvals.decide(db_session, asked.approval.id, outcome="approve", actor=HUMAN)
    budget = await db_session.scalar(select(Budget).where(Budget.business_unit_id == unit.id))
    assert budget is not None and budget.amount == Decimal("320")
    assert await _count(db_session, Transaction, company) == transactions, (
        "a budget is not a transaction"
    )


async def test_a_person_can_say_no(db_session):
    company, unit, agent = await _world(db_session)
    ctx = await _run(db_session, company, agent)
    bus = _bus()
    asked = await _ask_for_budget(db_session, bus, company, agent, ctx, unit=unit)
    await bus.approvals.decide(
        db_session, asked.approval.id, outcome="reject", actor=HUMAN, reason="not yet"
    )
    assert await db_session.scalar(select(Budget).where(Budget.business_unit_id == unit.id)) is None


# --- what it may not claim ------------------------------------------------------------------------


def _review(**overrides) -> BudgetReview:
    return BudgetReview.model_validate(
        {"summary": "Revenue is flat; costs rose.", "proposals": [], **overrides}
    )


def _proposal(approval_id=None, **overrides):
    return {
        "approval_id": str(approval_id) if approval_id else None,
        "amount": "320",
        "reason": "The business earned nothing on a budget twice this size.",
        "evidence": [{"metric": "revenue", "value": "0"}],
        **overrides,
    }


async def test_a_proposal_it_reports_is_an_approval_this_run_opened(db_session):
    company, unit, agent = await _world(db_session)
    ctx = await _run(db_session, company, agent)
    asked = await _ask_for_budget(db_session, _bus(), company, agent, ctx)
    honest = _review(proposals=[_proposal(asked.approval.id)])
    assert await every_proposal_waits_for_a_person(db_session, ctx, honest) == []

    invented = _review(proposals=[_proposal(uuid.uuid4())])
    assert "was not opened by this run" in " ".join(
        await every_proposal_waits_for_a_person(db_session, ctx, invented)
    )
    unsent = _review(proposals=[_proposal(None)])
    assert "was not sent" in " ".join(
        await every_proposal_waits_for_a_person(db_session, ctx, unsent)
    )


async def test_an_allocation_that_went_through_without_a_person_is_refused(db_session):
    """If the policy ever let a finance allocation through on its own, the review says so. The
    policy is tested to forbid it; this is the second lock, for the day someone loosens the first.
    """
    company, unit, agent = await _world(db_session)
    ctx = await _run(db_session, company, agent)
    db_session.add(
        CommandRecord(
            company_id=company.id,
            command=PROPOSE_COMMAND,
            actor={"kind": "agent", "id": str(agent.id)},
            role=ROLE,
            decision="allow",
            outcome=CommandOutcome.DONE.value,
            idempotency_key=f"test:{uuid.uuid4()}",
            run_id=ctx.run_id,
        )
    )
    await db_session.flush()
    issues = await every_proposal_waits_for_a_person(
        db_session, ctx, _review(unchanged_because="The allocation already went through.")
    )
    assert "AllocateBudget went through without a person (done)" in " ".join(issues)


async def test_it_may_not_leave_out_a_proposal_it_sent_or_fall_silent(db_session):
    company, unit, agent = await _world(db_session)
    ctx = await _run(db_session, company, agent, finished=True)
    await _ask_for_budget(db_session, _bus(), company, agent, ctx)
    hidden = _review(unchanged_because="all fine")
    assert "sent 1 AllocateBudget command(s) and reports 0" in " ".join(
        await said_what_it_proposed(db_session, ctx, hidden)
    )

    quiet_ctx = await _run(db_session, company, agent)
    assert "does not say why" in " ".join(
        await said_what_it_proposed(db_session, quiet_ctx, _review())
    )
    assert (
        await said_what_it_proposed(
            db_session, quiet_ctx, _review(unchanged_because="Costs are within the cap.")
        )
        == []
    )


async def test_every_number_it_cites_is_the_one_the_report_stored(db_session):
    company, unit, agent = await _world(db_session)
    ctx = await _run(db_session, company, agent)
    await _report(db_session, company, {"revenue": "0.000000", "cost": "412.500000", "members": 3})
    await _report(db_session, company, {"revenue": "0.000000", "members": 3}, unit=unit)

    def cite(*evidence):
        return _review(proposals=[_proposal(uuid.uuid4(), evidence=list(evidence))])

    in_unit = {
        "metric": "revenue",
        "scope": "business_unit",
        "scope_id": str(unit.id),
        "value": "0",
    }
    right = cite({"metric": "cost", "value": "412.5"}, {"metric": "members", "value": "3"}, in_unit)
    assert await the_numbers_are_the_reports(db_session, ctx, right) == []

    wrong = " ".join(
        await the_numbers_are_the_reports(db_session, ctx, cite({"metric": "cost", "value": "12"}))
    )
    assert "cost is 412.500000 in the report, not 12" in wrong
    unknown = " ".join(
        await the_numbers_are_the_reports(
            db_session, ctx, cite({"metric": "market_size", "value": "2e9"})
        )
    )
    assert "market_size is not in the company report" in unknown
    nameless = " ".join(
        await the_numbers_are_the_reports(
            db_session, ctx, cite({"metric": "revenue", "scope": "business_unit", "value": "0"})
        )
    )
    assert "must name which one" in nameless


async def test_a_number_from_a_business_nobody_measured_is_not_evidence(db_session):
    company, unit, agent = await _world(db_session)
    ctx = await _run(db_session, company, agent)
    in_unit = {
        "metric": "revenue",
        "scope": "business_unit",
        "scope_id": str(unit.id),
        "value": "0",
    }
    review = _review(proposals=[_proposal(uuid.uuid4(), evidence=[in_unit])])
    assert "there is no report for that business_unit" in " ".join(
        await the_numbers_are_the_reports(db_session, ctx, review)
    )


async def test_it_proposes_only_for_what_this_company_has(db_session):
    company, unit, agent = await _world(db_session)
    other, other_unit, _ = await _world(db_session, hire=False)
    ctx = await _run(db_session, company, agent)
    foreign = _review(proposals=[_proposal(uuid.uuid4(), business_unit_id=str(other_unit.id))])
    assert "no business" in " ".join(await proposes_for_what_exists(db_session, ctx, foreign))
    both = _review(
        proposals=[
            _proposal(uuid.uuid4(), business_unit_id=str(unit.id), project_id=str(uuid.uuid4()))
        ]
    )
    assert "not both" in " ".join(await proposes_for_what_exists(db_session, ctx, both))


def test_it_proposes_a_few_changes_at_most():
    with pytest.raises(ValueError):
        _review(proposals=[_proposal(uuid.uuid4()) for _ in range(4)])


# --- when it is asked -----------------------------------------------------------------------------


def _desk() -> FinanceDesk:
    templates = TemplateRegistry()
    register_finance_templates(templates)
    return FinanceDesk(WorkflowEngine(TaskManager(), templates))


async def _cycle(session, company) -> Cycle:
    cycle = Cycle(company_id=company.id, seq=1, stage=CycleStage.MEASURING.value, started_at=NOW)
    session.add(cycle)
    await session.flush()
    return cycle


async def test_measuring_asks_the_finance_agent_and_waits_for_its_review(db_session):
    company, unit, agent = await _world(db_session)
    cycle = await _cycle(db_session, company)
    desk = _desk()
    await desk.review_hook()(db_session, cycle)
    await desk.review_hook()(db_session, cycle)  # entered twice: still one review

    runs = (
        await db_session.scalars(select(WorkflowRun).where(WorkflowRun.cycle_id == cycle.id))
    ).all()
    assert [run.template_name for run in runs] == [TEMPLATE]
    assert await desk.measuring_is_done()(db_session, cycle) is False, "the review has not run yet"

    task = await db_session.scalar(select(Task).where(Task.workflow_run_id == runs[0].id))
    assert (task.name, task.required_role) == (REVIEW_BUDGETS, ROLE)
    task.state = "SUCCEEDED"
    await db_session.flush()
    assert await desk.measuring_is_done()(db_session, cycle) is True


async def test_a_company_without_a_finance_agent_measures_as_it_always_did(db_session):
    company, unit, _ = await _world(db_session, hire=False)
    cycle = await _cycle(db_session, company)
    desk = _desk()
    await desk.review_hook()(db_session, cycle)
    assert (
        await db_session.scalar(
            select(func.count()).select_from(WorkflowRun).where(WorkflowRun.cycle_id == cycle.id)
        )
        == 0
    )
    assert await desk.measuring_is_done()(db_session, cycle) is True


async def test_every_company_has_the_chair_and_hiring_fills_it(db_session):
    company, unit, agent = await _world(db_session)
    chair = await role_by_key(db_session, company.id, FINANCE_ROLE)
    assert chair is not None and chair.title == "CFO"
    assert (agent.role_id, agent.department_id) == (chair.id, chair.department_id)
    assert FINANCE_ROLE == ROLE, "the position and the behaviour must name the same role"


# --- the simulated finance officer ---------------------------------------------------------


def test_the_scripted_finance_officer_reports_and_proposes_nothing():
    """In simulation it reads the numbers and asks for nothing: a script must not put invented
    judgement in front of a person deciding about real money."""
    from autora.runtime.models.types import Message, ModelRequest

    snapshot = (
        '{"company": "x", "last_cycle": {"kpis": {"revenue": "360.000000", "cost": "12.000000"}}}'
    )
    request = ModelRequest.model_construct(
        context=type("Context", (), {"role": ROLE, "task_name": REVIEW_BUDGETS})(),
        messages=[Message.user("The company right now:\n" + snapshot)],
    )
    turn = company_simulation.respond(request)
    review = BudgetReview.model_validate_json(turn.text)
    assert review.proposals == []
    assert review.unchanged_because
    assert "360.000000" in review.summary and "12.000000" in review.summary
