"""Newsroom tools (logs/platform/05_NEWSROOM_DOMAIN.md §5), registered by ``register_tools``."""

from __future__ import annotations

from autora.domains.newsroom.tools import evidence, search
from autora.infra.blobstore import BlobStore
from autora.infra.http import PageFetcher
from autora.infra.search import SearchProvider
from autora.runtime.models.embeddings import Embedder
from autora.runtime.tools import ToolRegistry


def register_tools(
    registry: ToolRegistry,
    *,
    search_provider: SearchProvider,
    fetcher: PageFetcher,
    blobs: BlobStore,
    embedder: Embedder,
) -> None:
    search.register(registry, search_provider)
    evidence.register(registry, fetcher, blobs, embedder)


__all__ = ["register_tools"]
