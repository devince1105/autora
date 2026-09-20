"""T-609: what an agent carries from its last runs into the next one.

The two things worth testing here: the memory is bounded without needing a cleanup job to run,
and what it renders into the prompt is short, ordered, and about the work.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from autora.db.models import AgentMemoryEntry, MemoryKind
from autora.runtime.memory import (
    MAX_CHARS,
    RECENT_RUNS,
    ROW_CAP,
    TTL,
    AgentMemory,
    Recollection,
    count,
    run_memory,
)
from tests.conftest import running_agent_run

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)


class Clock:
    def __init__(self, now: datetime = NOW):
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta) -> datetime:
        self.now += timedelta(**delta)
        return self.now


async def _agent(session):
    run = await running_agent_run(session, "memory")
    return run


async def _remember(memory, session, run, task: str, outcome: str = "done", **kw):
    return await memory.remember(
        session,
        company_id=run.company_id,
        agent_id=run.agent_id,
        content=run_memory(task=task, outcome=outcome, **kw),
        task_id=run.task_id,
        run_id=run.id,
    )


# --- remembering and recalling -----------------------------------------------------------------


async def test_an_agent_recalls_its_last_few_runs_newest_first(db_session):
    run = await _agent(db_session)
    clock = Clock()
    memory = AgentMemory(clock=clock)
    for n in range(5):
        clock.advance(minutes=n + 1)
        await _remember(memory, db_session, run, f"Draft {n}")

    recalled = await memory.recall(db_session, run.agent_id)

    assert len(recalled) == RECENT_RUNS == 3
    assert [e.content["task"] for e in recalled.entries] == ["Draft 4", "Draft 3", "Draft 2"]


async def test_a_new_agent_remembers_nothing_and_says_nothing(db_session):
    run = await _agent(db_session)

    recalled = await AgentMemory(clock=Clock()).recall(db_session, run.agent_id)

    assert not recalled and recalled.text() == ""


async def test_one_agent_does_not_read_another_s_memory(db_session):
    mine = await _agent(db_session)
    theirs = await _agent(db_session)
    memory = AgentMemory(clock=Clock())
    await _remember(memory, db_session, mine, "My draft")
    await _remember(memory, db_session, theirs, "Their draft")

    recalled = await memory.recall(db_session, mine.agent_id)

    assert [e.content["task"] for e in recalled.entries] == ["My draft"]


async def test_being_sent_back_is_what_is_worth_remembering(db_session):
    """The point of the whole feature: an agent asked for a third revision should be able to
    tell that it is the third."""
    run = await _agent(db_session)
    clock = Clock()
    memory = AgentMemory(clock=clock)
    for n in range(2):
        clock.advance(minutes=n + 1)
        await _remember(
            memory,
            db_session,
            run,
            "Draft: Lumen City microgrid",
            outcome="sent back",
            issues=[f"claim {n} has no evidence"],
        )

    text = (await memory.recall(db_session, run.agent_id)).text()

    assert text.count("sent back") == 2  # once per run, not once per clause
    assert "claim 1 has no evidence" in text


# --- the bounds ----------------------------------------------------------------------------------


async def test_an_agent_keeps_only_its_newest_entries(db_session):
    """Enforced on write, so the bound holds whether or not any cleanup job ever runs."""
    run = await _agent(db_session)
    clock = Clock()
    memory = AgentMemory(clock=clock, row_cap=5)
    for n in range(12):
        clock.advance(minutes=n + 1)
        await _remember(memory, db_session, run, f"Task {n}")

    kept = (
        await db_session.scalars(
            select(AgentMemoryEntry.content).where(AgentMemoryEntry.agent_id == run.agent_id)
        )
    ).all()

    assert len(kept) == 5
    assert sorted(c["task"] for c in kept) == ["Task 10", "Task 11", "Task 7", "Task 8", "Task 9"]


async def test_the_cap_is_per_agent_not_per_company(db_session):
    mine = await _agent(db_session)
    theirs = await _agent(db_session)
    clock = Clock()
    memory = AgentMemory(clock=clock, row_cap=2)
    for n in range(4):
        clock.advance(minutes=n + 1)
        await _remember(memory, db_session, mine, f"Mine {n}")
        await _remember(memory, db_session, theirs, f"Theirs {n}")

    assert await count(db_session, mine.agent_id) == 2
    assert await count(db_session, theirs.agent_id) == 2


async def test_what_has_aged_out_is_not_recalled_even_before_it_is_deleted(db_session):
    run = await _agent(db_session)
    clock = Clock()
    memory = AgentMemory(clock=clock)
    await _remember(memory, db_session, run, "Ancient history")
    clock.advance(days=TTL.days + 1)
    await _remember(memory, db_session, run, "Yesterday")

    recalled = await memory.recall(db_session, run.agent_id)

    assert [e.content["task"] for e in recalled.entries] == ["Yesterday"]
    assert await count(db_session, run.agent_id) == 2  # still there, just not read


async def test_housekeeping_removes_what_nobody_will_read(db_session):
    run = await _agent(db_session)
    clock = Clock()
    memory = AgentMemory(clock=clock)
    await _remember(memory, db_session, run, "Ancient history")
    clock.advance(days=TTL.days + 1)
    await _remember(memory, db_session, run, "Yesterday")

    removed = await memory.forget_expired(db_session)

    # housekeeping is company-wide by design, so count what it did to *this* agent
    assert removed >= 1
    assert await count(db_session, run.agent_id) == 1
    assert (await memory.recall(db_session, run.agent_id)).entries[0].content["task"] == (
        "Yesterday"
    )


async def test_the_maintenance_job_is_the_same_thing(db_session):
    run = await _agent(db_session)
    clock = Clock()
    memory = AgentMemory(clock=clock)
    await _remember(memory, db_session, run, "Ancient history")
    clock.advance(days=TTL.days + 1)

    await memory.maintenance_job()(db_session)

    assert await count(db_session, run.agent_id) == 0


# --- what it puts in the prompt ------------------------------------------------------------------


def _entry(task: str, outcome: str, **kw) -> AgentMemoryEntry:
    return AgentMemoryEntry(
        id=uuid.uuid4(),
        company_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        kind=MemoryKind.RUN.value,
        content=run_memory(task=task, outcome=outcome, **kw),
    )


def test_the_recollection_reads_like_a_diary_oldest_first():
    """Recall gives newest first; the prompt wants the opposite, so the most recent run sits
    nearest the question being asked."""
    recalled = Recollection(
        (_entry("Draft 3", "done"), _entry("Draft 2", "sent back"), _entry("Draft 1", "done"))
    )

    text = recalled.text()

    assert text.startswith("Your recent runs:")
    assert text.index("Draft 1") < text.index("Draft 2") < text.index("Draft 3")


def test_a_recollection_says_what_came_of_each_run():
    recalled = Recollection(
        (
            _entry(
                "Draft: microgrid",
                "sent back",
                summary="2 claims, 1 without evidence",
                issues=["claim 2 has no evidence", "the number is not a number claim"],
            ),
        )
    )

    text = recalled.text()

    assert "Draft: microgrid: sent back" in text
    assert "2 claims, 1 without evidence" in text
    assert "because: claim 2 has no evidence" in text


def test_a_long_memory_is_cut_rather_than_carried():
    recalled = Recollection(tuple(_entry("T" * 200, "done", summary="S" * 300) for _ in range(5)))

    text = recalled.text()

    assert len(text) <= MAX_CHARS + len("Your recent runs:\n")
    assert text.endswith("…")


def test_what_a_run_leaves_behind_is_small():
    content = run_memory(
        task="Draft",
        outcome="sent back",
        summary="x" * 900,
        issues=[f"issue {n}" * 100 for n in range(9)],
    )

    assert len(content["summary"]) == 400
    assert len(content["issues"]) == 5
    assert all(len(issue) <= 200 for issue in content["issues"])


def test_a_run_that_went_fine_carries_no_issues():
    assert "issues" not in run_memory(task="Draft", outcome="done", summary="ok")
    assert "summary" not in run_memory(task="Draft", outcome="done")


async def test_the_default_bounds_are_the_spec_s(db_session):
    """platform/08 §4: 30 days, 50 rows per agent, 3 into the context."""
    memory = AgentMemory()
    assert (memory.ttl, memory.row_cap) == (timedelta(days=30), 50)
    assert (ROW_CAP, RECENT_RUNS) == (50, 3)
