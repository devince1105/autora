"""Newsroom tools (logs/platform/05_NEWSROOM_DOMAIN.md §5), registered by ``register_tools``."""

from __future__ import annotations

from autora.domains.newsroom.tools import (
    claims,
    commission,
    distribution,
    drafts,
    evidence,
    factcheck,
    filings,
    review,
    search,
)
from autora.infra.blobstore import BlobStore
from autora.infra.http import PageFetcher
from autora.infra.search import SearchProvider
from autora.runtime.dag import WorkflowEngine
from autora.runtime.models.embeddings import Embedder
from autora.runtime.policy import PolicyEngine
from autora.runtime.tools import ToolRegistry


def register_tools(
    registry: ToolRegistry,
    *,
    search_provider: SearchProvider,
    fetcher: PageFetcher,
    blobs: BlobStore,
    embedder: Embedder,
    policy: PolicyEngine | None = None,
    workflows: WorkflowEngine | None = None,
) -> None:
    search.register(registry, search_provider)
    evidence.register(registry, fetcher, blobs, embedder)
    filings.register(registry, fetcher, blobs, embedder)
    claims.register(registry)
    drafts.register(registry)
    factcheck.register(registry, embedder)
    review.register(registry)
    distribution.register(registry)
    if policy is not None and workflows is not None:
        # what the editor-in-chief does: commission a story and put the desk to work (T-605b)
        commission.register(registry, policy, workflows)


__all__ = ["register_tools"]
