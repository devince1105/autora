"""Scheduler: fires time-based triggers exactly once across any number of workers (T-212).

Why not APScheduler (as the tech-stack draft said): the ``schedules`` table already holds
``next_run_at`` and the lease. An in-memory job store would be a second copy of that state that
can drift from it. Polling the table with ``FOR UPDATE SKIP LOCKED`` is the same mechanism the task
queue uses, survives restarts for free, and needs no coordination between workers.

One tick:
1. Claim due schedules (short transaction): set a lease so no other worker picks them up.
2. For each claimed schedule, in one transaction: run the handler, advance ``next_run_at``,
   emit ``SCHEDULE_FIRED``, clear the lease. Handler writes and the event commit together.
3. If the handler fails: roll back, keep the lease until ``now + retry_backoff`` and record the
   error, so the next attempt happens later instead of every tick.

Misfire policy is "coalesce": after downtime a schedule fires once, then resumes its cadence; it
does not replay every missed slot. A crashed worker's lease simply expires.

Handlers must be quick and idempotent (they typically create a cycle or enqueue tasks).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from autora.db.models import Schedule
from autora.runtime.actor import Actor
from autora.runtime.events import catalog as ev
from autora.runtime.events.outbox import emit
from autora.runtime.events.schema import new_event

log = logging.getLogger(__name__)

Handler = Callable[[AsyncSession, Schedule, datetime], Awaitable[None]]
"""(session, schedule, scheduled_for). Runs inside the transaction that advances the schedule."""


class ScheduleError(Exception):
    pass


def next_fire_time(cron: str, timezone: str, after: datetime) -> datetime:
    """First cron slot strictly after ``after``, returned in UTC."""
    if not croniter.is_valid(cron):
        raise ScheduleError(f"invalid cron expression: {cron!r}")
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise ScheduleError(f"unknown timezone: {timezone!r}") from None
    local = after.astimezone(zone)
    return croniter(cron, local).get_next(datetime).astimezone(UTC)


def validate(cron: str, timezone: str) -> None:
    next_fire_time(cron, timezone, datetime.now(UTC))


@dataclass
class Scheduler:
    session_factory: async_sessionmaker[AsyncSession]
    worker_id: str
    lease: timedelta = timedelta(minutes=5)
    retry_backoff: timedelta = timedelta(minutes=1)
    batch_size: int = 20
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    _handlers: dict[str, Handler] = field(default_factory=dict)

    @property
    def actor(self) -> Actor:
        return Actor.system("scheduler")

    def register(self, key: str, handler: Handler) -> None:
        if key in self._handlers:
            raise ScheduleError(f"handler {key!r} already registered")
        self._handlers[key] = handler

    async def tick(self) -> list[str]:
        """Fire every due schedule once. Returns names of schedules that fired successfully."""
        fired = []
        for schedule_id, scheduled_for in await self._claim_due():
            name = await self._fire(schedule_id, scheduled_for)
            if name is not None:
                fired.append(name)
        return fired

    async def _claim_due(self) -> list[tuple[object, datetime]]:
        now = self.clock()
        async with self.session_factory() as session:
            rows = (
                await session.scalars(
                    select(Schedule)
                    .where(
                        Schedule.enabled,
                        Schedule.next_run_at <= now,
                        or_(Schedule.lease_until.is_(None), Schedule.lease_until < now),
                    )
                    .order_by(Schedule.next_run_at)
                    .limit(self.batch_size)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for row in rows:
                row.lease_owner = self.worker_id
                row.lease_until = now + self.lease
            claimed = [(row.id, row.next_run_at) for row in rows]
            await session.commit()
        return claimed

    async def _fire(self, schedule_id, scheduled_for: datetime) -> str | None:
        try:
            async with self.session_factory() as session:
                schedule = await session.get(Schedule, schedule_id, with_for_update=True)
                if schedule is None or schedule.lease_owner != self.worker_id:
                    return None  # lease lost (expired and taken over); the new owner fires it
                handler = self._handlers.get(schedule.handler)
                if handler is None:
                    raise ScheduleError(f"no handler registered for {schedule.handler!r}")

                await handler(session, schedule, scheduled_for)

                now = self.clock()
                schedule.last_run_at = now
                schedule.next_run_at = next_fire_time(
                    schedule.cron, schedule.timezone, max(now, scheduled_for)
                )
                schedule.lease_owner = None
                schedule.lease_until = None
                schedule.last_error = None
                schedule.consecutive_failures = 0
                await emit(
                    session,
                    new_event(
                        ev.ScheduleFired(schedule_name=schedule.name, scheduled_for=scheduled_for),
                        company_id=schedule.company_id,
                        actor=self.actor,
                        aggregate_type="schedule",
                        aggregate_id=schedule.id,
                    ),
                )
                name = schedule.name
                await session.commit()
                return name
        except Exception as exc:  # noqa: BLE001 - any handler failure is recorded, not raised
            log.exception("schedule %s failed", schedule_id)
            await self._record_failure(schedule_id, exc)
            return None

    async def _record_failure(self, schedule_id, exc: Exception) -> None:
        async with self.session_factory() as session:
            schedule = await session.get(Schedule, schedule_id, with_for_update=True)
            if schedule is None or schedule.lease_owner != self.worker_id:
                return
            schedule.lease_until = self.clock() + self.retry_backoff
            schedule.last_error = f"{type(exc).__name__}: {exc}"[:2000]
            schedule.consecutive_failures += 1
            await session.commit()


async def create_schedule(
    session: AsyncSession,
    *,
    company_id,
    name: str,
    cron: str,
    handler: str,
    timezone: str = "UTC",
    payload: dict | None = None,
    now: datetime | None = None,
) -> Schedule:
    validate(cron, timezone)
    schedule = Schedule(
        company_id=company_id,
        name=name,
        cron=cron,
        timezone=timezone,
        handler=handler,
        payload=payload or {},
        next_run_at=next_fire_time(cron, timezone, now or datetime.now(UTC)),
    )
    session.add(schedule)
    await session.flush()
    return schedule
