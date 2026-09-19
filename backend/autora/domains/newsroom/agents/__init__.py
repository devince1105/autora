"""Newsroom agent behaviors (3d-office/06 §1), registered by ``register_behaviors``.

T-506: the researcher. The analyst, writer, editor and marketing follow (T-507, T-509, T-511,
T-513).
"""

from __future__ import annotations

from autora.domains.newsroom.agents import researcher
from autora.runtime.behaviors import BehaviorRegistry

BEHAVIORS = (researcher.BEHAVIOR,)


def register_behaviors(registry: BehaviorRegistry) -> None:
    for behavior in BEHAVIORS:
        registry.register(behavior)


__all__ = ["BEHAVIORS", "register_behaviors"]
