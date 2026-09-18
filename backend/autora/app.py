"""Composition root: the only module allowed to import every layer (runtime, company, realtime,
domains). Processes (api, worker) and tools (schema codegen) assemble the system from here.
"""

from __future__ import annotations


def load_event_catalogs() -> None:
    """Import every module that registers event payloads, so the registry is complete."""
    import autora.company.events  # noqa: F401
    import autora.runtime.events  # noqa: F401

    # Domains register their events here once they exist (Phase 5: autora.domains.newsroom).
