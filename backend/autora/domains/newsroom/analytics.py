"""Readers per article and day (T-516; platform/05 §1, platform/08, 3d-office/06 §3).

The public site's beacons (T-515) land in ``analytics_events``. Every hour the collector
(schedule ``newsroom.collect_analytics``) recounts the days that can still change — yesterday and
today, UTC: a beacon is filed under the day it arrives — into ``analytics_daily``, one row per
article, language and day. A recount, not an increment: running it twice, or late, gives the same
numbers. When an article's numbers for a day changed, it emits ANALYTICS_DAILY_UPDATED.

Raw beacons older than 30 days are deleted (platform/08): their days were final long before.

The schedule is created when a company first publishes (``ensure_analytics_schedule``, from the
publisher). The measurement points of the workflow sketch (+1h, +24h, +7d after publication,
platform/05 §2) are not separate jobs: the hourly recount covers them (D-014).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete, func, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from autora.db.models import Schedule
from autora.domains.newsroom.events import AnalyticsDailyUpdated
from autora.domains.newsroom.models import AnalyticsDaily, AnalyticsEvent, AnalyticsEventType
from autora.runtime.actor import Actor
from autora.runtime.events.outbox import emit
from autora.runtime.events.schema import new_event
from autora.runtime.scheduler import Handler, create_schedule

ANALYTICS_SCHEDULE = "newsroom.collect_analytics"
ANALYTICS_CRON = "7 * * * *"
RAW_RETENTION_DAYS = 30

Key = tuple[uuid.UUID, str, date]
"""(article_id, lang, day)"""


async def ensure_analytics_schedule(
    session: AsyncSession, company_id: uuid.UUID, *, now: datetime | None = None
) -> Schedule:
    existing = await session.scalar(
        select(Schedule).where(
            Schedule.company_id == company_id, Schedule.name == ANALYTICS_SCHEDULE
        )
    )
    return existing or await create_schedule(
        session,
        company_id=company_id,
        name=ANALYTICS_SCHEDULE,
        cron=ANALYTICS_CRON,
        handler=ANALYTICS_SCHEDULE,
        now=now,
    )


@dataclass(frozen=True)
class Counts:
    views: int = 0
    uniques: int = 0
    read_complete: int = 0


@dataclass(frozen=True)
class CollectOutcome:
    changed: dict[Key, Counts]
    """The rows whose numbers changed (or appeared)."""
    deleted: int
    """Raw beacons deleted (older than the retention)."""


@dataclass
class AnalyticsCollector:
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    actor: Actor = field(default_factory=lambda: Actor.system("newsroom.analytics"))

    async def collect(self, session: AsyncSession, company_id: uuid.UUID) -> CollectOutcome:
        today = self.clock().astimezone(UTC).date()
        days = (today - timedelta(days=1), today)
        counted = await self._count(session, company_id, days)
        stored = {
            (row.article_id, row.lang, row.day): Counts(row.views, row.uniques, row.read_complete)
            for row in (
                await session.scalars(
                    select(AnalyticsDaily).where(
                        AnalyticsDaily.company_id == company_id, AnalyticsDaily.day.in_(days)
                    )
                )
            ).all()
        }
        changed = {key: counts for key, counts in counted.items() if stored.get(key) != counts}
        for (article_id, lang, day), counts in changed.items():
            values = {
                "views": counts.views,
                "uniques": counts.uniques,
                "read_complete": counts.read_complete,
            }
            await session.execute(
                insert(AnalyticsDaily)
                .values(company_id=company_id, article_id=article_id, lang=lang, day=day, **values)
                .on_conflict_do_update(
                    index_elements=["article_id", "lang", "day"],
                    set_={**values, "updated_at": func.now()},
                )
            )
        await self._announce(session, company_id, changed)
        deleted = await session.execute(
            delete(AnalyticsEvent).where(
                AnalyticsEvent.company_id == company_id,
                AnalyticsEvent.day < today - timedelta(days=RAW_RETENTION_DAYS),
            )
        )
        return CollectOutcome(changed=changed, deleted=deleted.rowcount or 0)

    async def _count(
        self, session: AsyncSession, company_id: uuid.UUID, days: tuple[date, ...]
    ) -> dict[Key, Counts]:
        e = AnalyticsEvent
        rows = (
            await session.execute(
                select(
                    e.article_id,
                    e.lang,
                    e.day,
                    func.count().filter(e.event_type == AnalyticsEventType.VIEW),
                    func.count(func.distinct(e.session_hash)),
                    func.count().filter(e.event_type == AnalyticsEventType.READ_COMPLETE),
                )
                .where(e.company_id == company_id, e.day.in_(days))
                .group_by(e.article_id, e.lang, e.day)
            )
        ).all()
        return {
            (article_id, lang, day): Counts(views, uniques, read_complete)
            for article_id, lang, day, views, uniques, read_complete in rows
        }

    async def _announce(
        self, session: AsyncSession, company_id: uuid.UUID, changed: dict[Key, Counts]
    ) -> None:
        """One event per article and day that changed, with that day's totals over languages."""
        touched = sorted({(article_id, day) for article_id, _, day in changed})
        if not touched:
            return
        rows = (
            await session.scalars(
                select(AnalyticsDaily)
                .where(tuple_(AnalyticsDaily.article_id, AnalyticsDaily.day).in_(touched))
                .execution_options(populate_existing=True)  # rows loaded above are stale now
            )
        ).all()
        for article_id, day in touched:
            langs = [r for r in rows if r.article_id == article_id and r.day == day]
            await emit(
                session,
                new_event(
                    AnalyticsDailyUpdated(
                        article_id=article_id,
                        date=day,
                        views=sum(r.views for r in langs),
                        uniques=sum(r.uniques for r in langs),
                        read_complete=sum(r.read_complete for r in langs),
                        langs={r.lang: r.views for r in sorted(langs, key=lambda r: r.lang)},
                    ),
                    company_id=company_id,
                    actor=self.actor,
                    aggregate_type="article",
                    aggregate_id=article_id,
                ),
            )

    def schedule_handler(self) -> Handler:
        async def handler(session: AsyncSession, schedule, scheduled_for: datetime) -> None:
            await self.collect(session, schedule.company_id)

        return handler
