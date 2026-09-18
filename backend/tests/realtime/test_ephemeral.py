"""T-304: live AGENT_STEP_PROGRESS from the runner, rate-limited, never stored."""

import asyncio
import uuid

from sqlalchemy import func, select

from autora.app import build_worker
from autora.db.models import EventRecord, WorkflowRun
from autora.realtime.gateway import EventHub
from autora.runtime.actor import Actor
from autora.runtime.events import catalog as ev
from autora.runtime.events import new_event
from autora.runtime.progress import ProgressPublisher
from tests.echo_fixtures import start_echo
from tests.realtime.test_gateway import FakeSocket

ORDER = ("echo_research", "echo_analyze", "echo_write")


class RecordingPublisher(ProgressPublisher):
    """The real rate logic; sending only records (no database)."""

    def __init__(self, **kw):
        super().__init__(session_factory=None, **kw)
        self.published: list[tuple[uuid.UUID, int]] = []

    async def _send(self, run_id, envelope):
        self._last_sent[run_id] = self.clock()
        self.published.append((run_id, envelope.payload.tokens_so_far))


def _progress(run_id, tokens):
    return new_event(
        ev.AgentStepProgress(step_seq=0, tokens_so_far=tokens),
        company_id=uuid.uuid4(), actor=Actor.system("t"), aggregate_type="agent_run",
        aggregate_id=run_id, run_id=run_id,
    )  # fmt: skip


# --- rate limit ----------------------------------------------------------------------------


async def test_rate_limit_sends_the_first_then_the_latest():
    publisher = RecordingPublisher(min_interval=0.2)
    run = uuid.uuid4()
    for tokens in range(1, 11):
        await publisher.report(run, _progress(run, tokens))
    assert publisher.published == [(run, 1)], "the first goes out at once"
    await asyncio.sleep(0.3)
    assert publisher.published == [(run, 1), (run, 10)], "then only the newest, once"
    await asyncio.sleep(0.3)
    assert len(publisher.published) == 2


async def test_runs_are_limited_separately_and_finish_drops_pending():
    publisher = RecordingPublisher(min_interval=0.2)
    a, b = uuid.uuid4(), uuid.uuid4()
    await publisher.report(a, _progress(a, 1))
    await publisher.report(b, _progress(b, 1))
    await publisher.report(a, _progress(a, 2))
    await publisher.finish(a)  # a ended: its pending 2 is dropped
    await asyncio.sleep(0.3)
    assert publisher.published == [(a, 1), (b, 1)]
    assert not publisher._timers and a not in publisher._last_sent


# --- through the runner and the gateway ------------------------------------------------------


async def _run_echo(committed, e2e_settings, echo_company, *, min_interval=None, publisher=None):
    worker = build_worker(
        e2e_settings, session_factory=committed, company_ids=frozenset({echo_company.company.id})
    )
    if publisher is not None:
        worker.runner.progress = publisher
    elif min_interval is not None:
        worker.runner.progress.min_interval = min_interval
    wf, _ = await start_echo(committed, echo_company)
    await worker.run_until_idle()
    return wf


async def test_progress_reaches_the_browser_and_is_never_stored(
    committed, db_engine, e2e_settings, echo_company
):
    company = echo_company.company.id
    hub = EventHub(engine=db_engine, session_factory=committed, poll_interval=30)
    await hub.start()
    try:
        socket = FakeSocket()
        await hub.connect(company, socket, since=0)
        await _run_echo(committed, e2e_settings, echo_company, min_interval=0)
        await socket.wait_for(lambda ms: sum(m["type"] == "EPHEMERAL" for m in ms) == 9)
    finally:
        await hub.stop()

    by_run: dict[str, list[dict]] = {}
    for message in socket.of("EPHEMERAL"):
        assert message["event_type"] == "AGENT_STEP_PROGRESS" and "seq" not in message
        by_run.setdefault(message["run_id"], []).append(message["payload"])
    assert len(by_run) == 3, "one run per desk"
    for steps in by_run.values():
        # model call (100 in + 50 out), tool call (+ the tool's own progress), model call
        assert [(p["step_seq"], p["tokens_so_far"]) for p in steps] == [
            (0, 150),
            (1, 150),
            (2, 300),
        ]
        assert [p["progress"] for p in steps] == [
            None, {"label": "notes", "current": 1, "target": 1}, None,
        ]  # fmt: skip

    async with committed() as session:
        stored = await session.scalar(
            select(func.count()).where(
                EventRecord.company_id == company, EventRecord.event_type == "AGENT_STEP_PROGRESS"
            )
        )
    assert stored == 0


async def test_default_rate_is_at_most_one_per_second_per_run(
    committed, db_engine, e2e_settings, echo_company
):
    hub = EventHub(engine=db_engine, session_factory=committed, poll_interval=30)
    await hub.start()
    try:
        socket = FakeSocket()
        await hub.connect(echo_company.company.id, socket, since=0)
        await _run_echo(committed, e2e_settings, echo_company)  # default min_interval 1 s
        await socket.wait_for(lambda ms: sum(m["type"] == "EPHEMERAL" for m in ms) >= 3)
        await asyncio.sleep(0.2)
    finally:
        await hub.stop()
    arrivals: dict[str, list[float]] = {}
    for message, at in zip(socket.messages, socket.received_at, strict=True):
        if message["type"] == "EPHEMERAL":
            arrivals.setdefault(message["run_id"], []).append(at)
    assert len(arrivals) == 3
    for stamps in arrivals.values():
        gaps = [b - a for a, b in zip(stamps, stamps[1:], strict=False)]
        assert all(gap >= 0.95 for gap in gaps), gaps


async def test_a_broken_publisher_never_fails_the_run(committed, e2e_settings, echo_company):
    def broken():
        raise ConnectionError("notify channel down")

    publisher = ProgressPublisher(session_factory=broken, min_interval=0)
    wf = await _run_echo(committed, e2e_settings, echo_company, publisher=publisher)
    async with committed() as session:
        assert (await session.get(WorkflowRun, wf.id)).state == "SUCCEEDED"
    assert publisher.sent == 0
