"""Tavily search (T-500, D-003): https://docs.tavily.com/documentation/api-reference/endpoint/search

- The key is sent as ``Authorization: Bearer <key>``; it is never logged, and error messages
  never contain it (they carry the status and Tavily's own message).
- Every call has a timeout, and a local rate limit keeps one process under the plan's limit
  (a call waits for a free slot up to ``max_wait_s``, then fails as retryable).
- Cost: Tavily bills in credits (``basic`` search 1, ``advanced`` 2); the response's
  ``usage.credits`` is used when present, else the depth's price. ``cost_per_credit`` is the
  pay-as-you-go price in USD (a setting, since plans differ).
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal
from email.utils import parsedate_to_datetime
from typing import Any, Literal

import httpx
from pydantic import SecretStr

from autora.infra.search import (
    SearchRejected,
    SearchResponse,
    SearchResult,
    SearchUnavailable,
)

ENDPOINT = "https://api.tavily.com/search"
Depth = Literal["basic", "advanced"]
_CREDITS: dict[str, int] = {"basic": 1, "advanced": 2}
_SNIPPET_LIMIT = 1000


class RateLimiter:
    """At most ``per_minute`` acquisitions in any 60-second window (a sliding window)."""

    def __init__(
        self,
        per_minute: int,
        *,
        max_wait_s: float = 10.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.per_minute = per_minute
        self.max_wait_s = max_wait_s
        self._clock = clock
        self._sleep = sleep
        self._stamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = self._clock()
            while self._stamps and now - self._stamps[0] >= 60:
                self._stamps.popleft()
            if len(self._stamps) >= self.per_minute:
                wait = 60 - (now - self._stamps[0])
                if wait > self.max_wait_s:
                    limit = f"search rate limit ({self.per_minute}/min) reached"
                    raise SearchUnavailable(f"{limit}; try again in {wait:.0f}s")
                await self._sleep(wait)
                now = self._clock()
                self._stamps.popleft()
            self._stamps.append(now)


class TavilySearchProvider:
    name = "tavily"

    def __init__(
        self,
        api_key: SecretStr,
        *,
        timeout_s: float = 15.0,
        depth: Depth = "basic",
        cost_per_credit: Decimal = Decimal("0.008"),
        requests_per_minute: int = 60,
        client: httpx.AsyncClient | None = None,
        limiter: RateLimiter | None = None,
    ):
        self._key = api_key
        self._timeout = timeout_s
        self._depth = depth
        self._cost_per_credit = cost_per_credit
        self._client = client
        self._limiter = limiter or RateLimiter(requests_per_minute)

    async def search(
        self, query: str, *, k: int, recency_days: int | None = None
    ) -> SearchResponse:
        body: dict[str, Any] = {
            "query": query,
            "max_results": k,
            "search_depth": self._depth,
            "include_answer": False,
            "include_raw_content": False,
            "include_published_date": True,
            "include_usage": True,
        }
        if recency_days is not None:
            body["time_range"] = _time_range(recency_days)
        await self._limiter.acquire()
        headers = {"Authorization": f"Bearer {self._key.get_secret_value()}"}
        try:
            if self._client is not None:
                response = await self._client.post(
                    ENDPOINT, json=body, headers=headers, timeout=self._timeout
                )
            else:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        ENDPOINT, json=body, headers=headers, timeout=self._timeout
                    )
        except httpx.TimeoutException:
            raise SearchUnavailable(f"Tavily did not answer within {self._timeout}s") from None
        except httpx.HTTPError as exc:
            raise SearchUnavailable(f"Tavily request failed: {type(exc).__name__}") from None

        if response.status_code != 200:
            raise _error_for(response)
        data = response.json()
        results = [_result(item) for item in data.get("results", [])[:k]]
        credits = (data.get("usage") or {}).get("credits")
        if not isinstance(credits, int) or credits < 0:
            credits = _CREDITS[self._depth]
        return SearchResponse(
            results=results, provider=self.name, cost_usd=self._cost_per_credit * credits
        )


def _time_range(days: int) -> str:
    if days <= 1:
        return "day"
    if days <= 7:
        return "week"
    if days <= 31:
        return "month"
    return "year"


def _result(item: dict[str, Any]) -> SearchResult:
    snippet = str(item.get("content") or "")
    return SearchResult(
        url=str(item["url"]),
        title=str(item.get("title") or item["url"]),
        snippet=snippet[:_SNIPPET_LIMIT],
        published_at=_date(item.get("published_date")),
        score=item.get("score"),
    )


def _date(value: Any) -> datetime | None:
    """Tavily's dates come as RFC 2822 (news) or ISO 8601; unparseable ones are dropped."""
    if not value or not isinstance(value, str):
        return None
    for parse in (parsedate_to_datetime, datetime.fromisoformat):
        try:
            parsed = parse(value)
        except (TypeError, ValueError, IndexError):
            continue
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def _error_for(response: httpx.Response) -> Exception:
    status = response.status_code
    detail = ""
    try:
        payload = response.json()
        detail = str(payload.get("detail", {}).get("error") or payload.get("detail") or "")[:200]
    except (ValueError, AttributeError):
        detail = response.text[:200]
    message = f"Tavily {status}" + (f": {detail}" if detail else "")
    if status == 429:
        retry_after = response.headers.get("retry-after")
        return SearchUnavailable(
            message + (f" (retry after {retry_after}s)" if retry_after else "")
        )
    if status >= 500:
        return SearchUnavailable(message)
    # 400/422 bad request, 401 bad key, 432/433 plan or pay-as-you-go limit: retrying won't help
    return SearchRejected(message)
