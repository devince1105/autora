"""T-516: the analytics collector — beacons recounted hourly into analytics_daily, with events."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from autora.app import build_scheduler
from autora.db.models import EventRecord, Schedule
from autora.domains.newsroom.analytics import (
    ANALYTICS_CRON,
    ANALYTICS_SCHEDULE,
    AnalyticsCollector,
    Counts,
)
from autora.domains.newsroom.models import AnalyticsDaily, AnalyticsEvent, AnalyticsEventType
from autora.domains.newsroom.site import record_beacon

NOW = datetime(2026, 9, 21, 10, 30, tzinfo=UTC)
TODAY = NOW.date()
YESTERDAY = TODAY - timedelta(days=1)
VIEW, READ = AnalyticsEventType.VIEW, AnalyticsEventType.READ_COMPLETE


def sid(name: str) -> str:
    return (name * 32)[:32].encode().hex()[:32]


async def _beacons(room, article_id, *beacons):
    async with room.committed() as session:
        for lang, kind, session_name, when in beacons:
            await record_beacon(
                session,
                article_id=article_id,
                lang=lang,
                event_type=kind,
                session_hash=sid(session_name),
                now=when,
            )
        await session.commit()


async def _collect(room, now=NOW):
    async with room.committed() as session:
        outcome = await AnalyticsCollector(clock=lambda: now).collect(session, room.company.id)
        await session.commit()
    return outcome


async def _rows(room):
    async with room.committed() as session:
        rows = (
            await session.scalars(
                select(AnalyticsDaily).where(AnalyticsDaily.company_id == room.company.id)
            )
        ).all()
    return {(r.lang, r.day): (r.views, r.uniques, r.read_complete) for r in rows}


async def _updates(room):
    async with room.committed() as session:
        return (
            await session.scalars(
                select(EventRecord.payload)
                .where(
                    EventRecord.company_id == room.company.id,
                    EventRecord.event_type == "ANALYTICS_DAILY_UPDATED",
                )
                .order_by(EventRecord.id)
            )
        ).all()


async def test_publishing_starts_the_hourly_collection(newsroom_room):
    room = newsroom_room
    await room.publish()
    async with room.committed() as session:
        [schedule] = (
            await session.scalars(
                select(Schedule).where(
                    Schedule.company_id == room.company.id, Schedule.name == ANALYTICS_SCHEDULE
                )
            )
        ).all()
    assert schedule.cron == ANALYTICS_CRON and schedule.handler == ANALYTICS_SCHEDULE
    scheduler = build_scheduler(None, room.committed, "test")
    assert ANALYTICS_SCHEDULE in scheduler._handlers


async def test_readers_are_counted_per_article_language_and_day(newsroom_room):
    room = newsroom_room
    article_id = uuid.UUID(await room.publish())
    earlier = NOW - timedelta(hours=2)
    await _beacons(
        room,
        article_id,
        ("en", VIEW, "a", earlier),
        ("en", VIEW, "b", earlier),
        ("en", VIEW, "c", NOW),
        ("en", READ, "a", earlier),
        ("en", READ, "b", NOW),
        ("en", VIEW, "a", NOW),  # a repeat: dropped by the beacon already
        ("zh-TW", VIEW, "a", NOW),
        ("en", VIEW, "d", NOW - timedelta(days=1)),
    )
    outcome = await _collect(room)
    assert await _rows(room) == {
        ("en", TODAY): (3, 3, 2),
        ("zh-TW", TODAY): (1, 1, 0),
        ("en", YESTERDAY): (1, 1, 0),
    }
    assert outcome.changed[(article_id, "en", TODAY)] == Counts(3, 3, 2)
    today, yesterday = sorted(await _updates(room), key=lambda p: p["date"], reverse=True)
    assert today == {
        "article_id": str(article_id),
        "date": TODAY.isoformat(),
        "views": 4,
        "uniques": 4,
        "read_complete": 2,
        "langs": {"en": 3, "zh-TW": 1},
    }
    assert yesterday["date"] == YESTERDAY.isoformat() and yesterday["views"] == 1

    # a recount changes nothing and says nothing
    again = await _collect(room)
    assert again.changed == {} and len(await _updates(room)) == 2

    # a new reader: only that row changes, one event with the day's new totals
    await _beacons(room, article_id, ("zh-TW", VIEW, "e", NOW))
    later = await _collect(room, NOW + timedelta(hours=1))
    assert list(later.changed) == [(article_id, "zh-TW", TODAY)]
    assert (await _rows(room))[("zh-TW", TODAY)] == (2, 2, 0)
    last = (await _updates(room))[-1]
    assert last["views"] == 5 and last["langs"] == {"en": 3, "zh-TW": 2}


async def test_closed_days_stay_and_old_beacons_go(newsroom_room):
    room = newsroom_room
    article_id = uuid.UUID(await room.publish())
    await _beacons(
        room,
        article_id,
        ("en", VIEW, "a", NOW - timedelta(days=1)),
        ("en", VIEW, "b", NOW - timedelta(days=30)),
        ("en", VIEW, "c", NOW - timedelta(days=31)),
    )
    first = await _collect(room)
    assert set(await _rows(room)) == {("en", YESTERDAY)}  # older days are not recounted
    assert first.deleted == 1  # 31 days old: gone; 30 days: kept
    async with room.committed() as session:
        days = (
            await session.scalars(
                select(AnalyticsEvent.day).where(AnalyticsEvent.article_id == article_id)
            )
        ).all()
    assert sorted(days) == [TODAY - timedelta(days=30), YESTERDAY]

    # the next day, yesterday's row is final: new days count, it stays as it was
    tomorrow = await _collect(room, NOW + timedelta(days=2))
    assert tomorrow.changed == {} and (await _rows(room))[("en", YESTERDAY)] == (1, 1, 0)


async def test_companies_are_counted_apart(newsroom_room, committed):
    room = newsroom_room
    article_id = uuid.UUID(await room.publish())
    await _beacons(room, article_id, ("en", VIEW, "a", NOW))
    async with committed() as session:
        other = await AnalyticsCollector(clock=lambda: NOW).collect(session, uuid.uuid4())
    assert other.changed == {}
    assert (await _collect(room)).changed == {(article_id, "en", TODAY): Counts(1, 1, 0)}
