"""Newsroom agent behaviors (3d-office/06 §1), registered by ``register_behaviors``.

T-605b: the editor-in-chief, who decides what the desk covers; T-506: the researcher; T-507:
the analyst; T-509: the writer; T-511: the editor; T-513: marketing.
"""

from __future__ import annotations

from autora.domains.newsroom.agents import (
    analyst,
    editor,
    editor_in_chief,
    marketing,
    researcher,
    writer,
)
from autora.runtime.behaviors import BehaviorRegistry

BEHAVIORS = (
    editor_in_chief.BEHAVIOR,
    researcher.BEHAVIOR,
    analyst.BEHAVIOR,
    writer.BEHAVIOR,
    editor.BEHAVIOR,
    marketing.BEHAVIOR,
)


def register_behaviors(registry: BehaviorRegistry) -> None:
    for behavior in BEHAVIORS:
        registry.register(behavior)


__all__ = ["BEHAVIORS", "register_behaviors"]
