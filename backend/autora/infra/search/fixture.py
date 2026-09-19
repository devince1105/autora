"""A search provider over a fixed corpus (T-500): tests and simulation (``TOOLS_PROFILE=fixture``).

Deterministic and offline. A document matches when it shares words with the query (Latin words,
case-insensitive; CJK text by single characters); results are ranked by how many query terms
they contain, then by corpus order. No match, no results: the fixture never invents pages.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, TypeAdapter

from autora.infra.search import SearchResponse, SearchResult

_WORD = re.compile(r"[a-z0-9]+|[㐀-鿿]")
_STOP = set("a an and the of in on for to is are with by 的 是 在 和".split())


class FixtureDocument(BaseModel):
    url: str
    title: str
    snippet: str
    published_at: datetime | None = None
    keywords: list[str] = []


def _terms(text: str) -> set[str]:
    return {t for t in _WORD.findall(text.lower()) if t not in _STOP}


class FixtureSearchProvider:
    name = "fixture"

    def __init__(self, documents: list[FixtureDocument], *, now: datetime | None = None):
        self._docs = documents
        self._now = now
        self._index = [_terms(" ".join([d.title, d.snippet, *d.keywords])) for d in documents]

    @classmethod
    def from_file(cls, path: Path) -> FixtureSearchProvider:
        docs = TypeAdapter(list[FixtureDocument]).validate_python(
            json.loads(path.read_text("utf-8"))
        )
        return cls(docs)

    async def search(
        self, query: str, *, k: int, recency_days: int | None = None
    ) -> SearchResponse:
        wanted = _terms(query)
        cutoff = None
        if recency_days is not None:
            cutoff = (self._now or datetime.now(UTC)) - timedelta(days=recency_days)
        scored = []
        for order, (doc, terms) in enumerate(zip(self._docs, self._index, strict=True)):
            hits = len(wanted & terms)
            if not hits:
                continue
            if cutoff is not None and (doc.published_at is None or doc.published_at < cutoff):
                continue
            scored.append((-hits, order, doc, hits / max(len(wanted), 1)))
        scored.sort(key=lambda row: (row[0], row[1]))
        results = [
            SearchResult(
                url=doc.url,
                title=doc.title,
                snippet=doc.snippet,
                published_at=doc.published_at,
                score=round(score, 3),
            )
            for _, _, doc, score in scored[:k]
        ]
        return SearchResponse(results=results, provider=self.name, cost_usd=Decimal(0))
