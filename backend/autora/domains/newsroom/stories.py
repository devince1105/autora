"""Stories (T-504): clustering source items into topics, deduplicating, scoring, and the story
lifecycle (platform/02 §6).

**Clustering** runs from its own schedule (``newsroom.cluster_stories``, two minutes after each
poll slot) over items not yet in a story. No language model: each item's title and summary is
embedded (the ``embed`` binding) and compared with the company's stories of the last 30 days,
whatever their state:
- an item whose URL is already in a story joins that story (the same article from two feeds);
- otherwise it joins the closest story if the cosine similarity reaches ``threshold``;
- otherwise it starts a new story (DISCOVERED, with STORY_DISCOVERED).
Matching includes stories already in production, published, ignored or dropped, so the same news
arriving again never becomes a second story ("have we written this already?"), and an ignored
topic stays ignored.

The default threshold, 0.65, comes from measuring the configured model on fixture headlines
(2026-09-19, nvidia/nemotron-3-embed-1b): the same event in two English headlines 0.73; the same
event in English and Chinese 0.57; related but different stories 0.41-0.54; unrelated 0.12-0.21.
Cross-language matching would need ~0.55, which also merges merely related stories, so for now
the English and Chinese reports of one event can be two stories (the researcher gathers both).

**Score** (0-1, no model): 0.5 x corroboration (distinct sources, 3 or more is full) + 0.3 x
freshness (newest item, fading to 0 over 72 hours) + 0.2 x the sources' average trust level.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Float, bindparam, func, literal, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from autora.db.models import Project
from autora.db.vector import HalfVector
from autora.domains.newsroom.events import StoryDiscovered, StoryDropped, StorySelected
from autora.domains.newsroom.models import (
    EMBED_DIM,
    Source,
    SourceItem,
    Story,
    StoryItem,
    StoryState,
)
from autora.runtime.actor import Actor
from autora.runtime.events.outbox import emit
from autora.runtime.events.schema import EventPayload, new_event
from autora.runtime.fsm import StateMachine, transitions
from autora.runtime.models.embeddings import EmbedCaller, Embedder
from autora.runtime.scheduler import Handler

S = StoryState
STORY_FSM = StateMachine(
    entity_type="story",
    states=StoryState,
    initial=S.DISCOVERED,
    transitions=transitions(
        {
            S.DISCOVERED: [S.SELECTED, S.IGNORED, S.DROPPED],
            S.SELECTED: [S.IN_PRODUCTION, S.DROPPED],
            S.IN_PRODUCTION: [S.PUBLISHED, S.DROPPED],
        }
    ),
)

CLUSTER_SCHEDULE = "newsroom.cluster_stories"
"""The schedule's name and its handler's key."""
CLUSTER_CRON = "2-59/5 * * * *"
"""Two minutes after each poll slot, so a poll's items are clustered in the same five minutes."""
MATCH_WINDOW = timedelta(days=30)
PENDING_WINDOW = timedelta(days=7)
"""Items older than this that never got clustered are left alone."""
FRESH_HOURS = 72


class StoryError(ValueError):
    pass


@dataclass(frozen=True)
class ClusterOutcome:
    new_story_ids: list[uuid.UUID]
    joined: dict[uuid.UUID, uuid.UUID]
    """source item id -> story id, for every item clustered this run."""


def _normalized(vector: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]


def _text(item: SourceItem) -> str:
    return item.title if not item.summary else f"{item.title}\n{item.summary}"


@dataclass
class StoryDesk:
    embedder: Embedder
    threshold: float = 0.65
    batch_size: int = 100
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    actor: Actor = field(default_factory=lambda: Actor.system("newsroom.stories"))

    # --- clustering ----------------------------------------------------------------------------

    async def cluster_pending(self, session: AsyncSession, company_id: uuid.UUID) -> ClusterOutcome:
        now = self.clock()
        items = (
            await session.scalars(
                select(SourceItem)
                .outerjoin(StoryItem, StoryItem.source_item_id == SourceItem.id)
                .where(
                    SourceItem.company_id == company_id,
                    StoryItem.story_id.is_(None),
                    SourceItem.created_at >= now - PENDING_WINDOW,
                )
                .order_by(
                    func.coalesce(SourceItem.published_at, SourceItem.created_at),
                    SourceItem.created_at,
                )
                .limit(self.batch_size)
                .with_for_update(of=SourceItem, skip_locked=True)
            )
        ).all()
        if not items:
            return ClusterOutcome([], {})
        # all network before any write that takes the event lock
        vectors = await self.embedder.embed(
            session,
            [_text(i) for i in items],
            purpose="passage",
            caller=EmbedCaller(company_id=company_id, role="system"),
        )
        await session.execute(text("SET LOCAL hnsw.iterative_scan = 'relaxed_order'"))

        new_stories: list[Story] = []
        touched: dict[uuid.UUID, Story] = {}
        joined: dict[uuid.UUID, uuid.UUID] = {}
        for item, vector in zip(items, vectors, strict=True):
            story, similarity = await self._match(session, company_id, item, vector, now)
            if story is None:
                story = Story(
                    company_id=company_id,
                    title=item.title[:500],
                    summary=item.summary,
                    state=S.DISCOVERED.value,
                    first_seen_at=now,
                    last_item_at=item.published_at or now,
                    items_count=0,
                    embedding=_normalized(vector),
                    embedding_model=self.embedder.model_id,
                )
                session.add(story)
                await session.flush()
                new_stories.append(story)
            else:
                count = story.items_count
                old = story.embedding or [0.0] * len(vector)
                story.embedding = _normalized(
                    [o * count + v for o, v in zip(old, vector, strict=True)]
                )
                story.last_item_at = max(story.last_item_at, item.published_at or now)
            story.items_count += 1
            session.add(
                StoryItem(
                    story_id=story.id,
                    source_item_id=item.id,
                    company_id=company_id,
                    similarity=similarity,
                )
            )
            await session.flush()
            touched[story.id] = story
            joined[item.id] = story.id

        for story in touched.values():
            await self._rescore(session, story, now)
        for story in new_stories:
            await self._emit(
                session,
                story,
                StoryDiscovered(story_id=story.id, title=story.title[:300], score=story.score),
            )
        return ClusterOutcome([s.id for s in new_stories], joined)

    async def _match(
        self,
        session: AsyncSession,
        company_id: uuid.UUID,
        item: SourceItem,
        vector: list[float],
        now: datetime,
    ) -> tuple[Story | None, float | None]:
        same_url = await session.scalar(
            select(Story)
            .join(StoryItem, StoryItem.story_id == Story.id)
            .join(SourceItem, SourceItem.id == StoryItem.source_item_id)
            .where(
                SourceItem.company_id == company_id,
                SourceItem.url == item.url,
                SourceItem.id != item.id,
            )
            .limit(1)
        )
        if same_url is not None:
            return same_url, 1.0
        query = bindparam("item_vector", vector, type_=HalfVector(EMBED_DIM))
        distance = Story.embedding.op("<=>", return_type=Float)(query)
        row = (
            await session.execute(
                select(Story, (literal(1.0) - distance).label("similarity"))
                .where(
                    Story.company_id == company_id,
                    Story.embedding_model == self.embedder.model_id,
                    Story.last_item_at >= now - MATCH_WINDOW,
                )
                .order_by(distance)
                .limit(1)
            )
        ).first()
        if row is not None and row.similarity >= self.threshold:
            return row.Story, round(float(row.similarity), 4)
        return None, None

    async def _rescore(self, session: AsyncSession, story: Story, now: datetime) -> None:
        sources, trust = (
            await session.execute(
                select(
                    func.count(func.distinct(SourceItem.source_id)), func.avg(Source.trust_level)
                )
                .select_from(StoryItem)
                .join(SourceItem, SourceItem.id == StoryItem.source_item_id)
                .join(Source, Source.id == SourceItem.source_id)
                .where(StoryItem.story_id == story.id)
            )
        ).one()
        story.sources_count = int(sources)
        age_hours = max(0.0, (now - story.last_item_at).total_seconds() / 3600)
        freshness = max(0.0, 1 - age_hours / FRESH_HOURS)
        corroboration = min(story.sources_count, 3) / 3
        value = 0.5 * corroboration + 0.3 * freshness + 0.2 * float(trust or 0)
        story.score = Decimal(str(round(min(1.0, value), 3)))
        await session.flush()

    # --- lifecycle -----------------------------------------------------------------------------

    async def create(
        self,
        session: AsyncSession,
        *,
        company_id: uuid.UUID,
        title: str,
        summary: str | None = None,
        seed: dict[str, Any] | None = None,
        actor: Actor,
    ) -> Story:
        """A story someone starts by hand (a title, and optionally urls / a search query for the
        researcher). Embedded like clustered stories, so later items about it join it."""
        [vector] = await self.embedder.embed(
            session,
            [title if not summary else f"{title}\n{summary}"],
            purpose="passage",
            caller=EmbedCaller(company_id=company_id, role="system"),
        )
        now = self.clock()
        story = Story(
            company_id=company_id,
            title=title[:500],
            summary=summary,
            state=S.DISCOVERED.value,
            first_seen_at=now,
            last_item_at=now,
            embedding=_normalized(vector),
            embedding_model=self.embedder.model_id,
            seed=dict(seed or {}),
        )
        session.add(story)
        await session.flush()
        await self._rescore(session, story, now)
        await self._emit(
            session,
            story,
            StoryDiscovered(story_id=story.id, title=story.title[:300], score=story.score),
            actor=actor,
        )
        return story

    async def select(
        self,
        session: AsyncSession,
        story: Story,
        *,
        project_id: uuid.UUID,
        actor: Actor,
        reason: str | None = None,
    ) -> None:
        project = await session.get(Project, project_id)
        if project is None or project.company_id != story.company_id:
            raise StoryError(f"project {project_id} is not this company's")
        await STORY_FSM.transition(session, story, S.SELECTED, actor=actor, reason=reason)
        story.project_id = project_id
        await self._emit(
            session,
            story,
            StorySelected(
                story_id=story.id, title=story.title[:300], score=story.score, project_id=project_id
            ),
            actor=actor,
        )

    async def ignore(
        self, session: AsyncSession, story: Story, *, actor: Actor, reason: str | None = None
    ) -> None:
        """Not worth writing. Audited; no event (nothing happened in the company)."""
        await STORY_FSM.transition(session, story, S.IGNORED, actor=actor, reason=reason)

    async def drop(self, session: AsyncSession, story: Story, *, actor: Actor, reason: str) -> None:
        await drop_story(session, story, actor=actor, reason=reason)

    async def _emit(
        self, session: AsyncSession, story: Story, payload: EventPayload, actor: Actor | None = None
    ) -> None:
        await emit(
            session,
            new_event(
                payload,
                company_id=story.company_id,
                actor=actor or self.actor,
                aggregate_type="story",
                aggregate_id=story.id,
            ),
        )

    def schedule_handler(self) -> Handler:
        """The ``newsroom.cluster_stories`` handler: cluster the company's pending items."""

        async def handler(session: AsyncSession, schedule, scheduled_for: datetime) -> None:
            await self.cluster_pending(session, schedule.company_id)

        return handler


async def drop_story(session: AsyncSession, story: Story, *, actor: Actor, reason: str) -> bool:
    """Drop a story (STORY_DROPPED); False when it is past dropping (published, already closed)."""
    if not STORY_FSM.can(story.state, S.DROPPED):
        return False
    await STORY_FSM.transition(session, story, S.DROPPED, actor=actor, reason=reason)
    await emit(
        session,
        new_event(
            StoryDropped(
                story_id=story.id, title=story.title[:300], score=story.score, reason=reason[:500]
            ),
            company_id=story.company_id,
            actor=actor,
            aggregate_type="story",
            aggregate_id=story.id,
        ),
    )
    return True
