"""Newsroom events (logs/3d-office/03_EVENT_MODEL.md §2.5, platform/11_EVENT_CATALOG.md).

Added with the task that first emits them; T-501: the source poller's.
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
