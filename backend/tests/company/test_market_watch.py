"""T-706: the business agent writes down what might be a business, and decides nothing.

What is held here:
- the policy lets the role record opportunities and signals and read the web, and nothing else:
  scoring, advancing, proposing, funding and opening stay with the CEO and a person;
- what it writes is checked when it is written: a company figure is the report's, a page is one
  this run captured, an agent is not a person, three new opportunities a look at most, and a
  decided opportunity stays decided;
- its report is exactly what its commands wrote, and silence needs a reason;
- it looks once a week, inside EXECUTING, on its own project — or not at all without an agent.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from autora.app import RESEARCH_TOOLS, build_behaviors, build_policy_engine
from autora.company import opportunities as opportunities_service
from autora.company import simulation as company_simulation
from autora.company import verbs, verbs_business
from autora.company.agents.business import (
    ROLE,
    WATCH_MARKET,
    MarketWatch,
    behaviors,
    reported_what_it_wrote,
    silence_has_a_reason,
)
from autora.company.agents.roster import hire_agent
from autora.company.commands import CommandBus
from autora.company.market_watch import (
    PROJECT,
    TEMPLATE,
    MarketWatchDesk,
    market_watch_project,
)
from autora.company.market_watch import register_templates as register_watch_templates
from autora.company.organization import (
    BUSINESS_ROLE,
    add_business_unit,
    bootstrap_executive,
    role_by_key,
)
from autora.db.models import (
    AgentRun,
    BusinessUnitState,
    CommandOutcome,
    Cycle,
    CycleStage,
    KpiScope,
    KpiSnapshot,
    Opportunity,
    OpportunitySignal,
    OpportunityState,
    ProjectState,
    Task,
    WorkflowRun,
    WorkflowRunState,
)
from autora.runtime.actor import Actor
from autora.runtime.approvals import ApprovalService
from autora.runtime.behaviors import RunContext
from autora.runtime.dag import TemplateRegistry, WorkflowEngine
from autora.runtime.events import catalog as ev
from autora.runtime.events.outbox import emit
from autora.runtime.events.schema import new_event
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
    company = await unique_company(session, "market")
    await bootstrap_executive(session, company.id, actor=HUMAN)
    unit = await add_business_unit(
        session, company_id=company.id, key="ai_media", name="AI Media",
        actor=HUMAN, state=BusinessUnitState.ACTIVE,
    )  # fmt: skip
    agent = None
    if hire:
        agent = await hire_agent(
            session, company_id=company.id, role=ROLE, display_name="Bea", actor=HUMAN,
            position=await role_by_key(session, company.id, BUSINESS_ROLE),
        )  # fmt: skip
    return company, unit, agent


async def _run(session, company, agent, *, finished=False) -> RunContext:
    """A look in progress, as the runner would have it (one open run per agent)."""
    project = await market_watch_project(session, company.id, actor=HUMAN)
    task = Task(
        company_id=company.id, project_id=project.id, name=WATCH_MARKET,
        display_name="Watch the market", required_role=ROLE, state="READY",
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


async def _captured(session, ctx: RunContext) -> uuid.UUID:
    """A page this run's own capture tool produced, as the runner records it."""
    captured = uuid.uuid4()
    await emit(
        session,
        new_event(
            ev.ToolCompleted(
                tool="fetch_url", tool_call_id=f"c-{captured.hex[:6]}", duration_ms=10,
                produced=[ev.ProducedRef(type="evidence", id=captured)],
            ),
            company_id=ctx.company_id, actor=Actor.agent(ctx.agent.id),
            aggregate_type="agent_run", aggregate_id=ctx.run_id, agent_id=ctx.agent.id,
            run_id=ctx.run_id, task_id=ctx.task.id,
        ),
    )  # fmt: skip
    return captured


async def _submit(session, bus, company, command, payload, *, agent=None, ctx=None):
    return await bus.submit(
        session, command, payload, company_id=company.id,
        actor=Actor.agent(agent.id) if agent else HUMAN, role=ROLE if agent else None,
        idempotency_key=f"k-{uuid.uuid4().hex[:8]}", run_id=ctx.run_id if ctx else None,
    )  # fmt: skip


def _discovery(key="ai_tutoring", *signals) -> dict:
    return {
        "key": key,
        "title": "AI tutoring for exam season",
        "thesis": "Readers who pay for membership ask about exam preparation every autumn.",
        "signals": list(signals) or [_company_signal()],
    }


def _company_signal(metric="members", value="3", **extra) -> dict:
    return {"source": "company", "summary": "Paying members", "metric": metric, "value": value,
            **extra}  # fmt: skip


async def _count(session, model, company) -> int:
    return await session.scalar(
        select(func.count()).select_from(model).where(model.company_id == company.id)
    )


# --- what it may do at all ------------------------------------------------------------------


def test_it_may_write_things_down_and_read_the_web_and_nothing_else():
    """Every action the policy knows, asked for the business role: what it may do alone is
    exactly noticing. Everything that decides something is somebody else's."""
    engine = build_policy_engine()
    business = Actor.agent(uuid.uuid4())
    alone = {
        action
        for action in engine._actions  # noqa: SLF001 - the point is to ask about all of them
        if engine.decide(business, action, role=ROLE).outcome == "allow"
    }
    assert alone == {
        "submit_command",
        "discover_opportunity",
        "record_opportunity_signal",
        *RESEARCH_TOOLS,
    }


async def test_it_cannot_score_or_advance_what_it_found(db_session):
    company, unit, agent = await _world(db_session)
    ctx = await _run(db_session, company, agent)
    bus = _bus()
    await _report(db_session, company, {"members": "3"})
    found = await _submit(db_session, bus, company, "DiscoverOpportunity", _discovery(),
                          agent=agent, ctx=ctx)  # fmt: skip
    assert found.record.outcome == CommandOutcome.DONE.value
    opportunity_id = found.record.result["opportunity_id"]
    for command, payload in (
        ("ScoreOpportunity", {"opportunity_id": opportunity_id, "score": "9"}),
        ("AdvanceOpportunity", {"opportunity_id": opportunity_id, "to_state": "EVALUATING"}),
        ("RejectOpportunity", {"opportunity_id": opportunity_id, "reason": "not for us"}),
    ):
        refused = await _submit(db_session, bus, company, command, payload, agent=agent, ctx=ctx)
        assert refused.record.outcome == CommandOutcome.REFUSED.value, command
    opportunity = await db_session.get(Opportunity, uuid.UUID(opportunity_id))
    assert (opportunity.state, opportunity.score) == (OpportunityState.DISCOVERED.value, None)


def test_its_eyes_are_the_ones_the_composition_root_lends_it():
    """Core does not know which domain can search the web: with no tools lent it works from
    the company's numbers, and the assembled app lends it the newsroom's."""
    [alone] = behaviors()
    assert alone.tools == ("submit_command",)
    assert "no tools to look outside" in alone.system_prompt
    assert "captured" not in alone.system_prompt

    assembled = build_behaviors().resolve(ROLE, WATCH_MARKET)
    assert assembled.tools == ("submit_command", *RESEARCH_TOOLS)
    assert "fetch_url" in assembled.system_prompt
    assert "You can only write things down" in assembled.system_prompt


# --- what it writes is checked when it is written -----------------------------------------------


async def test_it_writes_down_an_opportunity_from_the_companys_own_numbers(db_session):
    company, unit, agent = await _world(db_session)
    ctx = await _run(db_session, company, agent)
    await _report(db_session, company, {"members": "3", "churned_customers": "2"})
    found = await _submit(
        db_session, _bus(), company, "DiscoverOpportunity",
        _discovery("ai_tutoring", _company_signal(), _company_signal("churned_customers", "2.0")),
        agent=agent, ctx=ctx,
    )  # fmt: skip
    assert found.record.outcome == CommandOutcome.DONE.value, found.record.reason
    opportunity = await db_session.get(
        Opportunity, uuid.UUID(found.record.result["opportunity_id"])
    )
    assert (opportunity.state, opportunity.discovered_by_run_id) == (
        OpportunityState.DISCOVERED.value, ctx.run_id,
    )  # fmt: skip
    signals = (
        await db_session.scalars(
            select(OpportunitySignal).where(OpportunitySignal.opportunity_id == opportunity.id)
        )
    ).all()
    assert sorted(s.metric for s in signals) == ["churned_customers", "members"]
    assert {s.recorded_by_run_id for s in signals} == {ctx.run_id}
    assert sorted(found.record.result["signal_ids"]) == sorted(str(s.id) for s in signals)


async def test_a_figure_the_report_does_not_hold_writes_nothing_at_all(db_session):
    company, unit, agent = await _world(db_session)
    ctx = await _run(db_session, company, agent)
    await _report(db_session, company, {"members": "3"})
    bus = _bus()

    thirty = _discovery("a", _company_signal(value="30"))
    wrong = await _submit(db_session, bus, company, "DiscoverOpportunity", thirty,
                          agent=agent, ctx=ctx)  # fmt: skip
    assert wrong.record.outcome == CommandOutcome.REFUSED.value
    assert "members is 3 in the report, not 30" in wrong.record.reason

    # one good signal and one invented: the discovery is refused whole, not written in half
    half = await _submit(
        db_session, bus, company, "DiscoverOpportunity",
        _discovery("b", _company_signal(), _company_signal("market_size", "2000000000")),
        agent=agent, ctx=ctx,
    )  # fmt: skip
    assert half.record.outcome == CommandOutcome.REFUSED.value
    assert "market_size is not in the company report" in half.record.reason

    bare = await _submit(db_session, bus, company, "DiscoverOpportunity",
                         _discovery("c", {"source": "company", "summary": "members grew"}),
                         agent=agent, ctx=ctx)  # fmt: skip
    assert bare.record.outcome == CommandOutcome.REFUSED.value
    assert "cites a metric and its value" in bare.record.reason
    assert await _count(db_session, Opportunity, company) == 0
    assert await _count(db_session, OpportunitySignal, company) == 0


async def test_a_business_figure_is_checked_against_that_business(db_session):
    company, unit, agent = await _world(db_session)
    ctx = await _run(db_session, company, agent)
    bus = _bus()
    signal = _company_signal("revenue", "0", business_unit_id=str(unit.id))

    unmeasured = await _submit(db_session, bus, company, "DiscoverOpportunity",
                               _discovery("a", signal), agent=agent, ctx=ctx)  # fmt: skip
    assert "there is no report for that business_unit" in unmeasured.record.reason

    await _report(db_session, company, {"revenue": "0.000000"}, unit=unit)
    measured = await _submit(db_session, bus, company, "DiscoverOpportunity",
                             _discovery("b", signal), agent=agent, ctx=ctx)  # fmt: skip
    assert measured.record.outcome == CommandOutcome.DONE.value, measured.record.reason


async def test_a_page_counts_only_if_this_run_captured_it(db_session):
    company, unit, agent = await _world(db_session)
    earlier = await _run(db_session, company, agent, finished=True)
    elsewhere = await _captured(db_session, earlier)
    ctx = await _run(db_session, company, agent)
    captured = await _captured(db_session, ctx)
    bus = _bus()

    def web(ref):
        return {"source": "web", "summary": "Three tutoring apps, all above NT$600 a month",
                "evidence_ref": ref}  # fmt: skip

    cited = await _submit(db_session, bus, company, "DiscoverOpportunity",
                          _discovery("a", web(str(captured))), agent=agent, ctx=ctx)  # fmt: skip
    assert cited.record.outcome == CommandOutcome.DONE.value, cited.record.reason
    stored = await db_session.scalar(
        select(OpportunitySignal.evidence_ref).where(
            OpportunitySignal.id == uuid.UUID(cited.record.result["signal_ids"][0])
        )
    )
    assert stored == f"evidence:{captured}"

    for ref, why in (
        (str(elsewhere), "was not captured in this run"),  # another run's page
        (str(uuid.uuid4()), "was not captured in this run"),  # nobody's page
        ("https://example.com/tutoring", "cites the id of a page captured in this run"),
        (None, "cites the id of a page captured in this run"),
    ):
        refused = await _submit(db_session, bus, company, "DiscoverOpportunity",
                                _discovery(f"k_{uuid.uuid4().hex[:6]}", web(ref)),
                                agent=agent, ctx=ctx)  # fmt: skip
        assert refused.record.outcome == CommandOutcome.REFUSED.value, ref
        assert why in refused.record.reason, (ref, refused.record.reason)


async def test_an_agent_is_not_a_person_but_a_person_is(db_session):
    company, unit, agent = await _world(db_session)
    ctx = await _run(db_session, company, agent)
    bus = _bus()
    heard = {"source": "person", "summary": "A reader asked for exam prep"}

    pretending = await _submit(db_session, bus, company, "DiscoverOpportunity",
                               _discovery("a", heard), agent=agent, ctx=ctx)  # fmt: skip
    assert pretending.record.outcome == CommandOutcome.REFUSED.value
    assert "not a person's" in pretending.record.reason

    # a person answers for their own observations: a link, a conversation, unchecked
    link = {"source": "web", "summary": "Seen it", "evidence_ref": "https://example.com/x"}
    person = await _submit(db_session, bus, company, "DiscoverOpportunity",
                           _discovery("b", heard, link))  # fmt: skip
    assert person.record.outcome == CommandOutcome.DONE.value, person.record.reason


async def test_one_look_adds_three_new_opportunities_at_most(db_session):
    company, unit, agent = await _world(db_session)
    ctx = await _run(db_session, company, agent)
    await _report(db_session, company, {"members": "3"})
    bus = _bus()
    outcomes = [
        (
            await _submit(
                db_session,
                bus,
                company,
                "DiscoverOpportunity",
                _discovery(f"idea_{n}"),
                agent=agent,
                ctx=ctx,
            )
        ).record  # fmt: skip
        for n in range(4)
    ]
    assert [r.outcome for r in outcomes] == ["done", "done", "done", "refused"]
    assert "3 is the most one look at the market may add" in outcomes[3].reason
    # a person is not held to it, and neither is the next look
    person = await _submit(db_session, bus, company, "DiscoverOpportunity", _discovery("mine"))
    assert person.record.outcome == CommandOutcome.DONE.value


async def test_a_decided_opportunity_stays_decided(db_session):
    company, unit, agent = await _world(db_session)
    ctx = await _run(db_session, company, agent)
    await _report(db_session, company, {"members": "3"})
    bus = _bus()
    found = await _submit(db_session, bus, company, "DiscoverOpportunity", _discovery("tutoring"),
                          agent=agent, ctx=ctx)  # fmt: skip
    opportunity_id = found.record.result["opportunity_id"]
    no = {"opportunity_id": opportunity_id, "reason": "acquisition cost"}
    rejected = await _submit(db_session, bus, company, "RejectOpportunity", no)
    assert rejected.record.outcome == CommandOutcome.DONE.value

    more = await _submit(db_session, bus, company, "RecordOpportunitySignal",
                         {"opportunity_id": opportunity_id, "signal": _company_signal()},
                         agent=agent, ctx=ctx)  # fmt: skip
    assert "it was decided already" in more.record.reason
    again = await _submit(db_session, bus, company, "DiscoverOpportunity", _discovery("tutoring"),
                          agent=agent, ctx=ctx)  # fmt: skip
    assert "it may have been rejected" in again.record.reason


# --- its report is what it wrote -------------------------------------------------------------


def _watch(*findings, nothing_new_because=None) -> MarketWatch:
    return MarketWatch.model_validate(
        {
            "summary": "Looked at tutoring and the members' numbers.",
            "findings": list(findings),
            "nothing_new_because": nothing_new_because,
        }  # fmt: skip
    )


def _finding(opportunity_id, signal_ids, *, new) -> dict:
    return {"opportunity_id": str(opportunity_id), "new": new,
            "signal_ids": [str(s) for s in signal_ids],
            "why_it_matters": "Members ask for it."}  # fmt: skip


async def test_its_report_is_exactly_what_its_commands_wrote(db_session):
    company, unit, agent = await _world(db_session)
    await _report(db_session, company, {"members": "3"})
    bus = _bus()
    already = await opportunities_service.discover(
        db_session, company_id=company.id, key="courses", title="Courses", actor=HUMAN
    )
    ctx = await _run(db_session, company, agent)
    found = (await _submit(db_session, bus, company, "DiscoverOpportunity", _discovery("tutoring"),
                           agent=agent, ctx=ctx)).record.result  # fmt: skip
    added = (await _submit(db_session, bus, company, "RecordOpportunitySignal",
                           {"opportunity_id": str(already.id), "signal": _company_signal()},
                           agent=agent, ctx=ctx)).record.result  # fmt: skip
    new_id, new_signals = found["opportunity_id"], found["signal_ids"]
    old_signals = added["signal_ids"]

    honest = _watch(_finding(new_id, new_signals, new=True),
                    _finding(already.id, old_signals, new=False))  # fmt: skip
    assert await reported_what_it_wrote(db_session, ctx, honest) == []

    left_out = await reported_what_it_wrote(
        db_session, ctx, _watch(_finding(new_id, new_signals, new=True), nothing_new_because="x")
    )
    assert f"it wrote about {already.id} and does not report it" in " ".join(left_out)

    as_new = await reported_what_it_wrote(
        db_session,
        ctx,
        _watch(
            _finding(new_id, new_signals, new=True), _finding(already.id, old_signals, new=True)
        ),  # fmt: skip
    )
    assert "was already open, not discovered" in " ".join(as_new)

    wrong_signals = await reported_what_it_wrote(
        db_session,
        ctx,
        _watch(
            _finding(new_id, [uuid.uuid4()], new=True), _finding(already.id, old_signals, new=False)
        ),  # fmt: skip
    )
    assert "are not the ones written" in " ".join(wrong_signals)

    invented = await reported_what_it_wrote(
        db_session,
        ctx,
        _watch(
            _finding(new_id, new_signals, new=True),
            _finding(already.id, old_signals, new=False),
            _finding(uuid.uuid4(), [uuid.uuid4()], new=True),
        ),  # fmt: skip
    )
    assert "which this run wrote nothing about" in " ".join(invented)


async def test_a_look_that_found_nothing_says_why(db_session):
    company, unit, agent = await _world(db_session)
    ctx = await _run(db_session, company, agent)
    assert "does not say why" in " ".join(await silence_has_a_reason(db_session, ctx, _watch()))
    quiet = _watch(nothing_new_because="Every lead was a competitor we already track.")
    assert await silence_has_a_reason(db_session, ctx, quiet) == []
    assert await reported_what_it_wrote(db_session, ctx, quiet) == []


# --- when it looks --------------------------------------------------------------------------


def _desk() -> MarketWatchDesk:
    templates = TemplateRegistry()
    register_watch_templates(templates)
    return MarketWatchDesk(WorkflowEngine(TaskManager(), templates))


async def _cycle(session, company, seq, started_at) -> Cycle:
    cycle = Cycle(
        company_id=company.id, seq=seq, stage=CycleStage.EXECUTING.value, started_at=started_at
    )
    session.add(cycle)
    await session.flush()
    return cycle


async def _looks(session, company) -> list[WorkflowRun]:
    return list(
        (
            await session.scalars(
                select(WorkflowRun)
                .where(WorkflowRun.company_id == company.id, WorkflowRun.template_name == TEMPLATE)
                .order_by(WorkflowRun.created_at)
            )
        ).all()
    )


async def test_it_looks_once_a_week_on_its_own_project(db_session):
    """Workflow runs are stamped by the database clock, so the cycles are placed around now."""
    company, unit, agent = await _world(db_session)
    desk = _desk()
    now = datetime.now(UTC)
    first = await _cycle(db_session, company, 1, now)
    await desk.stage_hook()(db_session, first)
    await desk.stage_hook()(db_session, first)  # the stage entered twice: still one look
    [look] = await _looks(db_session, company)
    task = await db_session.scalar(select(Task).where(Task.workflow_run_id == look.id))
    assert (task.name, task.required_role) == (WATCH_MARKET, ROLE)

    project = await market_watch_project(db_session, company.id, actor=HUMAN)
    assert (look.project_id, project.name) == (project.id, PROJECT)
    assert project.kill_criteria["auto_pause_if"]["metric"] == "cost"

    await desk.stage_hook()(
        db_session, await _cycle(db_session, company, 2, now + timedelta(days=3))
    )
    assert len(await _looks(db_session, company)) == 1, "three days later: not yet"
    # a week later, even if the cycle started a little earlier in the day than last week's
    week = await _cycle(db_session, company, 3, now + timedelta(days=7, minutes=-5))
    await desk.stage_hook()(db_session, week)
    assert len(await _looks(db_session, company)) == 2


async def test_a_failed_look_does_not_cost_a_week(db_session):
    company, unit, agent = await _world(db_session)
    desk = _desk()
    now = datetime.now(UTC)
    await desk.stage_hook()(db_session, await _cycle(db_session, company, 1, now))
    [look] = await _looks(db_session, company)
    look.state = WorkflowRunState.FAILED.value
    await db_session.flush()
    await desk.stage_hook()(
        db_session, await _cycle(db_session, company, 2, now + timedelta(days=1))
    )
    assert len(await _looks(db_session, company)) == 2


async def test_nobody_looks_without_an_agent_or_on_a_paused_project(db_session):
    company, unit, _ = await _world(db_session, hire=False)
    desk = _desk()
    now = datetime.now(UTC)
    await desk.stage_hook()(db_session, await _cycle(db_session, company, 1, now))
    assert await _looks(db_session, company) == []

    staffed, _, agent = await _world(db_session)
    project = await market_watch_project(db_session, staffed.id, actor=HUMAN)
    project.state = ProjectState.PAUSED.value  # its kill criteria stopped it: a decision to undo
    await db_session.flush()
    await desk.stage_hook()(db_session, await _cycle(db_session, staffed, 1, now))
    assert await _looks(db_session, staffed) == []


async def test_every_company_has_the_chair_and_hiring_fills_it(db_session):
    company, unit, agent = await _world(db_session)
    chair = await role_by_key(db_session, company.id, BUSINESS_ROLE)
    assert chair is not None and chair.title == "Business Development"
    assert (agent.role_id, agent.department_id) == (chair.id, chair.department_id)
    assert BUSINESS_ROLE == ROLE, "the position and the behaviour must name the same role"


# --- the simulated business developer ----------------------------------------------------------


def test_the_scripted_business_developer_writes_nothing_down():
    """In simulation it looks and invents nothing: a scripted opportunity would cost the CEO a
    real review of something nobody observed."""
    from autora.runtime.models.types import Message, ModelRequest

    snapshot = '{"company": "x", "opportunities": [{"key": "a"}, {"key": "b"}]}'
    request = ModelRequest.model_construct(
        context=type("Context", (), {"role": ROLE, "task_name": WATCH_MARKET})(),
        messages=[Message.user("The company right now:\n" + snapshot)],
    )
    watch = MarketWatch.model_validate_json(company_simulation.respond(request).text)
    assert watch.findings == []
    assert watch.nothing_new_because
    assert "2 open opportunities" in watch.summary
