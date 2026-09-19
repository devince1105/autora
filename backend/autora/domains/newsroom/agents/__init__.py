"""Newsroom agent behaviors (3d-office/06 §1), registered by ``register_behaviors``.

T-506: the researcher; T-507: the analyst. The writer, editor and marketing follow (T-509,
T-511, T-513).
"""

from __future__ import annotations

from autora.domains.newsroom.agents import analyst, researcher
from autora.runtime.behaviors import BehaviorRegistry

BEHAVIORS = (researcher.BEHAVIOR, analyst.BEHAVIOR)


def register_behaviors(registry: BehaviorRegistry) -> None:
    for behavior in BEHAVIORS:
        registry.register(behavior)


__all__ = ["BEHAVIORS", "register_behaviors"]
