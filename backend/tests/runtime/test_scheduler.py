"""T-212: scheduler.

Scheduler uses real commits (it owns its transactions), so these tests use the ``committed``
factory. Each test only asserts on its own schedules and handlers; schedules are disabled on
teardown so later ticks never touch them.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update

from autora.db.models import EventRecord, Schedule
from autora.runtime.scheduler import ScheduleError, Scheduler, create_schedule, next_fire_time
from tests.conftest import unique_company

T0 = datetime(2026, 9, 17, 5, 59, tzinfo=UTC)


class Clock:
    def __init__(self, now: datetime):
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
async def make_schedule(committed):
    created: list[uuid.UUID] = []

    async def _make(handler: str, cron: str = "0 6 * * *", next_run_at: datetime | None = None):
        async with committed() as session:
            company = await unique_company(session, "sched")
            schedule = await create_schedule(
                session,
                company_id=company.id,
                name="cycle.daily_start",
                cron=cron,
                handler=handler,
                now=T0,
            )
            if next_run_at is not None:
                schedule.next_run_at = next_run_at
            await session.commit()
            created.append(schedule.id)
            return schedule

    yield _make
    async with committed() as session:
        await session.execute(
            update(Schedule).where(Schedule.id.in_(created)).values(enabled=False)
        )
        await session.commit()


async def _get(committed, schedule_id) -> Schedule:
    async with committed() as session:
        return await session.get(Schedule, schedule_id)


def _handler_key() -> str:
    return f"test.h{uuid.uuid4().hex[:10]}"


# --- cron --------------------------------------------------------------------------------


def test_next_fire_time_respects_timezone():
    # 06:00 in Taipei is 22:00 UTC the previous day.
    after = datetime(2026, 9, 17, 0, 0, tzinfo=UTC)
    assert next_fire_time("0 6 * * *", "Asia/Taipei", after) == datetime(
        2026, 9, 17, 22, 0, tzinfo=UTC
    )
    assert next_fire_time("0 6 * * *", "UTC", after) == datetime(2026, 9, 17, 6, 0, tzinfo=UTC)


def test_next_fire_time_is_strictly_after():
    at_slot = datetime(2026, 9, 17, 6, 0, tzinfo=UTC)
    assert next_fire_time("0 6 * * *", "UTC", at_slot) == at_slot + timedelta(days=1)


@pytest.mark.parametrize(("cron", "tz"), [("not a cron", "UTC"), ("0 6 * * *", "Mars/Olympus")])
def test_invalid_schedule_rejected(cron, tz):
    with pytest.raises(ScheduleError):
        next_fire_time(cron, tz, T0)


# --- firing ------------------------------------------------------------------------------


async def test_due_schedule_fires_once_and_advances(committed, make_schedule):
    calls = []
    key = _handler_key()
    schedule = await make_schedule(key)
    assert schedule.next_run_at == datetime(2026, 9, 17, 6, 0, tzinfo=UTC)

    clock = Clock(datetime(2026, 9, 17, 6, 0, 5, tzinfo=UTC))
    scheduler = Scheduler(committed, worker_id="w1", clock=clock)

    async def handler(session, sched, scheduled_for):
        calls.append((sched.id, scheduled_for, dict(sched.payload)))

    scheduler.register(key, handler)

    before = Clock(datetime(2026, 9, 17, 5, 0, tzinfo=UTC))
    await Scheduler(committed, worker_id="early", clock=before, _handlers={key: handler}).tick()
    assert calls == [], "not due yet"

    await scheduler.tick()
    await scheduler.tick()  # same instant again: already advanced, must not fire twice

    assert calls == [(schedule.id, datetime(2026, 9, 17, 6, 0, tzinfo=UTC), {})]
    after = await _get(committed, schedule.id)
    assert after.next_run_at == datetime(2026, 9, 18, 6, 0, tzinfo=UTC)
    assert after.last_run_at == clock.now
    assert after.lease_owner is None and after.consecutive_failures == 0

    async with committed() as session:
        fired = await session.scalar(
            select(func.count())
            .select_from(EventRecord)
            .where(
                EventRecord.aggregate_id == schedule.id, EventRecord.event_type == "SCHEDULE_FIRED"
            )
        )
    assert fired == 1


async def test_concurrent_workers_fire_exactly_once(committed, make_schedule):
    key = _handler_key()
    schedule = await make_schedule(key)
    clock = Clock(datetime(2026, 9, 17, 6, 0, 1, tzinfo=UTC))
    calls = []

    async def slow_handler(session, sched, scheduled_for):
        calls.append(sched.id)
        await asyncio.sleep(0.2)

    workers = [
        Scheduler(committed, worker_id=f"w{i}", clock=clock, _handlers={key: slow_handler})
        for i in range(4)
    ]
    await asyncio.gather(*(w.tick() for w in workers))
    assert calls == [schedule.id]


async def test_failed_handler_rolls_back_and_backs_off(committed, make_schedule):
    key = _handler_key()
    schedule = await make_schedule(key)
    clock = Clock(datetime(2026, 9, 17, 6, 0, 1, tzinfo=UTC))
    attempts = []

    async def flaky(session, sched, scheduled_for):
        attempts.append(clock.now)
        sched.payload = {"written_by_handler": True}  # must be rolled back on failure
        if len(attempts) == 1:
            raise RuntimeError("upstream unavailable")

    scheduler = Scheduler(
        committed, worker_id="w1", clock=clock, retry_backoff=timedelta(minutes=1),
        _handlers={key: flaky},
    )  # fmt: skip

    await scheduler.tick()
    failed = await _get(committed, schedule.id)
    assert failed.last_error == "RuntimeError: upstream unavailable"
    assert failed.consecutive_failures == 1
    assert failed.payload == {}, "handler writes roll back with the failure"
    assert failed.next_run_at == schedule.next_run_at, "not advanced"
    assert failed.lease_until == clock.now + timedelta(minutes=1)

    clock.now += timedelta(seconds=30)
    await scheduler.tick()
    assert len(attempts) == 1, "backoff not over yet"

    clock.now += timedelta(seconds=31)
    await scheduler.tick()
    recovered = await _get(committed, schedule.id)
    assert len(attempts) == 2
    assert recovered.last_error is None and recovered.consecutive_failures == 0
    assert recovered.payload == {"written_by_handler": True}
    assert recovered.next_run_at == datetime(2026, 9, 18, 6, 0, tzinfo=UTC)


async def test_missed_runs_coalesce_into_one(committed, make_schedule):
    key = _handler_key()
    three_days_late = datetime(2026, 9, 14, 6, 0, tzinfo=UTC)
    schedule = await make_schedule(key, next_run_at=three_days_late)
    clock = Clock(datetime(2026, 9, 17, 7, 0, tzinfo=UTC))
    calls = []

    async def handler(session, sched, scheduled_for):
        calls.append(scheduled_for)

    await Scheduler(committed, worker_id="w1", clock=clock, _handlers={key: handler}).tick()
    assert calls == [three_days_late]
    after = await _get(committed, schedule.id)
    assert after.next_run_at == datetime(2026, 9, 18, 6, 0, tzinfo=UTC)


async def test_expired_lease_of_crashed_worker_is_taken_over(committed, make_schedule):
    key = _handler_key()
    schedule = await make_schedule(key)
    now = datetime(2026, 9, 17, 6, 0, 1, tzinfo=UTC)
    async with committed() as session:
        await session.execute(
            update(Schedule)
            .where(Schedule.id == schedule.id)
            .values(lease_owner="crashed", lease_until=now - timedelta(seconds=1))
        )
        await session.commit()

    calls = []

    async def handler(session, sched, scheduled_for):
        calls.append(sched.id)

    await Scheduler(committed, worker_id="w2", clock=Clock(now), _handlers={key: handler}).tick()
    assert calls == [schedule.id]


async def test_unknown_handler_is_recorded_as_failure(committed, make_schedule):
    schedule = await make_schedule(_handler_key())
    clock = Clock(datetime(2026, 9, 17, 6, 0, 1, tzinfo=UTC))
    await Scheduler(committed, worker_id="w1", clock=clock).tick()
    after = await _get(committed, schedule.id)
    assert "no handler registered" in after.last_error


def test_duplicate_handler_registration_rejected(committed):
    scheduler = Scheduler(committed, worker_id="w1")

    async def handler(session, sched, scheduled_for):
        pass

    scheduler.register("cycle.start", handler)
    with pytest.raises(ScheduleError, match="already registered"):
        scheduler.register("cycle.start", handler)
