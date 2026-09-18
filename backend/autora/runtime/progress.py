"""Live progress of agent runs: AGENT_STEP_PROGRESS, never stored (T-304, 3d-office/05 §1, §7).

The runner reports after every model call (tokens used so far in the run) and after every tool
call (plus the tool's own progress, e.g. "sources 12/40"). ``ProgressPublisher`` sends these as
ephemeral NOTIFYs that the WebSocket gateway forwards; nothing is written to ``events``.

Rate: at most one message per run per ``min_interval`` (05 §7: <= 1/s). Reports in between are
coalesced: the latest one is sent when the interval has passed, so the newest value is never
lost while the run is going. When the run ends the pending one is dropped, because the run's
persisted events (COMPLETED, FAILED...) supersede it.

Publishing is best-effort: a failure is logged and never fails the run.

The model gateway does not stream yet, so there is no per-token progress inside one call.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from autora.runtime.events.outbox import publish_ephemeral
from autora.runtime.events.schema import EventEnvelope

log = logging.getLogger("autora.progress")


@dataclass
class ProgressPublisher:
    session_factory: async_sessionmaker[AsyncSession]
    min_interval: float = 1.0
    clock: Callable[[], float] = time.monotonic
    _last_sent: dict[uuid.UUID, float] = field(default_factory=dict)
    _pending: dict[uuid.UUID, EventEnvelope] = field(default_factory=dict)
    _timers: dict[uuid.UUID, asyncio.Task[None]] = field(default_factory=dict)
    sent: int = 0
    """How many were actually published (metrics, tests)."""

    async def report(self, run_id: uuid.UUID, envelope: EventEnvelope) -> None:
        last = self._last_sent.get(run_id)
        now = self.clock()
        if last is None or now - last >= self.min_interval:
            self._pending.pop(run_id, None)
            await self._send(run_id, envelope)
            return
        self._pending[run_id] = envelope  # newest wins
        if run_id not in self._timers:
            delay = self.min_interval - (now - last)
            self._timers[run_id] = asyncio.create_task(self._flush_later(run_id, delay))

    async def finish(self, run_id: uuid.UUID) -> None:
        """The run ended: drop what is pending and forget the run."""
        self._pending.pop(run_id, None)
        self._last_sent.pop(run_id, None)
        timer = self._timers.pop(run_id, None)
        if timer is not None and timer is not asyncio.current_task():
            timer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await timer

    async def _flush_later(self, run_id: uuid.UUID, delay: float) -> None:
        await asyncio.sleep(max(delay, 0.0))
        self._timers.pop(run_id, None)
        envelope = self._pending.pop(run_id, None)
        if envelope is not None:
            await self._send(run_id, envelope)

    async def _send(self, run_id: uuid.UUID, envelope: EventEnvelope) -> None:
        self._last_sent[run_id] = self.clock()
        try:
            async with self.session_factory() as session:
                await publish_ephemeral(session, envelope)
                await session.commit()
            self.sent += 1
        except Exception:  # noqa: BLE001 - live progress is best-effort
            log.warning("could not publish progress for run %s", run_id, exc_info=True)
