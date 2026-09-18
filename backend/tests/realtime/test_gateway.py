"""T-303: WebSocket gateway (3d-office/05 §8) with real NOTIFY and committed events.

The hub is driven with in-memory transports; the last test runs the real endpoint on a real
uvicorn server over a real WebSocket.
"""

import asyncio
import json
import uuid

import pytest
import uvicorn
import websockets

from autora.company.events import GoalCreated
from autora.realtime.gateway import CloseCode, EventHub
from autora.runtime.actor import Actor
from autora.runtime.events import catalog as ev
from autora.runtime.events import new_event
from autora.runtime.events.outbox import emit, publish_ephemeral
from tests.conftest import unique_company

SYSTEM = Actor.system("test")


class FakeSocket:
    """Records what the hub sends. ``gate`` (if set) holds EVENT sends until released."""

    def __init__(self, gate: asyncio.Event | None = None):
        self.messages: list[dict] = []
        self.received_at: list[float] = []
        self.arrived = asyncio.Condition()
        self.closed: int | None = None
        self.gate = gate

    async def send_json(self, message):
        if self.gate is not None and message["type"] == "EVENT":
            await self.gate.wait()
        async with self.arrived:
            self.messages.append(json.loads(json.dumps(message)))
            self.received_at.append(asyncio.get_running_loop().time())
            self.arrived.notify_all()

    async def close(self, code=1000):
        self.closed = code

    async def wait_for(self, predicate, timeout=5.0):
        async def _wait():
            async with self.arrived:
                await self.arrived.wait_for(lambda: predicate(self.messages))

        await asyncio.wait_for(_wait(), timeout)

    def of(self, kind):
        return [m for m in self.messages if m["type"] == kind]

    def event_seqs(self):
        seqs = []
        for message in self.messages:
            if message["type"] == "EVENTS":
                seqs += [item["seq"] for item in message["items"]]
            elif message["type"] == "EVENT":
                seqs.append(message["seq"])
        return seqs


async def _company(committed):
    async with committed() as session:
        company = await unique_company(session, "ws")
        await session.commit()
    return company.id


async def _emit(committed, company_id, n=1) -> list[int]:
    seqs = []
    async with committed() as session:
        for i in range(n):
            event = await emit(
                session,
                new_event(
                    GoalCreated(title=f"g{i}", level="cycle", metric="m", target=1),
                    company_id=company_id, actor=SYSTEM, aggregate_type="goal",
                    aggregate_id=uuid.uuid4(),
                ),
            )  # fmt: skip
            seqs.append(event.seq)
        await session.commit()
    return seqs


@pytest.fixture
async def hub_factory(db_engine, committed):
    hubs = []

    async def make(**kw):
        hub = EventHub(engine=db_engine, session_factory=committed, **kw)
        await hub.start()
        hubs.append(hub)
        return hub

    yield make
    for hub in hubs:
        await hub.stop()


# --- the four gateway tests of 05 §8 -------------------------------------------------------


async def test_gateway_backlog_then_live(committed, hub_factory):
    hub = await hub_factory(poll_interval=30, batch_size=2)
    company = await _company(committed)
    seqs = await _emit(committed, company, 5)

    socket = FakeSocket()
    await hub.connect(company, socket, since=seqs[1])
    hello = socket.messages[0]
    assert hello["type"] == "HELLO" and hello["mode"] == "backlog" and hello["head_seq"] == seqs[-1]
    assert [len(m["items"]) for m in socket.of("EVENTS")] == [2, 1], "batches of batch_size"
    assert socket.of("BACKLOG_DONE") == [{"type": "BACKLOG_DONE", "head_seq": seqs[-1]}]

    live = await _emit(committed, company, 2)
    await socket.wait_for(lambda ms: sum(m["type"] == "EVENT" for m in ms) == 2)
    assert socket.event_seqs() == seqs[2:] + live, "in order, no gap, no duplicate"
    types = [m["type"] for m in socket.messages]
    assert types == ["HELLO", "EVENTS", "EVENTS", "BACKLOG_DONE", "EVENT", "EVENT"]
    event = socket.of("EVENT")[0]
    assert event["event_type"] == "GOAL_CREATED" and event["company_id"] == str(company)


async def test_gateway_up_to_date_client_goes_live(committed, hub_factory):
    hub = await hub_factory(poll_interval=30)
    company = await _company(committed)
    [head] = await _emit(committed, company)
    socket = FakeSocket()
    await hub.connect(company, socket, since=head)
    assert socket.messages[0]["mode"] == "live" and len(socket.messages) == 1
    [new] = await _emit(committed, company)
    await socket.wait_for(lambda ms: len(ms) == 2)
    assert socket.event_seqs() == [new]


async def test_gateway_snapshot_required(committed, hub_factory):
    hub = await hub_factory(poll_interval=30, backlog_max=3)
    company = await _company(committed)
    seqs = await _emit(committed, company, 6)

    for since, reason in [
        (seqs[0], "gap_too_large"),   # 5 events behind, backlog_max 3
        (None, "unknown_since"),      # the client has no state
        (seqs[-1] + 1000, "unknown_since"),  # ahead of the server: not this database
    ]:  # fmt: skip
        socket = FakeSocket()
        await hub.connect(company, socket, since=since)
        assert [m["type"] for m in socket.messages] == ["HELLO", "SNAPSHOT_REQUIRED"]
        assert socket.messages[0]["mode"] == "snapshot_required"
        assert socket.messages[1]["reason"] == reason
        assert socket.closed == CloseCode.SNAPSHOT_REQUIRED
    assert hub.connections(company) == set()

    socket = FakeSocket()
    await hub.connect(company, socket, since=seqs[2])  # exactly backlog_max behind: fine
    assert socket.messages[0]["mode"] == "backlog" and socket.event_seqs() == seqs[3:]


async def test_notify_fallback_poll(committed, hub_factory):
    """No LISTEN at all (every NOTIFY lost): the poll still delivers, in order."""
    hub = await hub_factory(listen=False, poll_interval=0.2)
    company = await _company(committed)
    [head] = await _emit(committed, company)
    socket = FakeSocket()
    await hub.connect(company, socket, since=head)
    live = await _emit(committed, company, 3)
    await socket.wait_for(lambda ms: sum(m["type"] == "EVENT" for m in ms) == 3, timeout=3)
    assert socket.event_seqs() == live


async def test_notify_delivers_without_waiting_for_the_poll(committed, hub_factory):
    hub = await hub_factory(poll_interval=60)
    company = await _company(committed)
    [head] = await _emit(committed, company)
    socket = FakeSocket()
    await hub.connect(company, socket, since=head)
    [new] = await _emit(committed, company)
    await socket.wait_for(lambda ms: len(ms) == 2, timeout=3)
    assert socket.event_seqs() == [new]


async def test_queue_overflow(committed, hub_factory):
    """A socket that cannot keep up is told to re-hydrate and closed; the others carry on."""
    hub = await hub_factory(poll_interval=30, queue_size=3)
    company = await _company(committed)
    [head] = await _emit(committed, company)
    gate = asyncio.Event()
    slow, fast = FakeSocket(gate), FakeSocket()
    await hub.connect(company, slow, since=head)
    await hub.connect(company, fast, since=head)

    live = await _emit(committed, company, 10)
    await fast.wait_for(lambda ms: sum(m["type"] == "EVENT" for m in ms) == 10)
    gate.set()
    await slow.wait_for(lambda ms: ms[-1]["type"] == "SNAPSHOT_REQUIRED")
    assert slow.messages[-1]["reason"] == "queue_overflow"
    assert slow.event_seqs() == live[:1], "the send in flight completes; the rest is dropped"
    await asyncio.sleep(0.05)
    assert slow.closed == CloseCode.SNAPSHOT_REQUIRED
    assert fast.event_seqs() == live and fast.closed is None
    assert (
        hub.connections(company) == {c for c in hub.connections(company)}
        and len(hub.connections(company)) == 1
    )


# --- routing, ephemeral, heartbeat -----------------------------------------------------------


async def test_companies_are_isolated(committed, hub_factory):
    hub = await hub_factory(poll_interval=30)
    a, b = await _company(committed), await _company(committed)
    [head_a] = await _emit(committed, a)
    [head_b] = await _emit(committed, b)
    socket_a, socket_b = FakeSocket(), FakeSocket()
    await hub.connect(a, socket_a, since=head_a)
    await hub.connect(b, socket_b, since=head_b)
    [event_b] = await _emit(committed, b)
    await socket_b.wait_for(lambda ms: len(ms) == 2)
    await asyncio.sleep(0.2)
    assert socket_b.event_seqs() == [event_b] and socket_a.event_seqs() == []


async def test_ephemeral_events_reach_only_their_company(committed, hub_factory):
    hub = await hub_factory(poll_interval=30)
    a, b = await _company(committed), await _company(committed)
    socket_a, socket_b = FakeSocket(), FakeSocket()
    await hub.connect(a, socket_a, since=0)
    await hub.connect(b, socket_b, since=0)
    agent, run = uuid.uuid4(), uuid.uuid4()
    async with committed() as session:
        await publish_ephemeral(
            session,
            new_event(
                ev.AgentStepProgress(step_seq=3, tokens_so_far=120),
                company_id=a, actor=SYSTEM, aggregate_type="agent_run", aggregate_id=run,
                agent_id=agent, run_id=run,
            ),
        )  # fmt: skip
        await session.commit()
    await socket_a.wait_for(lambda ms: any(m["type"] == "EPHEMERAL" for m in ms))
    [message] = socket_a.of("EPHEMERAL")
    assert message["event_type"] == "AGENT_STEP_PROGRESS"
    assert (message["agent_id"], message["run_id"]) == (str(agent), str(run))
    assert message["payload"]["tokens_so_far"] == 120 and "seq" not in message
    await asyncio.sleep(0.2)
    assert socket_b.of("EPHEMERAL") == []


async def test_heartbeat_and_ping(committed, hub_factory):
    hub = await hub_factory(poll_interval=30, heartbeat_interval=0.2)
    company = await _company(committed)
    [head] = await _emit(committed, company)
    socket = FakeSocket()
    connection = await hub.connect(company, socket, since=head)
    await socket.wait_for(lambda ms: any(m["type"] == "HEARTBEAT" for m in ms))
    assert socket.of("HEARTBEAT")[0]["head_seq"] == head

    before = len(socket.of("HEARTBEAT"))
    await hub.receive(connection, {"type": "PING"})
    await socket.wait_for(lambda ms: sum(m["type"] == "HEARTBEAT" for m in ms) > before)
    await hub.receive(connection, {"type": "ACK", "last_seq": head})
    assert connection.last_acked_seq == head


# --- the real endpoint ---------------------------------------------------------------------


@pytest.fixture
async def server(db_settings, monkeypatch, committed):
    """The real API app on a real port, pointed at the test database."""
    from autora.db.session import dispose_engine
    from autora.infra.settings import get_settings

    monkeypatch.setenv("DATABASE_URL", db_settings.database_url)
    monkeypatch.setenv("API_BEARER_TOKEN", "ws-test-token")
    get_settings.cache_clear()
    await dispose_engine()

    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "api"))
    from autora_api.app import create_app

    config = uvicorn.Config(create_app(), host="127.0.0.1", port=0, log_level="warning")
    instance = uvicorn.Server(config)
    task = asyncio.create_task(instance.serve())
    while not instance.started:
        await asyncio.sleep(0.02)
    port = instance.servers[0].sockets[0].getsockname()[1]
    yield f"ws://127.0.0.1:{port}"
    instance.should_exit = True
    await task
    await dispose_engine()
    get_settings.cache_clear()


async def _recv(ws):
    return json.loads(await asyncio.wait_for(ws.recv(), 5))


async def test_websocket_endpoint(server, committed):
    company = await _company(committed)
    [head] = await _emit(committed, company)
    url = f"{server}/ws/companies/{company}"

    async with websockets.connect(f"{url}?token=wrong&since={head}") as ws:
        assert (await _recv(ws))["code"] == "unauthorized"
        with pytest.raises(websockets.ConnectionClosed) as closed:
            await ws.recv()
        assert closed.value.rcvd.code == CloseCode.UNAUTHORIZED

    async with websockets.connect(
        f"{server}/ws/companies/{uuid.uuid4()}?token=ws-test-token"
    ) as ws:
        assert (await _recv(ws))["code"] == "not_found"

    async with websockets.connect(f"{url}?token=ws-test-token&since={head}") as ws:
        hello = await _recv(ws)
        assert hello["type"] == "HELLO" and hello["mode"] == "live" and hello["head_seq"] == head
        [new] = await _emit(committed, company)
        event = await _recv(ws)
        assert event["type"] == "EVENT" and event["seq"] == new
        await ws.send(json.dumps({"type": "PING"}))
        assert (await _recv(ws))["type"] == "HEARTBEAT"
