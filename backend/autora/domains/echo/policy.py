"""Permission rules for echo tools."""

from __future__ import annotations

from autora.domains.echo.workflow import ROLES
from autora.runtime.policy import PolicyEngine, allow


def register(engine: PolicyEngine) -> None:
    engine.declare("echo_note", "write")
    engine.add(allow("echo_note", *ROLES.values()))
