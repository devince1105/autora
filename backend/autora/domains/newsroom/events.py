"""Newsroom events (logs/3d-office/03_EVENT_MODEL.md §2.5, platform/11_EVENT_CATALOG.md).

Added with the task that first emits them; T-501: the source poller's; T-502: evidence;
T-504: stories; T-505: claims; T-508: articles;
T-510: fact-check verdicts.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from autora.runtime.events.schema import EventPayload, event


@event("SOURCE_POLLED")
class SourcePolled(EventPayload):
    source_id: uuid.UUID
    count: int
    """New items this poll found (already-known entries are not counted)."""
    seen: int = 0
    """Entries the source listed this time, new or not."""
    cost_usd: Decimal | None = None
    """What the poll cost (a search_query source's search); None when free."""
    error: str | None = None
    """Why the poll failed; the source is retried at its next interval."""


@event("SOURCE_ITEM_DISCOVERED")
class SourceItemDiscovered(EventPayload):
    """Archivable (03 §3): one per new item, so a timeline can show what came in."""

    item_id: uuid.UUID
    source_id: uuid.UUID
    url: str
    title: str


@event("SOURCE_PAUSED")
class SourcePaused(EventPayload):
    source_id: uuid.UUID
    reason: str
    failures: int


@event("EVIDENCE_CAPTURED")
class EvidenceCaptured(EventPayload):
    """A new snapshot (a re-fetch of identical content reuses the old one and emits nothing)."""

    evidence_id: uuid.UUID
    url: str
    title: str | None = None
    source_id: uuid.UUID | None = None
    story_id: uuid.UUID | None = None
    """Filled once stories exist (T-504) and the fetch was for one."""


@event("STORY_DISCOVERED")
class StoryDiscovered(EventPayload):
    story_id: uuid.UUID
    title: str
    score: Decimal


@event("STORY_SELECTED")
class StorySelected(EventPayload):
    story_id: uuid.UUID
    title: str
    score: Decimal
    project_id: uuid.UUID


@event("STORY_DROPPED")
class StoryDropped(EventPayload):
    story_id: uuid.UUID
    title: str
    score: Decimal
    reason: str


@event("CLAIM_CREATED")
class ClaimCreated(EventPayload):
    claim_id: uuid.UUID
    story_id: uuid.UUID
    claim_type: str
    evidence_ids: list[uuid.UUID] = []
    """Evidence quoted when the claim was made (more can be linked later)."""


@event("ARTICLE_CREATED")
class ArticleCreated(EventPayload):
    """The first draft of a story's article (later drafts are new versions of it)."""

    article_id: uuid.UUID
    story_id: uuid.UUID
    version_id: uuid.UUID
    """The primary-language version of the first draft."""
    langs: list[str]
    slug: str


@event("CLAIM_VERIFIED")
class ClaimVerified(EventPayload):
    """Passed the deterministic fact-check (the editor still judges the meaning)."""

    claim_id: uuid.UUID
    story_id: uuid.UUID
    claim_type: str
    evidence_ids: list[uuid.UUID] = []


@event("CLAIM_REJECTED")
class ClaimRejected(EventPayload):
    claim_id: uuid.UUID
    story_id: uuid.UUID
    claim_type: str
    evidence_ids: list[uuid.UUID] = []
    problems: list[str] = []
