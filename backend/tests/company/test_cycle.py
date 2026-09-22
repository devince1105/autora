"""T-601: the cycle keeps time on its own — it always moves, and it never moves backwards.

The clock is injected everywhere, so a whole day (and in ``test_seven_cycles_in_a_row`` a whole
week) passes here in milliseconds. That is the same mechanism T-610's accelerated soak uses.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from autora.company.cycle import (
    CYCLE_FSM,
    CYCLE_START_CRON,
    CYCLE_START_SCHEDULE,
    DEFAULT_STAGE_MINUTES,
    STAGE_MINUTES_POLICY,
    CycleAlreadyOpen,
    CycleRunner,
    ensure_cycle_schedule,
    maintenance_job,
    work_is_finished,
)
from autora.db.models import (
    Company,
    CompanyPolicy,
    Cycle,
    CycleStage,
    EventRecord,
    Project,
    StateTransition,
    Task,
    WorkflowRun,
)
from autora.runtime.fsm import IllegalTransition
from tests.conftest import unique_company

S = CycleStage
DAY_START = datetime(2026, 9, 21, 6, 0, tzinfo=UTC)


class FakeClock:
    """A clock the test moves by hand."""

    def __init__(self, now: datetime = DAY_START):
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta) -> datetime:
        self.now += timedelta(**delta)
        return self.now


async def _runner(clock: FakeClock | None = None) -> CycleRunner:
    runner = CycleRunner(clock=clock or FakeClock())
    runner.finishes_when(S.EXECUTING, work_is_finished)
    return runner


async def _events(session, company_id) -> list[str]:
    rows = await session.scalars(
        select(EventRecord.event_type)
        .where(EventRecord.company_id == company_id, EventRecord.event_type.like("CYCLE_%"))
        .order_by(EventRecord.seq)
    )
    return list(rows)


# --- starting ---------------------------------------------------------------------------


async def test_the_first_cycle_starts_in_planning_with_a_deadline(db_session):
    company = await unique_company(db_session, "cycle")
    clock = FakeClock()
    runner = await _runner(clock)

    cycle = await runner.start(db_session, company.id)

    assert (cycle.seq, cycle.stage) == (1, S.PLANNING)
    assert cycle.started_at == DAY_START
    assert cycle.stage_deadline == DAY_START + timedelta(minutes=DEFAULT_STAGE_MINUTES[S.PLANNING])
    assert cycle.ended_at is None
    assert await _events(db_session, company.id) == ["CYCLE_STARTED"]


async def test_a_second_cycle_is_refused_while_the_first_is_open(db_session):
    company = await unique_company(db_session, "cycle")
    runner = await _runner()
    first = await runner.start(db_session, company.id)

    with pytest.raises(CycleAlreadyOpen) as refused:
        await runner.start(db_session, company.id)

    assert refused.value.cycle.id == first.id
    assert "PLANNING" in str(refused.value)


async def test_the_daily_schedule_skips_a_day_whose_cycle_is_still_open(db_session):
    """The schedule must keep its cadence: a company that is behind is logged, not crashed."""
    company = await unique_company(db_session, "cycle")
    runner = await _runner()
    await runner.start(db_session, company.id)

    schedule = type("Schedule", (), {"company_id": company.id})()
    await runner.schedule_handler()(db_session, schedule, DAY_START)  # must not raise

    assert (
        await db_session.scalar(
            select(Cycle.seq).where(Cycle.company_id == company.id).order_by(Cycle.seq.desc())
        )
        == 1
    )


# --- advancing --------------------------------------------------------------------------


async def test_an_empty_company_completes_its_cycle_at_once(db_session):
    """Nothing planned means nothing to wait for. A company with no CEO and no work still keeps
    time: it does not sit through four deadlines to get to DONE."""
    company = await unique_company(db_session, "cycle")
    clock = FakeClock()
    runner = await _runner(clock)
    cycle = await runner.start(db_session, company.id)

    moved = await runner.tick(db_session, company.id)

    assert moved is not None and moved.id == cycle.id
    assert (moved.stage, moved.ended_at, moved.stage_deadline) == (S.DONE, DAY_START, None)
    assert await _events(db_session, company.id) == [
        "CYCLE_STARTED",
        "CYCLE_STAGE_CHANGED",
        "CYCLE_STAGE_CHANGED",
        "CYCLE_STAGE_CHANGED",
        "CYCLE_STAGE_CHANGED",
        "CYCLE_COMPLETED",
    ]


async def test_execution_ends_as_soon_as_its_workflows_are_done(db_session):
    company = await unique_company(db_session, "cycle")
    project = Project(company_id=company.id, name="p")
    db_session.add(project)
    await db_session.flush()
    clock = FakeClock()
    runner = await _runner(clock)
    run = _plans_one_workflow(runner, project)
    cycle = await runner.start(db_session, company.id)
    await runner.tick(db_session, company.id)
    assert cycle.stage == S.EXECUTING  # held by the workflow the plan started

    clock.advance(minutes=10)
    assert await runner.tick(db_session, company.id) is None  # the work is still running
    assert cycle.stage == S.EXECUTING

    run.row.state = "SUCCEEDED"
    await db_session.flush()
    moved = await runner.tick(db_session, company.id)

    assert moved is not None and moved.stage == S.DONE  # nothing holds the later stages
    assert moved.ended_at == clock.now
    assert "CYCLE_STAGE_TIMEOUT" not in await _events(db_session, company.id)


async def test_a_stage_that_runs_late_is_advanced_anyway_and_says_what_it_left(db_session):
    company = await unique_company(db_session, "cycle")
    project = Project(company_id=company.id, name="p")
    db_session.add(project)
    await db_session.flush()
    clock = FakeClock()
    runner = await _runner(clock)
    run = _plans_one_workflow(runner, project)
    cycle = await runner.start(db_session, company.id)
    await runner.tick(db_session, company.id)
    stuck = Task(
        company_id=company.id, project_id=project.id, workflow_run_id=run.row.id,
        cycle_id=cycle.id, name="research", display_name="Research",
        required_role="researcher", state="RUNNING", lease_owner="w1",
        lease_until=clock.now + timedelta(minutes=5), lease_token=uuid.uuid4(),
    )  # fmt: skip
    db_session.add(stuck)
    await db_session.flush()

    clock.advance(minutes=DEFAULT_STAGE_MINUTES[S.EXECUTING])
    moved = await runner.tick(db_session, company.id)

    assert moved is not None and moved.stage == S.DONE
    timeout = await db_session.scalar(
        select(EventRecord)
        .where(
            EventRecord.company_id == company.id, EventRecord.event_type == "CYCLE_STAGE_TIMEOUT"
        )
        .order_by(EventRecord.seq.desc())
    )
    assert timeout.payload["stage"] == "EXECUTING"
    assert timeout.payload["cancelled_task_ids"] == [str(stuck.id)]
    # the run is not cancelled: work that holds a lease finishes into the next stage
    assert (await db_session.get(Task, stuck.id)).state == "RUNNING"


async def test_every_move_is_audited_and_the_cycle_never_goes_backwards(db_session):
    company = await unique_company(db_session, "cycle")
    clock = FakeClock()
    runner = await _runner(clock)
    cycle = await runner.start(db_session, company.id)
    await runner.tick(db_session, company.id)

    moves = (
        await db_session.scalars(
            select(StateTransition)
            .where(StateTransition.entity_type == "cycle", StateTransition.entity_id == cycle.id)
            .order_by(StateTransition.id)
        )
    ).all()
    assert [(m.from_state, m.to_state) for m in moves] == [
        ("PLANNING", "EXECUTING"),
        ("EXECUTING", "MEASURING"),
        ("MEASURING", "REVIEWING"),
        ("REVIEWING", "DONE"),
    ]
    assert [m.reason for m in moves] == [None, None, None, None]  # nothing ran late
    assert all(m.actor["id"] == "cycle-runner" for m in moves)

    with pytest.raises(IllegalTransition):
        await CYCLE_FSM.transition(
            db_session, cycle, S.REVIEWING, actor=runner.actor
        )  # DONE is terminal


async def test_a_company_may_shorten_its_stages_with_a_policy(db_session):
    company = await unique_company(db_session, "cycle")
    db_session.add(
        CompanyPolicy(
            company_id=company.id,
            key=STAGE_MINUTES_POLICY,
            value={"PLANNING": 5, "EXECUTING": "nonsense"},
        )
    )
    await db_session.flush()
    project = Project(company_id=company.id, name="p")
    db_session.add(project)
    await db_session.flush()
    clock = FakeClock()
    runner = await _runner(clock)
    _plans_one_workflow(runner, project)

    cycle = await runner.start(db_session, company.id)

    assert cycle.stage_deadline == DAY_START + timedelta(minutes=5)
    await runner.tick(db_session, company.id)
    # the unreadable value is ignored, not fatal: EXECUTING keeps its default
    assert cycle.stage == S.EXECUTING
    assert cycle.stage_deadline == DAY_START + timedelta(minutes=DEFAULT_STAGE_MINUTES[S.EXECUTING])


# --- stage hooks ------------------------------------------------------------------------


async def test_each_stage_runs_its_hooks_on_the_way_in(db_session):
    """The CEO plans here (T-605), the ledger settles here (T-602). This is that seam."""
    company = await unique_company(db_session, "cycle")
    clock = FakeClock()
    runner = await _runner(clock)
    seen: list[tuple[str, int]] = []

    def record(stage):
        async def hook(session, cycle):
            seen.append((stage, cycle.seq))

        return hook

    for stage in (S.PLANNING, S.MEASURING, S.REVIEWING, S.DONE):
        runner.when_entering(stage, record(stage))

    await runner.start(db_session, company.id)
    await runner.tick(db_session, company.id)

    assert seen == [("PLANNING", 1), ("MEASURING", 1), ("REVIEWING", 1), ("DONE", 1)]


async def test_a_failing_hook_leaves_the_cycle_where_it_was(db_session):
    company = await unique_company(db_session, "cycle")
    clock = FakeClock()
    runner = await _runner(clock)
    runner.when_entering(S.MEASURING, _explode)

    cycle = await runner.start(db_session, company.id)
    with pytest.raises(RuntimeError):
        await runner.tick(db_session, company.id)
    await db_session.rollback()

    assert (await db_session.get(Cycle, cycle.id)) is None  # the whole tick rolled back


async def _explode(session, cycle):
    raise RuntimeError("the ledger is down")


class _Started:
    """Holds the workflow row a plan hook created, so a test can finish it later."""

    row: WorkflowRun


def _plans_one_workflow(runner: CycleRunner, project: Project) -> _Started:
    """Stand in for the CEO: planning starts one workflow, which holds EXECUTING open."""
    started = _Started()

    async def plan(session, cycle):
        started.row = WorkflowRun(
            company_id=cycle.company_id, project_id=project.id, cycle_id=cycle.id,
            template_name="t", state="RUNNING",
        )  # fmt: skip
        session.add(started.row)
        await session.flush()

    runner.when_entering(S.PLANNING, plan)
    return started


# --- the worker's job -------------------------------------------------------------------


async def test_the_maintenance_job_moves_every_active_company(db_session):
    clock = FakeClock()
    runner = await _runner(clock)
    first = await unique_company(db_session, "cycle-a")
    second = await unique_company(db_session, "cycle-b")
    archived = await unique_company(db_session, "cycle-c")
    archived.status = "archived"
    await db_session.flush()
    for company in (first, second, archived):
        await runner.start(db_session, company.id)

    await maintenance_job(runner)(db_session)

    stages = {row.company_id: row.stage for row in (await db_session.scalars(select(Cycle))).all()}
    assert stages[first.id] == S.DONE
    assert stages[second.id] == S.DONE
    assert stages[archived.id] == S.PLANNING  # an archived company is left alone


async def test_one_broken_company_does_not_hold_up_the_others(db_session):
    clock = FakeClock()
    runner = await _runner(clock)
    healthy = await unique_company(db_session, "cycle-ok")
    broken = await unique_company(db_session, "cycle-bad")
    await runner.start(db_session, healthy.id)
    await runner.start(db_session, broken.id)
    broken_id = broken.id

    async def only_for_the_broken_one(session, cycle):
        if cycle.company_id == broken_id:
            raise RuntimeError("this company's ledger is down")

    runner.when_entering(S.MEASURING, only_for_the_broken_one)
    await maintenance_job(runner)(db_session)

    stages = {row.company_id: row.stage for row in (await db_session.scalars(select(Cycle))).all()}
    assert stages[healthy.id] == S.DONE
    assert stages[broken_id] == S.PLANNING  # rolled back to the savepoint, retried next tick


async def test_the_job_can_be_limited_to_this_worker_s_companies(db_session):
    clock = FakeClock()
    runner = await _runner(clock)
    mine = await unique_company(db_session, "cycle-mine")
    theirs = await unique_company(db_session, "cycle-theirs")
    await runner.start(db_session, mine.id)
    await runner.start(db_session, theirs.id)

    await maintenance_job(runner, frozenset({mine.id}))(db_session)

    stages = {row.company_id: row.stage for row in (await db_session.scalars(select(Cycle))).all()}
    assert (stages[mine.id], stages[theirs.id]) == (S.DONE, S.PLANNING)


# --- the week ---------------------------------------------------------------------------


async def test_seven_cycles_in_a_row(db_session):
    """AC-14 in miniature: seven days, no hand-holding, every cycle DONE and numbered in order."""
    company = await unique_company(db_session, "cycle-week")
    clock = FakeClock()
    runner = await _runner(clock)
    schedule = type("Schedule", (), {"company_id": company.id})()

    for _ in range(7):
        await runner.schedule_handler()(db_session, schedule, clock.now)
        await maintenance_job(runner)(db_session)
        clock.advance(hours=24)

    cycles = (
        await db_session.scalars(
            select(Cycle).where(Cycle.company_id == company.id).order_by(Cycle.seq)
        )
    ).all()
    assert [c.seq for c in cycles] == [1, 2, 3, 4, 5, 6, 7]
    assert {c.stage for c in cycles} == {S.DONE}
    assert all(c.ended_at is not None and c.stage_deadline is None for c in cycles)
    assert await _events(db_session, company.id) == [
        step
        for _ in range(7)
        for step in (
            "CYCLE_STARTED",
            "CYCLE_STAGE_CHANGED",
            "CYCLE_STAGE_CHANGED",
            "CYCLE_STAGE_CHANGED",
            "CYCLE_STAGE_CHANGED",
            "CYCLE_COMPLETED",
        )
    ]


async def test_the_database_refuses_a_second_cycle_with_the_same_number(db_session):
    company = await unique_company(db_session, "cycle-dup")
    db_session.add_all(
        [
            Cycle(company_id=company.id, seq=1, stage="DONE", ended_at=DAY_START),
            Cycle(company_id=company.id, seq=1, stage="PLANNING"),
        ]
    )
    with pytest.raises(Exception, match="uq_cycles_company_id_seq"):
        await db_session.flush()
    await db_session.rollback()


async def test_a_cycle_is_done_exactly_when_it_has_an_end(db_session):
    company = await unique_company(db_session, "cycle-end")
    db_session.add(Cycle(company_id=company.id, seq=1, stage="DONE"))
    with pytest.raises(Exception, match="ended_at_iff_done"):
        await db_session.flush()
    await db_session.rollback()


async def test_an_unknown_company_has_no_cycle(db_session):
    runner = await _runner()
    assert await runner.tick(db_session, uuid.uuid4()) is None
    assert await runner.open_cycle(db_session, uuid.uuid4()) is None


async def test_the_company_of_a_cycle_must_exist(db_session):
    db_session.add(Cycle(company_id=uuid.uuid4(), seq=1, stage="PLANNING"))
    with pytest.raises(Exception, match="fk_cycles_company_id_companies"):
        await db_session.flush()
    await db_session.rollback()


async def test_a_task_may_only_point_at_a_real_cycle(db_session):
    """The deferred foreign key, now enforced: the audit chain cycle -> workflow -> task."""
    company = await unique_company(db_session, "cycle-fk")
    project = Project(company_id=company.id, name="p")
    db_session.add(project)
    await db_session.flush()
    db_session.add(
        Task(
            company_id=company.id,
            project_id=project.id,
            cycle_id=uuid.uuid4(),
            name="n",
            display_name="N",
            required_role="researcher",
            state="PENDING",
        )  # fmt: skip
    )
    with pytest.raises(Exception, match="fk_tasks_cycle_id_cycles"):
        await db_session.flush()
    await db_session.rollback()


async def test_archived_companies_keep_their_history(db_session):
    """A company that is archived stops cycling but its cycles stay readable."""
    company = await unique_company(db_session, "cycle-arch")
    runner = await _runner(FakeClock())
    cycle = await runner.start(db_session, company.id)
    company.status = "archived"
    await db_session.flush()

    assert await db_session.get(Cycle, cycle.id) is not None
    assert (await db_session.get(Company, company.id)).status == "archived"


async def test_a_company_gets_one_daily_schedule_and_only_one(db_session):
    """AC-11 starts here: after this, nobody has to press anything again."""
    company = await unique_company(db_session, "cycle-sched")

    first = await ensure_cycle_schedule(db_session, company.id, now=DAY_START)
    again = await ensure_cycle_schedule(db_session, company.id, now=DAY_START)

    assert first.id == again.id
    assert (first.name, first.handler, first.cron) == (
        CYCLE_START_SCHEDULE,
        CYCLE_START_SCHEDULE,
        CYCLE_START_CRON,
    )
    assert first.next_run_at == DAY_START + timedelta(days=1)  # 06:00 today has passed
    assert first.enabled


async def test_the_cycle_settles_its_own_costs_on_the_way_through(db_session):
    """T-602 meets T-601: MEASURING is where the day's model calls become money. The runner
    knows nothing about the ledger — it just enters the stage."""
    from decimal import Decimal

    from autora.company.ledger import MODEL_COST, Ledger
    from autora.db.models import ModelCall, Transaction

    company = await unique_company(db_session, "cycle-money")
    project = Project(company_id=company.id, name="p")
    db_session.add(project)
    await db_session.flush()
    clock = FakeClock()
    runner = await _runner(clock)
    ledger = Ledger(clock=lambda: clock.now)
    runner.when_entering(S.MEASURING, ledger.stage_hook())
    cycle = await runner.start(db_session, company.id)
    db_session.add(
        ModelCall(
            company_id=company.id,
            project_id=project.id,
            cycle_id=cycle.id,
            role="writer",
            capability="drafting",
            alias="frontier",
            provider="nvidia",
            model_id="m",
            status="ok",
            cost_usd=Decimal("0.42"),
        )  # fmt: skip
    )
    await db_session.flush()

    await runner.tick(db_session, company.id)

    assert cycle.stage == S.DONE
    expense = await db_session.scalar(
        select(Transaction).where(Transaction.company_id == company.id)
    )
    assert (expense.category, expense.amount) == (MODEL_COST, Decimal("13.440000"))  # TWD
    assert (expense.source_amount, expense.source_currency) == (Decimal("0.420000"), "USD")
    assert expense.ref_id == cycle.id
