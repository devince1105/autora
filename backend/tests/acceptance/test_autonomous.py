"""Phase 6 acceptance: the company runs itself (AC-11, AC-14, T-610).

Three questions, all answered by running the real thing in simulation (``MODEL_PROVIDER=fake``,
fixture tools) with an accelerated clock:

1. **Does a new company start its own day?** Nobody calls ``cycles.start`` here. A company is
   created, the schedule it was born with comes due, and a cycle exists.
2. **Does it keep going?** Seven days in a row, each planned, worked, measured, governed and
   reviewed, with no human actor anywhere in the events.
3. **Is any of this about news?** A company with no domain at all runs the same days.

The clock is the only thing that is faked. Everything else — the scheduler, the worker, the
task manager, the agents, the ledger, the rules — is what the worker process runs.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from autora.app import build_runtime, build_worker
from autora.company import opportunities
from autora.company.agents import hire_agent, strategist
from autora.company.companies import create_company
from autora.company.cycle import CYCLE_START_SCHEDULE, ensure_cycle_schedule
from autora.company.organization import add_role, bootstrap_executive
from autora.db.models import (
    BusinessProposal,
    BusinessUnit,
    CommandRecord,
    Company,
    Cycle,
    CycleStage,
    EventRecord,
    KpiScope,
    KpiSnapshot,
    Opportunity,
    OpportunityState,
    Project,
    ProjectState,
    ProposalState,
    Schedule,
    WorkflowRun,
)
from autora.domains.newsroom.demo import gather_stories, seed_demo
from autora.domains.newsroom.settings import get_newsroom_settings
from autora.domains.newsroom.sources import SourcePoller
from autora.domains.newsroom.stories import StoryDesk
from autora.runtime.actor import Actor

from autora.app import build_embedder, build_page_fetcher, build_search_provider  # isort: skip

OPERATOR = Actor.human("acceptance-operator")
CYCLES = 7
"""AC-14. Seven because a week is long enough for the second cycle's mistakes to show."""


class Clock:
    """A clock the test moves. The company cannot tell it from the real one."""

    def __init__(self, now: datetime):
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta) -> datetime:
        self.now += timedelta(**delta)
        return self.now


async def _wind_to_next_start(committed, company_id: uuid.UUID, clock: "Clock") -> datetime:
    """Move the clock to just before the company's next scheduled start.

    The schedule row decides when the day begins — the test does not get to pick 06:00 for it.
    Reading it back is also the only honest way to tell the company opened the day itself.
    """
    async with committed() as session:
        schedule = await session.scalar(
            select(Schedule).where(
                Schedule.company_id == company_id, Schedule.name == CYCLE_START_SCHEDULE
            )
        )
    assert schedule is not None
    clock.now = schedule.next_run_at.astimezone(UTC) - timedelta(minutes=5)
    return schedule.next_run_at.astimezone(UTC)


async def _drive(worker, clock: Clock, *, hours: int, step_minutes: int = 20) -> None:
    """Run the worker through a stretch of the day, letting maintenance come due as it passes.

    The worker's own loop does this with sleeps; here the clock moves instead, so a day costs
    milliseconds. Nothing else is skipped: every tick claims, runs and advances for real.
    """
    for _ in range(int(hours * 60 / step_minutes)):
        await worker.run_until_idle()
        clock.advance(minutes=step_minutes)


# --- 1. a company that starts its own day --------------------------------------------------


async def test_a_new_company_is_born_with_a_daily_cycle(committed):
    """AC-11, the first half: nobody switches autonomy on."""
    slug = f"cold-{uuid.uuid4().hex[:8]}"
    async with committed() as session:
        company, _ = await create_company(
            session, slug=slug, name="Cold Start",
            mission=None, actor=OPERATOR,
        )  # fmt: skip
        await session.commit()
        company_id = company.id

    async with committed() as session:
        schedule = await session.scalar(
            select(Schedule).where(
                Schedule.company_id == company_id, Schedule.name == CYCLE_START_SCHEDULE
            )
        )
        assert schedule is not None, "a company was created without a day"
        assert schedule.enabled and schedule.next_run_at > datetime.now(UTC)
        # asking again is not a second schedule
        again = await ensure_cycle_schedule(session, company_id)
        assert again.id == schedule.id


async def test_the_schedule_opens_the_first_cycle_with_nobody_watching(committed, e2e_settings):
    """AC-11, the second half: at the next scheduled point, a cycle exists."""
    clock = Clock(datetime.now(UTC))
    runtime = build_runtime(e2e_settings)
    runtime.cycles.clock = clock
    slug = f"cold-{uuid.uuid4().hex[:8]}"

    async with committed() as session:
        company, _ = await create_company(
            session, slug=slug, name="Cold Start",
            mission=None, actor=OPERATOR,
        )  # fmt: skip
        await session.commit()
        company_id = company.id

    worker = build_worker(
        e2e_settings, session_factory=committed, company_ids=frozenset({company_id}),
        runtime=runtime,
    )  # fmt: skip
    worker.clock = clock
    worker.scheduler.clock = clock
    worker.maintenance_interval = 1

    due_at = await _wind_to_next_start(committed, company_id, clock)
    await worker.run_until_idle()
    async with committed() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(Cycle).where(Cycle.company_id == company_id)
            )
            == 0
        )  # five minutes early: the schedule is not due, so there is no day yet

    clock.advance(minutes=10)  # the scheduled point passes
    await worker.run_until_idle()

    async with committed() as session:
        cycles = (await session.scalars(select(Cycle).where(Cycle.company_id == company_id))).all()
        fired = await session.scalar(
            select(func.count())
            .select_from(EventRecord)
            .where(
                EventRecord.company_id == company_id,
                EventRecord.event_type == "CYCLE_STARTED",
            )
        )
    assert len(cycles) == 1 and cycles[0].seq == 1
    assert fired == 1
    assert due_at <= cycles[0].started_at.astimezone(UTC) <= clock.now


# --- 2. seven days in a row ------------------------------------------------------------------


@pytest.mark.slow
async def test_seven_cycles_with_nobody_fixing_anything(committed, e2e_settings):
    """AC-14: seven days, each planned and reviewed, with the rules run on every one of them.

    The only human act in this test is creating the company and giving it sources. After that
    every workflow, every agent run and every decision is the company's own.
    """
    clock = Clock(datetime.now(UTC))
    runtime = build_runtime(e2e_settings)
    runtime.cycles.clock = clock
    poller = SourcePoller(
        fetcher=build_page_fetcher(e2e_settings), search=build_search_provider(e2e_settings)
    )
    desk = StoryDesk(
        build_embedder(e2e_settings), threshold=get_newsroom_settings().story_match_threshold
    )
    slug = f"soak-{uuid.uuid4().hex[:8]}"

    async with committed() as session:
        demo = await seed_demo(session, actor=OPERATOR, slug=slug)
        await gather_stories(session, demo.company.id, poller=poller, desk=desk)
        await session.commit()
        company_id = demo.company.id

    worker = build_worker(
        e2e_settings, session_factory=committed, company_ids=frozenset({company_id}),
        runtime=runtime,
    )  # fmt: skip
    worker.clock = clock
    worker.scheduler.clock = clock
    worker.maintenance_interval = 1

    for _ in range(CYCLES):
        await _wind_to_next_start(committed, company_id, clock)
        clock.advance(minutes=10)  # the day opens itself
        await _drive(worker, clock, hours=17)

    async with committed() as session:
        cycles = (
            await session.scalars(
                select(Cycle).where(Cycle.company_id == company_id).order_by(Cycle.seq)
            )
        ).all()
        workflows = await session.scalar(
            select(func.count())
            .select_from(WorkflowRun)
            .where(WorkflowRun.company_id == company_id)
        )
        measured = (
            await session.scalars(
                select(KpiSnapshot).where(
                    KpiSnapshot.company_id == company_id,
                    KpiSnapshot.scope == KpiScope.COMPANY.value,
                )
            )
        ).all()
        opened = await session.scalar(
            select(func.min(EventRecord.seq)).where(
                EventRecord.company_id == company_id,
                EventRecord.event_type == "CYCLE_STARTED",
            )
        )
        # everything a person did happened before the first day opened (seeding the company)
        human_events = (
            await session.scalars(
                select(EventRecord.event_type).where(
                    EventRecord.company_id == company_id,
                    EventRecord.actor["kind"].astext == "human",
                    EventRecord.seq > opened,
                )
            )
        ).all()

    print(
        "\n"
        + "\n".join(
            f"cycle {c.seq}: {c.stage} plan={(c.plan or {}).get('by')} "
            f"review={'missing' if (c.review or {}).get('missing') else 'ok'} "
            f"governance={c.governance and c.governance['checked']}"
            for c in cycles
        )
    )
    assert len(cycles) == CYCLES, [f"{c.seq}:{c.stage}" for c in cycles]
    assert [c.seq for c in cycles] == list(range(1, CYCLES + 1))
    for cycle in cycles:
        where = f"cycle {cycle.seq}"
        assert cycle.stage == CycleStage.DONE.value, f"{where} never finished ({cycle.stage})"
        assert cycle.ended_at is not None, where
        assert cycle.plan, f"{where} has no plan at all"
        assert cycle.review, f"{where} has no review at all"
        # the rules ran on every day, whether or not they found anything (T-610)
        assert cycle.governance is not None, f"nobody governed {where}"
        assert cycle.governance["checked"], f"{where}: the rules looked at nothing"

    # every day was the CEO's own: not one fell back, and not one review went missing
    assert [c.seq for c in cycles if c.plan.get("by") != "ceo"] == []
    assert [c.seq for c in cycles if c.review.get("missing")] == []
    assert all(c.governance["checked"].get("projects", 0) > 0 for c in cycles)
    assert workflows > CYCLES, "the days were planned but nothing was worked on"
    assert len(measured) == CYCLES, "a day went by unmeasured"
    # a person seeded the company and then went away: seven days ran without one
    assert human_events == [], sorted(set(human_events))


# --- 3. none of this is about news ------------------------------------------------------------


@pytest.mark.slow
async def test_a_company_with_no_domain_runs_the_same_days(committed, e2e_settings):
    """ARCHITECTURE_V2_1 §9: delete every domain and the company still keeps time.

    No newsroom, no sources, no stories — a company with a CEO and a project, and nothing that
    knows anything about news. It plans, measures, governs and reviews, exactly as the newsroom
    does. The loop and the CEO are the company's; the business is not.
    """
    clock = Clock(datetime.now(UTC))
    runtime = build_runtime(e2e_settings)
    runtime.cycles.clock = clock
    slug = f"plain-{uuid.uuid4().hex[:8]}"

    async with committed() as session:
        company, _ = await create_company(
            session, slug=slug, name="A Company",
            mission="do something", actor=OPERATOR,
        )  # fmt: skip
        _, ceo_role = await bootstrap_executive(session, company.id, actor=OPERATOR)
        await hire_agent(
            session, company_id=company.id, role=ceo_role.key, display_name="Cyra",
            actor=OPERATOR, position=ceo_role,
        )  # fmt: skip
        session.add(
            Project(
                company_id=company.id,
                name="Whatever it does",
                state=ProjectState.ACTIVE.value,
                kill_criteria={
                    "auto_pause_if": {"metric": "cost_usd", "op": ">", "value": 1000},
                },
            )
        )
        await session.commit()
        company_id = company.id

    worker = build_worker(
        e2e_settings, session_factory=committed, company_ids=frozenset({company_id}),
        runtime=runtime,
    )  # fmt: skip
    worker.clock = clock
    worker.scheduler.clock = clock
    worker.maintenance_interval = 1

    for _ in range(2):
        await _wind_to_next_start(committed, company_id, clock)
        clock.advance(minutes=10)
        await _drive(worker, clock, hours=17)

    async with committed() as session:
        cycles = (
            await session.scalars(
                select(Cycle).where(Cycle.company_id == company_id).order_by(Cycle.seq)
            )
        ).all()
        company = await session.get(Company, company_id)

    assert len(cycles) == 2
    for cycle in cycles:
        assert cycle.stage == CycleStage.DONE.value
        assert cycle.plan, f"cycle {cycle.seq}: no plan, not even a fallback"
        assert cycle.governance is not None
        # at least the one it was given; the CEO may have started others of its own
        assert cycle.governance["checked"]["projects"] >= 1
    # the CEO planned for a company in no particular industry
    assert any(cycle.plan.get("by") == "ceo" for cycle in cycles), [c.plan for c in cycles]
    assert any(cycle.review for cycle in cycles)
    assert company is not None


# --- 4. deciding what to do next --------------------------------------------------------------


@pytest.mark.slow
async def test_the_company_decides_about_an_opportunity_by_itself(committed, e2e_settings):
    """T-605a increment: the CEO's review reaches the business loop (ARCHITECTURE_V2_1 §5).

    A person records something the company might do and then leaves. In the cycle's REVIEWING
    stage the CEO reads it in its snapshot, scores it and moves it on — through the command
    pipeline, like everything else it decides. Nobody asks it to.
    """
    clock = Clock(datetime.now(UTC))
    runtime = build_runtime(e2e_settings)
    runtime.cycles.clock = clock
    slug = f"opp-{uuid.uuid4().hex[:8]}"

    async with committed() as session:
        company, _ = await create_company(
            session, slug=slug, name="A Curious Company",
            mission="find something worth doing", actor=OPERATOR,
        )  # fmt: skip
        _, ceo_role = await bootstrap_executive(session, company.id, actor=OPERATOR)
        await hire_agent(
            session, company_id=company.id, role=ceo_role.key, display_name="Cyra",
            actor=OPERATOR, position=ceo_role,
        )  # fmt: skip
        session.add(
            Project(
                company_id=company.id,
                name="Keep the lights on",
                state=ProjectState.ACTIVE.value,
                kill_criteria={},
            )
        )
        opportunity = await opportunities.discover(
            session,
            company_id=company.id,
            key="ai_english",
            title="AI English learning",
            thesis="Adults pay for lessons; a model teaches at the margin of zero.",
            actor=OPERATOR,
        )
        await opportunities.add_signal(
            session,
            opportunity,
            source="search",
            summary="three competitors, none bilingual",
            actor=OPERATOR,
        )
        await session.commit()
        company_id, opportunity_id = company.id, opportunity.id

    worker = build_worker(
        e2e_settings, session_factory=committed, company_ids=frozenset({company_id}),
        runtime=runtime,
    )  # fmt: skip
    worker.clock = clock
    worker.scheduler.clock = clock
    worker.maintenance_interval = 1

    await _wind_to_next_start(committed, company_id, clock)
    clock.advance(minutes=10)
    await _drive(worker, clock, hours=17)

    async with committed() as session:
        decided = await session.get(Opportunity, opportunity_id)
        commands = (
            await session.scalars(
                select(CommandRecord)
                .where(
                    CommandRecord.company_id == company_id,
                    CommandRecord.command.in_(["ScoreOpportunity", "AdvanceOpportunity"]),
                )
                .order_by(CommandRecord.created_at)
            )
        ).all()
        cycle = await session.scalar(select(Cycle).where(Cycle.company_id == company_id))

    assert decided.state == OpportunityState.EVALUATING.value, "nobody looked at it properly"
    assert decided.score is not None, "it was moved on without being compared to anything"
    assert [c.command for c in commands] == ["ScoreOpportunity", "AdvanceOpportunity"]
    assert all(c.actor["kind"] == "agent" and c.role == "ceo" for c in commands)
    assert all(c.outcome == "done" for c in commands)
    # and the review says what it did, which is what the validator checks
    [written] = cycle.review["opportunities"]
    assert written["opportunity_id"] == str(opportunity_id)
    assert written["decision"] == "evaluate"


@pytest.mark.slow
async def test_an_opportunity_becomes_a_proposal_a_person_can_decide(committed, e2e_settings):
    """T-611, the loop closed: nobody writes the proposal by hand (ARCHITECTURE_V2_1 §4).

    Day one the CEO evaluates what somebody noticed. Day two the company starts an exploration
    project and its strategist writes a proposal and puts it up for decision. Then — and only
    then — a person approves it, and the business exists. Everything between the two human acts
    is the company's own.
    """
    clock = Clock(datetime.now(UTC))
    runtime = build_runtime(e2e_settings)
    runtime.cycles.clock = clock
    slug = f"proposal-{uuid.uuid4().hex[:8]}"

    async with committed() as session:
        company, _ = await create_company(
            session, slug=slug, name="A Company With An Idea",
            mission="find something worth doing", actor=OPERATOR,
        )  # fmt: skip
        department, ceo_role = await bootstrap_executive(session, company.id, actor=OPERATOR)
        strategist_role = await add_role(
            session, company_id=company.id, department_id=department.id,
            key=strategist.ROLE, title="Strategist", actor=OPERATOR,
        )  # fmt: skip
        for role, name in ((ceo_role, "Cyra"), (strategist_role, "Sol")):
            await hire_agent(
                session, company_id=company.id, role=role.key, display_name=name,
                actor=OPERATOR, position=role,
            )  # fmt: skip
        opportunity = await opportunities.discover(
            session,
            company_id=company.id,
            key="ai_english",
            title="AI English learning",
            thesis="Adults pay for lessons; a model teaches at the margin of zero.",
            actor=OPERATOR,
        )
        await opportunities.add_signal(
            session, opportunity, source="search", summary="three competitors, none bilingual",
            actor=OPERATOR,
        )  # fmt: skip
        await session.commit()
        company_id, opportunity_id = company.id, opportunity.id

    worker = build_worker(
        e2e_settings, session_factory=committed, company_ids=frozenset({company_id}),
        runtime=runtime,
    )  # fmt: skip
    worker.clock = clock
    worker.scheduler.clock = clock
    worker.maintenance_interval = 1

    for _ in range(2):  # day one evaluates it, day two writes the proposal
        await _wind_to_next_start(committed, company_id, clock)
        clock.advance(minutes=10)
        await _drive(worker, clock, hours=17)

    async with committed() as session:
        proposal = await session.scalar(
            select(BusinessProposal).where(BusinessProposal.opportunity_id == opportunity_id)
        )
        exploring = await session.scalar(
            select(Project).where(Project.opportunity_id == opportunity_id)
        )
        human_events = (
            await session.scalars(
                select(EventRecord.event_type).where(
                    EventRecord.company_id == company_id,
                    EventRecord.actor["kind"].astext == "human",
                    EventRecord.event_type.in_(["PROPOSAL_DRAFTED", "PROPOSAL_SUBMITTED"]),
                )
            )
        ).all()

    assert proposal is not None, "nobody wrote the proposal"
    assert proposal.state == ProposalState.SUBMITTED.value, "it was written but never put up"
    assert proposal.kill_criteria, "a proposal that does not say what would end it"
    assert proposal.authored_by_run_id is not None, "it came from nowhere"
    # finding out is an ordinary project, with a budget and a stop-loss like any other work
    assert (
        exploring is not None and exploring.kill_criteria["auto_pause_if"]["metric"] == "cost_usd"
    )
    assert human_events == [], "a person wrote it after all"

    # and now the one thing that is a person's: opening the business
    async with committed() as session:
        asked = await runtime.commands.submit(
            session,
            "CreateBusinessUnit",
            {"proposal_id": str(proposal.id), "capital": "50"},
            company_id=company_id,
            actor=Actor.human("founder"),
            role=None,
            idempotency_key=f"open-{uuid.uuid4().hex[:8]}",
        )
        await session.commit()

    async with committed() as session:
        unit = await session.scalar(
            select(BusinessUnit).where(BusinessUnit.company_id == company_id)
        )
        decided = await session.get(Opportunity, opportunity_id)

    assert asked.done, "a person's own command should not need approving"
    assert unit is not None and unit.state == "ACTIVE"
    assert decided.state == OpportunityState.APPROVED.value
    assert decided.business_unit_id == unit.id
