"""Newsroom tools (logs/platform/05_NEWSROOM_DOMAIN.md §5), registered by ``register_tools``."""

from __future__ import annotations

from autora.domains.newsroom.tools import search
from autora.infra.search import SearchProvider
from autora.runtime.tools import ToolRegistry


def register_tools(registry: ToolRegistry, *, search_provider: SearchProvider) -> None:
    search.register(registry, search_provider)


__all__ = ["register_tools"]
