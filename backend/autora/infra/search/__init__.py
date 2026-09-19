"""Web search providers (T-500, D-003).

A search result is a **candidate**, never evidence: whoever uses one must fetch the page with
``fetch_url`` (T-502), which snapshots it as Evidence. Nothing here writes to the database.

Implementations:
- ``TavilySearchProvider`` (``tavily.py``): the live provider (``TOOLS_PROFILE=live``). The API
  key comes only from the environment (settings) and is never logged or returned.
- ``FixtureSearchProvider`` (``fixture.py``): a fixed corpus, for tests and simulation
  (``TOOLS_PROFILE=fixture``). No network.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Protocol

from pydantic import BaseModel, Field


class SearchResult(BaseModel):
    url: str
    title: str
    snippet: str
    published_at: datetime | None = None
    score: float | None = None


class SearchResponse(BaseModel):
    results: list[SearchResult]
    provider: str
    cost_usd: Decimal = Field(default=Decimal(0), ge=0)
    """What this call cost (the provider's price for it); 0 for fixtures."""


class SearchError(Exception):
    """A search that did not return results. ``retryable``: whether trying again may help."""

    retryable = False


class SearchUnavailable(SearchError):
    """Timeouts, rate limits, server errors: worth another try later."""

    retryable = True


class SearchRejected(SearchError):
    """Bad key, exhausted plan, invalid request: another try gives the same answer."""


class SearchProvider(Protocol):
    name: str

    async def search(
        self, query: str, *, k: int, recency_days: int | None = None
    ) -> SearchResponse:
        """Up to ``k`` results for ``query``; ``recency_days``: only pages from that many days."""
        ...


__all__ = [
    "SearchError",
    "SearchProvider",
    "SearchRejected",
    "SearchResponse",
    "SearchResult",
    "SearchUnavailable",
]
