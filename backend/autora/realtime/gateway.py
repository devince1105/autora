"""WebSocket gateway: company events pushed to browsers (T-303, 3d-office/05 §1-2, §4, §7).

``EventHub`` is one per API process. It LISTENs on the outbox channel, and on every
notification (plus a poll every ``poll_interval`` in case a NOTIFY is lost or the listener
connection drops) reads the company's events with ``seq > cursor`` from the database and offers
them to every ``Connection`` of that company. It also forwards ephemeral events (progress,
heartbeats of agents) that arrive on their own channel and are never stored.

Why a connection's stream has no gaps (and clients need no seq arithmetic): ``seq`` is global
across companies, so a company's seqs are not consecutive. But the outbox commits a company's
events in seq order (per-company lock), so ``seq > cursor`` read from the database is always
the complete continuation, and a socket does not lose messages. Only two things can break a
stream, and both are explicit: the send queue overflowing and the client being too far behind.
Both end in SNAPSHOT_REQUIRED; the client re-hydrates and reconnects with the new ``since``.

Connection protocol (server messages; the transport adapter lives in the API):

    HELLO {server_time, head_seq, mode: live|backlog|snapshot_required}
    EVENTS {items} ...  BACKLOG_DONE {head_seq}      catch-up from ``since`` (<= 200 per batch)
    EVENT {...envelope}                              live, in seq order, no duplicates
    EPHEMERAL {event_type, agent_id, run_id, payload, occurred_at}   no seq, may be dropped
    HEARTBEAT {server_time, head_seq}                every ``heartbeat_interval``
    SNAPSHOT_REQUIRED {reason}                       then the server closes the socket
    ERROR {code, message}                            then the server closes the socket

Client messages: ``ACK {last_seq}`` (metrics only) and ``PING`` (answered with HEARTBEAT).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from collections import defaultdict
from collections.abc import Awaitable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from autora.db.models import EventRecord
from autora.runtime.events.outbox import EPHEMERAL_CHANNEL, EVENTS_CHANNEL, to_envelope

log = logging.getLogger("autora.realtime")

SnapshotReason = Literal["gap_too_large", "queue_overflow", "unknown_since"]


class Transport(Protocol):
    async def send_json(self, message: dict[str, Any]) -> None: ...
    async def close(self, code: int = 1000) -> None: ...


class CloseCode:
    SNAPSHOT_REQUIRED = 4000
    UNAUTHORIZED = 4401
    NOT_FOUND = 4404
    SHUTDOWN = 1001


def _now() -> str:
    return datetime.now(UTC).isoformat()


# --- one socket ----------------------------------------------------------------------------


@dataclass(eq=False)
class Connection:
    company_id: uuid.UUID
    transport: Transport
    queue: asyncio.Queue[dict[str, Any]]
    last_sent_seq: int = 0
    last_acked_seq: int | None = None
    closing: SnapshotReason | None = None
    _sender: asyncio.Task[None] | None = None

    def offer(self, message: dict[str, Any], *, droppable: bool = False) -> None:
        """Queue a message without waiting. A full queue drops ephemeral messages and ends the
        stream for everything else (the client must re-hydrate)."""
        if self.closing is not None:
            return
        try:
            self.queue.put_nowait(message)
        except asyncio.QueueFull:
            if droppable:
                return
            self.closing = "queue_overflow"
            while not self.queue.empty():
                self.queue.get_nowait()
            self.queue.put_nowait({"type": "SNAPSHOT_REQUIRED", "reason": "queue_overflow"})


# --- the hub -------------------------------------------------------------------------------


@dataclass
class EventHub:
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    poll_interval: float = 2.0
    heartbeat_interval: float = 15.0
    backlog_max: int = 5000
    batch_size: int = 200
    fetch_limit: int = 500
    queue_size: int = 1000
    listen: bool = True
    """False disables LISTEN entirely (tests of the poll fallback)."""
    _connections: dict[uuid.UUID, set[Connection]] = field(default_factory=lambda: defaultdict(set))
    _cursors: dict[uuid.UUID, int] = field(default_factory=dict)
    _locks: dict[uuid.UUID, asyncio.Lock] = field(default_factory=lambda: defaultdict(asyncio.Lock))
    _tasks: list[asyncio.Task[None]] = field(default_factory=list)
    _pending: set[asyncio.Task[None]] = field(default_factory=set)
    _stopping: bool = False

    # --- lifecycle -------------------------------------------------------------------------

    async def start(self) -> None:
        self._stopping = False
        if self.listen:
            self._tasks.append(asyncio.create_task(self._listen_forever(), name="hub-listen"))
        self._tasks.append(asyncio.create_task(self._poll_forever(), name="hub-poll"))
        self._tasks.append(asyncio.create_task(self._heartbeat_forever(), name="hub-heartbeat"))

    async def stop(self) -> None:
        self._stopping = True
        for task in [*self._tasks, *self._pending]:
            task.cancel()
        await asyncio.gather(*self._tasks, *self._pending, return_exceptions=True)
        self._tasks.clear()
        for connections in list(self._connections.values()):
            for connection in list(connections):
                await self.disconnect(connection, CloseCode.SHUTDOWN)

    # --- connections -----------------------------------------------------------------------

    async def connect(
        self, company_id: uuid.UUID, transport: Transport, since: int | None
    ) -> Connection:
        """Register a socket, send HELLO and its backlog, then stream live. Returns once the
        connection is set up; its sender task keeps running until ``disconnect``."""
        connection = Connection(company_id, transport, asyncio.Queue(maxsize=self.queue_size))
        # Register first, so nothing committed from now on can be missed; the backlog may
        # overlap with the live queue, and the sender drops what it already sent.
        self._connections[company_id].add(connection)
        async with self._locks[company_id]:
            await self._track(company_id)

        head = await self._head(company_id)
        reason = await self._backlog_problem(company_id, since, head)
        mode = "snapshot_required" if reason else ("live" if since == head else "backlog")
        await transport.send_json(
            {"type": "HELLO", "server_time": _now(), "head_seq": head, "mode": mode}
        )
        if reason is not None:
            await transport.send_json({"type": "SNAPSHOT_REQUIRED", "reason": reason})
            await self.disconnect(connection, CloseCode.SNAPSHOT_REQUIRED)
            return connection

        connection.last_sent_seq = since
        if mode == "backlog":
            await self._send_backlog(connection)
        connection._sender = asyncio.create_task(self._send_forever(connection))
        return connection

    async def disconnect(self, connection: Connection, code: int = 1000) -> None:
        self._connections[connection.company_id].discard(connection)
        if not self._connections[connection.company_id]:
            self._connections.pop(connection.company_id, None)
            self._cursors.pop(connection.company_id, None)
        if connection._sender is not None and connection._sender is not asyncio.current_task():
            connection._sender.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await connection._sender
        await self._close(connection, code)

    async def receive(self, connection: Connection, message: dict[str, Any]) -> None:
        """A message from the client."""
        kind = message.get("type")
        if kind == "ACK" and isinstance(message.get("last_seq"), int):
            connection.last_acked_seq = message["last_seq"]
        elif kind == "PING":
            connection.offer(self._heartbeat(connection.company_id))

    def connections(self, company_id: uuid.UUID) -> set[Connection]:
        return set(self._connections.get(company_id, ()))

    # --- sending ---------------------------------------------------------------------------

    async def _send_backlog(self, connection: Connection) -> None:
        while True:
            async with self.session_factory() as session:
                rows = (
                    await session.scalars(
                        select(EventRecord)
                        .where(
                            EventRecord.company_id == connection.company_id,
                            EventRecord.seq > connection.last_sent_seq,
                        )
                        .order_by(EventRecord.seq)
                        .limit(self.batch_size)
                    )
                ).all()
            if not rows:
                break
            items = [to_envelope(row).model_dump(mode="json") for row in rows]
            await connection.transport.send_json({"type": "EVENTS", "items": items})
            connection.last_sent_seq = rows[-1].seq
            if len(rows) < self.batch_size:
                break
        await connection.transport.send_json(
            {"type": "BACKLOG_DONE", "head_seq": connection.last_sent_seq}
        )

    async def _send_forever(self, connection: Connection) -> None:
        try:
            while True:
                message = await connection.queue.get()
                if message["type"] == "EVENT":
                    if message["seq"] <= connection.last_sent_seq:
                        continue  # already sent in the backlog
                    connection.last_sent_seq = message["seq"]
                await connection.transport.send_json(message)
                if message["type"] == "SNAPSHOT_REQUIRED":
                    await self.disconnect(connection, CloseCode.SNAPSHOT_REQUIRED)
                    return
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the socket went away; clean up quietly
            log.debug("sender for %s stopped", connection.company_id, exc_info=True)
            await self.disconnect(connection)

    async def _close(self, connection: Connection, code: int) -> None:
        with contextlib.suppress(Exception):
            await connection.transport.close(code)

    # --- reading the log -------------------------------------------------------------------

    async def _track(self, company_id: uuid.UUID) -> None:
        if company_id not in self._cursors:
            self._cursors[company_id] = await self._head(company_id)

    async def _head(self, company_id: uuid.UUID) -> int:
        async with self.session_factory() as session:
            return await session.scalar(
                select(func.coalesce(func.max(EventRecord.seq), 0)).where(
                    EventRecord.company_id == company_id
                )
            )

    async def _backlog_problem(
        self, company_id: uuid.UUID, since: int | None, head: int
    ) -> SnapshotReason | None:
        if since is None or since < 0 or since > head:
            return "unknown_since"
        if since == head:
            return None
        async with self.session_factory() as session:
            behind = await session.scalar(
                select(func.count()).select_from(
                    select(EventRecord.seq)
                    .where(EventRecord.company_id == company_id, EventRecord.seq > since)
                    .limit(self.backlog_max + 1)
                    .subquery()
                )
            )
        return "gap_too_large" if behind > self.backlog_max else None

    async def fetch(self, company_id: uuid.UUID) -> int:
        """Deliver the company's events after its cursor to its connections. Returns how many."""
        if not self._connections.get(company_id):
            return 0
        delivered = 0
        async with self._locks[company_id]:
            await self._track(company_id)
            while True:
                cursor = self._cursors[company_id]
                async with self.session_factory() as session:
                    rows = (
                        await session.scalars(
                            select(EventRecord)
                            .where(EventRecord.company_id == company_id, EventRecord.seq > cursor)
                            .order_by(EventRecord.seq)
                            .limit(self.fetch_limit)
                        )
                    ).all()
                if not rows:
                    return delivered
                for row in rows:
                    message = {"type": "EVENT", **to_envelope(row).model_dump(mode="json")}
                    for connection in list(self._connections[company_id]):
                        connection.offer(message)
                    # Let senders drain between events: a burst must not overflow a socket
                    # that keeps up; only one that cannot send should hit the queue limit.
                    await asyncio.sleep(0)
                self._cursors[company_id] = rows[-1].seq
                delivered += len(rows)
                if len(rows) < self.fetch_limit:
                    return delivered

    def forward_ephemeral(self, raw: str) -> None:
        try:
            data = json.loads(raw)
            company_id = uuid.UUID(data["company_id"])
        except (ValueError, KeyError, TypeError):
            log.warning("ignored malformed ephemeral notification")
            return
        message = {
            "type": "EPHEMERAL",
            "event_type": data.get("event_type"),
            "agent_id": data.get("agent_id"),
            "run_id": data.get("run_id"),
            "occurred_at": data.get("occurred_at"),
            "payload": data.get("payload", {}),
        }
        for connection in list(self._connections.get(company_id, ())):
            connection.offer(message, droppable=True)

    def _heartbeat(self, company_id: uuid.UUID) -> dict[str, Any]:
        return {
            "type": "HEARTBEAT",
            "server_time": _now(),
            "head_seq": self._cursors.get(company_id, 0),
        }

    # --- background loops ------------------------------------------------------------------

    def _schedule_fetch(self, company_id: uuid.UUID) -> None:
        task = asyncio.create_task(self._safe(self.fetch(company_id)))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _safe(self, work: Awaitable[Any]) -> None:
        try:
            await work
        except Exception:  # noqa: BLE001 - the next poll retries
            log.exception("realtime fetch failed")

    async def _listen_forever(self) -> None:
        while not self._stopping:
            try:
                async with self.engine.connect() as conn:
                    raw = (await conn.get_raw_connection()).driver_connection
                    lost = asyncio.Event()

                    def on_event(_c, _pid, _channel, payload: str) -> None:
                        with contextlib.suppress(ValueError):
                            self._schedule_fetch(uuid.UUID(payload.split(":", 1)[0]))

                    def on_ephemeral(_c, _pid, _channel, payload: str) -> None:
                        self.forward_ephemeral(payload)

                    await raw.add_listener(EVENTS_CHANNEL, on_event)
                    await raw.add_listener(EPHEMERAL_CHANNEL, on_ephemeral)
                    raw.add_termination_listener(lambda _c, lost=lost: lost.set())
                    await lost.wait()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - reconnect; polling covers the gap
                log.warning("realtime LISTEN connection failed; retrying", exc_info=True)
            await asyncio.sleep(1.0)

    async def _poll_forever(self) -> None:
        while True:
            await asyncio.sleep(self.poll_interval)
            for company_id in list(self._connections):
                await self._safe(self.fetch(company_id))

    async def _heartbeat_forever(self) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_interval)
            for company_id, connections in list(self._connections.items()):
                message = self._heartbeat(company_id)
                for connection in list(connections):
                    connection.offer(message, droppable=True)
