"""Sources and the poller (T-501, logs/platform/05_NEWSROOM_DOMAIN.md §1).

A **source** is polled on its own interval; each entry it lists becomes a **source item** once.
The poller runs from the scheduler (T-212): one ``newsroom.poll_sources`` schedule per company
fires every few minutes and polls that company's sources whose ``next_poll_at`` has come.

One poll happens in two halves, so no event is written (and the company's event lock is not
taken) while waiting on the network:
1. gather: fetch every due source (feed, URL list, or search) concurrently, no writes;
2. apply: in the schedule's transaction, insert the new items (a unique constraint per source on
   ``external_id`` and on ``content_hash`` makes a re-listed entry a no-op), emit
   SOURCE_ITEM_DISCOVERED for each new one and SOURCE_POLLED for the source, and move
   ``next_poll_at`` on.

A failed poll is recorded (SOURCE_POLLED with ``error``) and retried at the next interval; after
``pause_after`` failures in a row the source is paused (SOURCE_PAUSED) until someone resumes it.
Items are candidates for stories (T-504), not evidence: nothing here reads the linked pages.
Duplicates *across* sources (two feeds linking the same article) are left to story clustering.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from autora.db.models import Schedule
from autora.domains.newsroom.events import SourceItemDiscovered, SourcePaused, SourcePolled
from autora.domains.newsroom.feeds import FeedEntry, FeedError, parse_feed
from autora.domains.newsroom.models import Source, SourceItem, SourceKind, SourceStatus
from autora.infra.http import FetchError, PageFetcher
from autora.infra.search import SearchError, SearchProvider
from autora.runtime.actor import Actor
from autora.runtime.events.outbox import emit
from autora.runtime.events.schema import EventPayload, new_event
from autora.runtime.scheduler import Handler, create_schedule

POLL_SCHEDULE = "newsroom.poll_sources"
"""The schedule's name and its handler's key."""
POLL_CRON = "*/5 * * * *"
_TRACKING = re.compile(r"^(utm_.*|fbclid|gclid|mc_cid|mc_eid|ref|ref_src)$", re.IGNORECASE)
_SPACE = re.compile(r"\s+")


class SourceConfigError(ValueError):
    pass


# --- identity of an item ---------------------------------------------------------------------


def canonical_url(url: str) -> str:
    """The same page written differently maps to one URL: lower-case scheme and host, no
    default port, no fragment, no tracking parameters, no trailing slash (except the root)."""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    port = parts.port
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        host = f"{host}:{port}"
    query = urlencode(
        [
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not _TRACKING.match(k)
        ]
    )
    path = parts.path or "/"
    if len(path) > 1:
        path = path.rstrip("/")
    return urlunsplit((scheme, host, path, query, ""))


TITLE_PREFIX = "title_prefix"
"""``config.title_prefix``: put before every item's title. A feed whose entries all have the
same title — SEC's is "13F-HR - Quarterly report filed by institutional managers" for everybody
— needs it to say whose filing it is (D-036)."""

OWN_STORY = "own_story"
"""``config.own_story``: every item of this source is a story of its own, never merged into a
similar one by the story desk (only the same URL again joins). For filings: this quarter's 13F
looks exactly like last quarter's and must not join the story already written (D-036)."""


MAX_AGE_DAYS = "max_age_days"
"""``config.max_age_days``: skip entries published longer ago than this. A feed lists its history
— SEC's lists a filer's last ten filings — and adding such a source would otherwise turn years
of it into stories on the first poll. Entries without a date are kept (D-036)."""


PRIMARY = "primary"
"""``config.primary``: the source *is* the record — a filing, not a report of one. A story from
it needs no second source to be believed, so the story desk scores its corroboration as full
(D-036). Without it a 13F, which only SEC publishes, ranks below any press release two feeds
happened to carry."""


SECTION = "section"
"""``config.section``: which part of the site a story from this source belongs in (D-047) — one
of ``SECTIONS``. A story's section is the one most of its items' sources name; a source without
one adds nothing, and a story none of whose sources name one is only on the front page."""

SECTIONS = ("holdings", "ai", "tw", "us", "crypto")
"""The site's sections: big investors' filings, AI and tech, Taiwan stocks, US stocks, crypto."""


def content_hash(url: str, title: str) -> str:
    normalized = _SPACE.sub(" ", title).strip().lower()
    return hashlib.sha256(f"{canonical_url(url)}\n{normalized}".encode()).hexdigest()


# --- creating sources ------------------------------------------------------------------------


def _validate(kind: SourceKind, url: str | None, config: dict[str, Any]) -> None:
    if kind is SourceKind.RSS:
        if not url or not url.startswith(("http://", "https://")):
            raise SourceConfigError("an rss source needs an http(s) feed url")
    elif kind is SourceKind.URL_LIST:
        urls = config.get("urls")
        if (
            not isinstance(urls, list)
            or not urls
            or not all(isinstance(u, str) and u.startswith(("http://", "https://")) for u in urls)
        ):
            raise SourceConfigError("a url_list source needs config.urls: a list of http(s) URLs")
    elif kind is SourceKind.SEARCH_QUERY:
        query = config.get("query")
        if not isinstance(query, str) or not query.strip():
            raise SourceConfigError("a search_query source needs config.query")
        k = config.get("k", 10)
        if not isinstance(k, int) or not 1 <= k <= 10:
            raise SourceConfigError("config.k must be 1-10")
    age = config.get(MAX_AGE_DAYS)
    if age is not None and (not isinstance(age, int) or isinstance(age, bool) or age < 1):
        raise SourceConfigError("config.max_age_days must be a whole number of days, 1 or more")
    section = config.get(SECTION)
    if section is not None and section not in SECTIONS:
        raise SourceConfigError(f"config.section must be one of {', '.join(SECTIONS)}")


async def ensure_newsroom_schedules(
    session: AsyncSession, company_id: uuid.UUID, *, now: datetime | None = None
) -> list[Schedule]:
    """The company's poll schedule and, two minutes behind it, the story clustering (T-504); and
    the refresh of its investors' 13F positions for the stock pages (D-049)."""
    from autora.domains.newsroom.holdings import HOLDINGS_CRON, HOLDINGS_SCHEDULE
    from autora.domains.newsroom.stories import CLUSTER_CRON, CLUSTER_SCHEDULE

    schedules = []
    for name, cron in (
        (POLL_SCHEDULE, POLL_CRON),
        (CLUSTER_SCHEDULE, CLUSTER_CRON),
        (HOLDINGS_SCHEDULE, HOLDINGS_CRON),
    ):
        existing = await session.scalar(
            select(Schedule).where(Schedule.company_id == company_id, Schedule.name == name)
        )
        schedules.append(
            existing
            or await create_schedule(
                session, company_id=company_id, name=name, cron=cron, handler=name, now=now
            )
        )
    return schedules


async def add_source(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    name: str,
    kind: SourceKind | str,
    url: str | None = None,
    config: dict[str, Any] | None = None,
    trust_level: Decimal | float = Decimal("0.5"),
    language: str | None = None,
    poll_interval_seconds: int = 3600,
    now: datetime | None = None,
) -> Source:
    """Add a source (polled from ``now`` on) and make sure the company's newsroom schedules
    exist (polling, story clustering)."""
    kind = SourceKind(kind)
    config = dict(config or {})
    _validate(kind, url, config)
    source = Source(
        company_id=company_id,
        name=name,
        kind=kind.value,
        url=url,
        config=config,
        trust_level=Decimal(str(trust_level)),
        language=language,
        poll_interval_seconds=poll_interval_seconds,
        next_poll_at=now or datetime.now(UTC),
    )
    session.add(source)
    await session.flush()
    await ensure_newsroom_schedules(session, company_id, now=now)
    return source


# --- polling ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Gathered:
    entries: list[FeedEntry]
    cost_usd: Decimal | None = None
    error: str | None = None


@dataclass(frozen=True)
class PollOutcome:
    source_id: uuid.UUID
    new_item_ids: list[uuid.UUID]
    seen: int
    error: str | None
    paused: bool


@dataclass
class SourcePoller:
    fetcher: PageFetcher
    search: SearchProvider
    max_items: int = 50
    """Per poll, newest first: a feed's first poll does not flood the timeline."""
    pause_after: int = 5
    batch_size: int = 20
    concurrency: int = 4
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    actor: Actor = field(default_factory=lambda: Actor.system("newsroom.poller"))

    async def poll_due(self, session: AsyncSession, company_id: uuid.UUID) -> list[PollOutcome]:
        now = self.clock()
        sources = (
            await session.scalars(
                select(Source)
                .where(
                    Source.company_id == company_id,
                    Source.status == SourceStatus.ACTIVE.value,
                    Source.next_poll_at <= now,
                )
                .order_by(Source.next_poll_at)
                .limit(self.batch_size)
                .with_for_update(skip_locked=True)
            )
        ).all()
        return await self._poll(session, sources)

    async def poll(self, session: AsyncSession, source: Source) -> PollOutcome:
        """Poll one source now, whatever its schedule (tests, a "poll now" button)."""
        [outcome] = await self._poll(session, [source])
        return outcome

    async def _poll(self, session: AsyncSession, sources: Sequence[Source]) -> list[PollOutcome]:
        gate = asyncio.Semaphore(self.concurrency)

        async def gather_one(source: Source) -> _Gathered:
            async with gate:
                return await self._gather(source)

        gathered = await asyncio.gather(*(gather_one(s) for s in sources))
        now = self.clock()
        return [
            await self._apply(session, s, g, now) for s, g in zip(sources, gathered, strict=True)
        ]

    async def _gather(self, source: Source) -> _Gathered:
        try:
            if source.kind == SourceKind.RSS:
                page = await self.fetcher.fetch(source.url or "")
                return _Gathered(parse_feed(page.body))
            if source.kind == SourceKind.URL_LIST:
                # a URL is its own id and title: written two ways, it is still one page
                urls = dict.fromkeys(canonical_url(u) for u in source.config.get("urls", []))
                return _Gathered(
                    [
                        FeedEntry(external_id=u, url=u, title=u, summary=None, published_at=None)
                        for u in urls
                    ]
                )
            config = source.config
            response = await self.search.search(
                config["query"], k=int(config.get("k", 10)), recency_days=config.get("recency_days")
            )
            entries = [
                FeedEntry(
                    external_id=canonical_url(r.url),
                    url=r.url,
                    title=r.title,
                    summary=r.snippet or None,
                    published_at=r.published_at,
                )
                for r in response.results
            ]
            return _Gathered(entries, cost_usd=response.cost_usd or None)
        except (FetchError, FeedError, SearchError) as exc:
            return _Gathered([], error=f"{type(exc).__name__}: {exc}"[:500])

    async def _apply(
        self, session: AsyncSession, source: Source, gathered: _Gathered, now: datetime
    ) -> PollOutcome:
        source.last_polled_at = now
        source.next_poll_at = now + timedelta(seconds=source.poll_interval_seconds)
        if gathered.error is not None:
            source.consecutive_failures += 1
            source.last_error = gathered.error
            await self._emit(
                session,
                source,
                SourcePolled(
                    source_id=source.id,
                    count=0,
                    seen=0,
                    cost_usd=gathered.cost_usd,
                    error=gathered.error,
                ),
            )
            paused = source.consecutive_failures >= self.pause_after
            if paused:
                source.status = SourceStatus.PAUSED.value
                reason = (
                    f"{source.consecutive_failures} failed polls in a row; last: {gathered.error}"[
                        :500
                    ]
                )
                await self._emit(
                    session,
                    source,
                    SourcePaused(
                        source_id=source.id, reason=reason, failures=source.consecutive_failures
                    ),
                )
            return PollOutcome(source.id, [], 0, gathered.error, paused)

        source.consecutive_failures = 0
        source.last_error = None
        max_age = source.config.get(MAX_AGE_DAYS)
        oldest = now - timedelta(days=max_age) if max_age else None
        newest_first = sorted(
            (
                e
                for e in gathered.entries
                if oldest is None or e.published_at is None or e.published_at >= oldest
            ),
            key=lambda e: e.published_at or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )[: self.max_items]
        new_ids: list[uuid.UUID] = []
        prefix = str(source.config.get(TITLE_PREFIX) or "").strip()
        for entry in newest_first:
            url = canonical_url(entry.url)
            title = f"{prefix} {entry.title}"[:1000] if prefix else entry.title
            item_id = await session.scalar(
                insert(SourceItem)
                .values(
                    id=uuid.uuid4(),
                    company_id=source.company_id,
                    source_id=source.id,
                    external_id=entry.external_id,
                    url=url,
                    title=title,
                    summary=entry.summary,
                    published_at=entry.published_at,
                    content_hash=content_hash(url, title),
                )
                .on_conflict_do_nothing()
                .returning(SourceItem.id)
            )
            if item_id is None:
                continue
            new_ids.append(item_id)
            await self._emit(
                session,
                source,
                SourceItemDiscovered(
                    item_id=item_id, source_id=source.id, url=url, title=title[:300]
                ),
            )
        await self._emit(
            session,
            source,
            SourcePolled(
                source_id=source.id,
                count=len(new_ids),
                seen=len(gathered.entries),
                cost_usd=gathered.cost_usd,
            ),
        )
        return PollOutcome(source.id, new_ids, len(gathered.entries), None, False)

    async def _emit(self, session: AsyncSession, source: Source, payload: EventPayload) -> None:
        await emit(
            session,
            new_event(
                payload,
                company_id=source.company_id,
                actor=self.actor,
                aggregate_type="source",
                aggregate_id=source.id,
            ),
        )

    def schedule_handler(self) -> Handler:
        """The ``newsroom.poll_sources`` handler: poll the schedule's company's due sources."""

        async def handler(
            session: AsyncSession, schedule: Schedule, scheduled_for: datetime
        ) -> None:
            await self.poll_due(session, schedule.company_id)

        return handler
