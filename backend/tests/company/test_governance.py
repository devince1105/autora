"""T-606: the rules that run without asking anybody.

What matters here is that these hold when the CEO does not: they read numbers that already
exist, they never call a model, and a project whose own stated criteria are breached stops
whether or not anyone agrees.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from autora.app import build_policy_engine
from autora.company import verbs
from autora.company.agents.roster import hire_agent
from autora.company.commands import CommandBus
from autora.company.governance import (
    FAILURES_BEFORE_PAUSE,
    WARN_AT,
    Governance,
)
from autora.company.organization import add_business_unit, bootstrap_executive, role_by_key
from autora.company.reporting import Reporting
from autora.db.models import (
    AgentRun,
    AgentStatus,
    Budget,
    BusinessUnit,
    BusinessUnitState,
    CommandRecord,
    Cycle,
    EventRecord,
    KpiScope,
    KpiSnapshot,
    ModelCall,
    Project,
    ProjectState,
    Task,
)
from autora.runtime.actor import Actor
from autora.runtime.approvals import ApprovalService
from autora.runtime.task_manager import TaskManager
from tests.conftest import unique_company

HUMAN = Actor.human("founder")
START = datetime(2026, 9, 21, 6, 0, tzinfo=UTC)
END = START + timedelta(hours=14)

PAUSE_IF = {"metric": "newsroom.cost_per_published_article", "op": ">", "value": 3.0}


def _governance() -> Governance:
    bus = CommandBus(policy=build_policy_engine(), approvals=ApprovalService(TaskManager()))
    verbs.register(bus)
    bus.install()
    return Governance(commands=bus)


async def _company(db_session, *, seq: int = 1, unit_criteria=None, project_criteria=None):
    company = await unique_company(db_session, "gov")
    unit = await add_business_unit(
        db_session, company_id=company.id, key="ai_media", name="AI Media",
        actor=HUMAN, state=BusinessUnitState.ACTIVE, kill_criteria=unit_criteria,
    )  # fmt: skip
    project = Project(
        company_id=company.id,
        business_unit_id=unit.id,
        name="Lumen Daily",
        state=ProjectState.ACTIVE.value,
        kill_criteria=project_criteria if project_criteria is not None else {"note": "none"},
    )
    # a cycle in REVIEWING has not ended: the database enforces "ended_at iff DONE" (T-601)
    cycle = Cycle(company_id=company.id, seq=seq, stage="REVIEWING", started_at=START)
    db_session.add_all([project, cycle])
    await db_session.flush()
    return company, unit, project, cycle


async def _measured(db_session, company, cycle, *, project=None, unit=None, **metrics):
    """A KPI snapshot with the numbers a rule will read."""
    snapshot = KpiSnapshot(
        company_id=company.id,
        cycle_id=cycle.id,
        project_id=project.id if project else None,
        business_unit_id=unit.id if unit else None,
        scope=(KpiScope.PROJECT if project else KpiScope.BUSINESS_UNIT).value
        if (project or unit)
        else KpiScope.COMPANY.value,
        metrics=metrics,
        period_start=cycle.started_at,
        period_end=cycle.ended_at or END,
    )
    db_session.add(snapshot)
    await db_session.flush()
    return snapshot


# --- 1. kill criteria ----------------------------------------------------------------------------


async def test_a_project_that_breaks_its_own_criteria_is_paused(db_session):
    """The number to beat was chosen before anyone knew whether it would be met."""
    company, unit, project, cycle = await _company(
        db_session, project_criteria={"auto_pause_if": PAUSE_IF}
    )
    await _measured(
        db_session, company, cycle, project=project,
        **{"newsroom.cost_per_published_article": "4.50"},
    )  # fmt: skip

    done = await _governance().review(db_session, cycle)

    assert done.paused_projects == [project.id]
    assert (await db_session.get(Project, project.id)).state == ProjectState.PAUSED.value
    (breach,) = done.breaches
    assert "4.5" in str(breach) and "> 3" in str(breach)
    # and it is recorded like any other pause, with the rule as its reason
    record = await db_session.scalar(
        select(CommandRecord).where(
            CommandRecord.company_id == company.id, CommandRecord.command == "PauseProject"
        )
    )
    assert record.outcome == "done" and record.actor["id"] == "governance"
    event = await db_session.scalar(
        select(EventRecord).where(
            EventRecord.company_id == company.id, EventRecord.event_type == "PROJECT_PAUSED"
        )
    )
    assert event.payload["trigger"] == "kill_criteria"
    assert "cost_per_published_article" in event.payload["reason"]


async def test_a_project_inside_its_criteria_is_left_alone(db_session):
    company, unit, project, cycle = await _company(
        db_session, project_criteria={"auto_pause_if": PAUSE_IF}
    )
    await _measured(
        db_session, company, cycle, project=project,
        **{"newsroom.cost_per_published_article": "1.20"},
    )  # fmt: skip

    done = await _governance().review(db_session, cycle)

    assert not done.paused_projects
    assert (await db_session.get(Project, project.id)).state == ProjectState.ACTIVE.value


async def test_a_metric_nobody_measured_is_not_a_breach(db_session):
    """Silence is not evidence: a criterion on a number that was never computed does nothing."""
    company, unit, project, cycle = await _company(
        db_session, project_criteria={"auto_pause_if": PAUSE_IF}
    )
    await _measured(db_session, company, cycle, project=project, cost_usd="9.99")

    done = await _governance().review(db_session, cycle)

    assert not done.paused_projects and not done.breaches


async def test_a_project_is_given_the_cycles_it_was_promised(db_session):
    """``evaluate_after_cycles``: judged only once it has had its run."""
    company, unit, project, cycle = await _company(
        db_session,
        seq=3,
        project_criteria={"evaluate_after_cycles": 7, "auto_pause_if": PAUSE_IF},
    )
    await _measured(
        db_session, company, cycle, project=project,
        **{"newsroom.cost_per_published_article": "99"},
    )  # fmt: skip

    done = await _governance().review(db_session, cycle)

    assert not done.paused_projects  # cycle 3 of the 7 it was given


async def test_a_criterion_that_needs_several_cycles_needs_all_of_them(db_session):
    company, unit, project, cycle = await _company(
        db_session,
        seq=2,
        project_criteria={
            "auto_pause_if": {**PAUSE_IF, "consecutive_cycles": 2},
        },
    )
    earlier = Cycle(
        company_id=company.id,
        seq=1,
        stage="DONE",
        started_at=START - timedelta(days=1),
        ended_at=END - timedelta(days=1),
    )
    db_session.add(earlier)
    await db_session.flush()
    # only the latest cycle is over the line
    await _measured(
        db_session, company, earlier, project=project,
        **{"newsroom.cost_per_published_article": "1.00"},
    )  # fmt: skip
    await _measured(
        db_session, company, cycle, project=project,
        **{"newsroom.cost_per_published_article": "5.00"},
    )  # fmt: skip

    assert not (await _governance().review(db_session, cycle)).paused_projects

    # now both are
    snapshot = await db_session.scalar(
        select(KpiSnapshot).where(KpiSnapshot.cycle_id == earlier.id)
    )
    snapshot.metrics = {"newsroom.cost_per_published_article": "6.00"}
    await db_session.flush()

    done = await _governance().review(db_session, cycle)

    assert done.paused_projects == [project.id]
    assert done.breaches[0].cycles == 2


async def test_an_unreadable_criterion_stops_nothing(db_session):
    """A typo in a rule must not pause a business; it is logged and skipped."""
    company, unit, project, cycle = await _company(
        db_session, project_criteria={"auto_pause_if": {"metric": "x", "op": "≫", "value": 1}}
    )
    await _measured(db_session, company, cycle, project=project, x="99")

    done = await _governance().review(db_session, cycle)

    assert not done.paused_projects


async def test_a_business_that_breaks_its_criteria_takes_its_projects_with_it(db_session):
    """A paused business is not one that keeps spending."""
    company, unit, project, cycle = await _company(
        db_session, unit_criteria={"auto_pause_if": PAUSE_IF}
    )
    await _measured(
        db_session, company, cycle, unit=unit,
        **{"newsroom.cost_per_published_article": "8"},
    )  # fmt: skip

    done = await _governance().review(db_session, cycle)

    assert done.paused_units == [unit.id]
    assert done.paused_projects == [project.id]
    assert (await db_session.get(BusinessUnit, unit.id)).state == BusinessUnitState.PAUSED.value
    event = await db_session.scalar(
        select(EventRecord).where(
            EventRecord.company_id == company.id,
            EventRecord.event_type == "BUSINESS_UNIT_PAUSED",
        )
    )
    assert event.payload["trigger"] == "kill_criteria"


async def test_governance_pauses_but_never_kills(db_session):
    """Only a person ends a project; a rule that fires on a bad week must not close one."""
    company, unit, project, cycle = await _company(
        db_session, project_criteria={"auto_pause_if": PAUSE_IF}
    )
    await _measured(
        db_session, company, cycle, project=project,
        **{"newsroom.cost_per_published_article": "99"},
    )  # fmt: skip

    await _governance().review(db_session, cycle)

    assert (await db_session.get(Project, project.id)).state == ProjectState.PAUSED.value
    killed = await db_session.scalar(
        select(CommandRecord).where(
            CommandRecord.company_id == company.id, CommandRecord.command == "KillProject"
        )
    )
    assert killed is None


async def test_running_the_rules_twice_pauses_once(db_session):
    """MEASURING and REVIEWING can both be entered again after a restart."""
    company, unit, project, cycle = await _company(
        db_session, project_criteria={"auto_pause_if": PAUSE_IF}
    )
    await _measured(
        db_session, company, cycle, project=project,
        **{"newsroom.cost_per_published_article": "4"},
    )  # fmt: skip
    governance = _governance()

    first = await governance.review(db_session, cycle)
    again = await governance.review(db_session, cycle)

    assert first.paused_projects == [project.id]
    assert not again.paused_projects  # already paused; nothing left to do
    records = (
        await db_session.scalars(
            select(CommandRecord).where(
                CommandRecord.company_id == company.id,
                CommandRecord.command == "PauseProject",
            )
        )
    ).all()
    assert len(records) == 1


# --- 2. an agent that keeps failing -----------------------------------------------------------


async def _agent_with_runs(db_session, company, project, *states):
    position = await role_by_key(db_session, company.id, "ceo")
    agent = await hire_agent(
        db_session, company_id=company.id, role="ceo", display_name=f"A{uuid.uuid4().hex[:6]}",
        actor=HUMAN, position=position,
    )  # fmt: skip
    for n, state in enumerate(states):
        task = Task(
            company_id=company.id, project_id=project.id, name="plan", display_name="Plan",
            required_role="ceo", state="READY",
        )  # fmt: skip
        db_session.add(task)
        await db_session.flush()
        db_session.add(
            AgentRun(
                company_id=company.id,
                agent_id=agent.id,
                task_id=task.id,
                attempt=1,
                state=state,
                created_at=START + timedelta(minutes=n),
                started_at=START + timedelta(minutes=n),
                # the database enforces "finished_at iff terminal"
                finished_at=START + timedelta(minutes=n, seconds=30),
            )
        )
    await db_session.flush()
    return agent


async def test_an_agent_that_keeps_failing_stops_being_given_work(db_session):
    company, unit, project, cycle = await _company(db_session)
    await bootstrap_executive(db_session, company.id, actor=HUMAN)
    agent = await _agent_with_runs(db_session, company, project, *(["FAILED"] * 3))

    done = await _governance().review(db_session, cycle)

    assert done.paused_agents == [agent.id]
    assert (await db_session.get(type(agent), agent.id)).status == AgentStatus.PAUSED.value
    record = await db_session.scalar(
        select(CommandRecord).where(
            CommandRecord.company_id == company.id, CommandRecord.command == "PauseAgent"
        )
    )
    assert record.outcome == "done"
    assert f"{FAILURES_BEFORE_PAUSE} runs in a row failed" in record.payload["reason"]


async def test_a_failure_streak_broken_by_a_success_does_not_count(db_session):
    company, unit, project, cycle = await _company(db_session)
    await bootstrap_executive(db_session, company.id, actor=HUMAN)
    agent = await _agent_with_runs(db_session, company, project, "FAILED", "COMPLETED", "FAILED")

    done = await _governance().review(db_session, cycle)

    assert not done.paused_agents
    assert (await db_session.get(type(agent), agent.id)).status == AgentStatus.ACTIVE.value


async def test_a_new_agent_with_too_few_runs_is_not_judged(db_session):
    company, unit, project, cycle = await _company(db_session)
    await bootstrap_executive(db_session, company.id, actor=HUMAN)
    await _agent_with_runs(db_session, company, project, "FAILED", "FAILED")

    assert not (await _governance().review(db_session, cycle)).paused_agents


# --- 3. spending near a cap -------------------------------------------------------------------


async def test_a_budget_most_of_the_way_spent_is_announced(db_session):
    company, unit, project, cycle = await _company(db_session)
    db_session.add(
        Budget(company_id=company.id, project_id=project.id, period="cycle", amount=Decimal("10"))
    )
    await _measured(db_session, company, cycle, project=project, cost_usd="8.50")

    done = await _governance().review(db_session, cycle)

    assert done.warnings and "85%" in done.warnings[0]
    event = await db_session.scalar(
        select(EventRecord).where(
            EventRecord.company_id == company.id, EventRecord.event_type == "BUDGET_WARNING"
        )
    )
    assert event.payload["ratio"] >= WARN_AT
    assert Decimal(str(event.payload["cap"])) == Decimal("10")


async def test_a_budget_barely_touched_says_nothing(db_session):
    company, unit, project, cycle = await _company(db_session)
    db_session.add(
        Budget(company_id=company.id, project_id=project.id, period="cycle", amount=Decimal("10"))
    )
    await _measured(db_session, company, cycle, project=project, cost_usd="1.00")

    assert not (await _governance().review(db_session, cycle)).warnings


async def test_a_budget_already_at_the_wall_is_the_guard_s_business_not_this_one(db_session):
    """At 100% the cost guard refuses the call; repeating it here would be noise."""
    company, unit, project, cycle = await _company(db_session)
    db_session.add(
        Budget(company_id=company.id, project_id=project.id, period="cycle", amount=Decimal("10"))
    )
    await _measured(db_session, company, cycle, project=project, cost_usd="10.00")

    assert not (await _governance().review(db_session, cycle)).warnings


# --- all of it together -----------------------------------------------------------------------


async def test_nothing_wrong_means_nothing_happens(db_session):
    company, unit, project, cycle = await _company(db_session)

    done = await _governance().review(db_session, cycle)

    assert not done
    assert (await db_session.get(Project, project.id)).state == ProjectState.ACTIVE.value


async def test_the_stage_hook_runs_the_rules(db_session):
    company, unit, project, cycle = await _company(
        db_session, project_criteria={"auto_pause_if": PAUSE_IF}
    )
    await _measured(
        db_session, company, cycle, project=project,
        **{"newsroom.cost_per_published_article": "7"},
    )  # fmt: skip

    await _governance().stage_hook()(db_session, cycle)

    assert (await db_session.get(Project, project.id)).state == ProjectState.PAUSED.value


async def test_the_rules_never_call_a_model(db_session):
    """Deterministic by construction: they hold when the CEO is slow, wrong or absent."""
    company, unit, project, cycle = await _company(
        db_session, project_criteria={"auto_pause_if": PAUSE_IF}
    )
    await _measured(
        db_session, company, cycle, project=project,
        **{"newsroom.cost_per_published_article": "7"},
    )  # fmt: skip

    await _governance().review(db_session, cycle)

    calls = await db_session.scalar(select(ModelCall.id).where(ModelCall.company_id == company.id))
    assert calls is None


async def test_reporting_and_governance_agree_on_the_numbers(db_session):
    """Governance reads what Reporting wrote: one measurement, one decision."""
    company, unit, project, cycle = await _company(
        db_session,
        project_criteria={"auto_pause_if": {"metric": "cost_usd", "op": ">", "value": 1}},
    )
    db_session.add(
        ModelCall(
            company_id=company.id,
            project_id=project.id,
            role="writer",
            capability="drafting",
            alias="frontier",
            provider="nvidia",
            model_id="m",
            status="ok",
            cost_usd=Decimal("2.50"),
            created_at=START + timedelta(hours=1),
        )  # fmt: skip
    )
    await db_session.flush()
    await Reporting(clock=lambda: END).measure_cycle(db_session, cycle)

    done = await _governance().review(db_session, cycle)

    assert done.paused_projects == [project.id]
    assert done.breaches[0].observed == 2.5
