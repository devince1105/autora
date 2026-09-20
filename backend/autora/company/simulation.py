"""The simulated CEO (``MODEL_PROVIDER=fake``, T-605a).

Like the newsroom's, it reads its conversation the way a real model would — the snapshot it was
given, the results of the commands it has submitted — and acts through the same tool. Only the
judgement is scripted. So a simulated cycle exercises the real pipeline: the policy decides, a
refusal comes back as a refusal, and the plan is checked against what was actually submitted.

The scripted judgement is the simplest defensible one, and it is stated here rather than hidden
in the replies: **spend the day's cap on the business that is running, and set a goal for it.**
That is enough to make a cycle real end to end, and little enough that nobody mistakes it for
the CEO's actual reasoning.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from autora.runtime.models.providers.fake import FakeToolUse, FakeTurn
from autora.runtime.models.types import ModelRequest, ToolResultBlock, ToolUseBlock

ROLE = "ceo"
DEFAULT_ALLOCATION = Decimal("5")


def respond(request: ModelRequest) -> FakeTurn | None:
    """A reply for the CEO's tasks; None for anything else."""
    if request.context.role != ROLE:
        return None
    if request.context.task_name == "plan":
        return _plan(request)
    if request.context.task_name == "review":
        return _review(request)
    return None


def _plan(request: ModelRequest) -> FakeTurn:
    snapshot = _snapshot(request)
    unit = _first_running_business(snapshot)
    submitted = _submitted(request)

    if "CreateCycleGoal" not in submitted:
        return FakeTurn(
            tool_uses=[
                FakeToolUse(
                    name="submit_command",
                    input={
                        "command": "CreateCycleGoal",
                        "payload": {
                            "title": "Do what the day is worth",
                            "metric": _goal_metric(snapshot),
                            "target": 3,
                        },
                        "reason": "one measurable thing for the cycle",
                    },
                )
            ]
        )
    if unit is not None and "AllocateBudget" not in submitted:
        return FakeTurn(
            tool_uses=[
                FakeToolUse(
                    name="submit_command",
                    input={
                        "command": "AllocateBudget",
                        "payload": {
                            "amount": str(_affordable(snapshot)),
                            "period": "cycle",
                            "business_unit_id": unit["id"],
                        },
                        "reason": f"{unit['name']} is the business that is running",
                    },
                )
            ]
        )

    allocations = []
    if unit is not None and _succeeded(request, "AllocateBudget"):
        allocations.append(
            {
                "amount": str(_affordable(snapshot)),
                "business_unit_id": unit["id"],
                "rationale": f"{unit['name']} is where the work is",
            }
        )
    goals = []
    if _succeeded(request, "CreateCycleGoal"):
        goals.append(
            {
                "title": "Do what the day is worth",
                "metric": _goal_metric(snapshot),
                "target": 3,
            }
        )
    return FakeTurn(
        text=json.dumps(
            {
                "goals": goals,
                "allocations": allocations,
                "priorities": [],
                "rationale": (
                    "One goal and one envelope: the business that is running gets what the "
                    "company can afford today, and nothing else is started."
                ),
            }
        )
    )


def _review(request: ModelRequest) -> FakeTurn:
    snapshot = _snapshot(request)
    projects = []
    for unit in snapshot.get("portfolio", []):
        for project in unit.get("projects", []):
            projects.append(
                {
                    "project_id": project["id"],
                    "decision": "continue",
                    "rationale": "it did what it was funded for and nothing says to stop",
                }
            )
    return FakeTurn(
        text=json.dumps(
            {
                "projects": projects,
                "summary": (
                    "The cycle ran. Costs are within the day's cap and no project is in "
                    "breach of its criteria."
                ),
                "next_cycle_hints": {"focus": "the same, unless the numbers change"},
                "strategy_change_proposal": None,
            }
        )
    )


# --- reading its own conversation ---------------------------------------------------------------


MARKER = "The company right now:"


def _snapshot(request: ModelRequest) -> dict[str, Any]:
    """The document the CEO was handed, parsed back out of its first message.

    Read from the marker onwards, not from the first brace in the message: the task's own input
    is JSON too, and a regex spanning both parses neither.
    """
    for message in request.messages:
        for text in _texts(message):
            if MARKER not in text:
                continue
            after = text.split(MARKER, 1)[1]
            try:
                value, _ = json.JSONDecoder().raw_decode(after.lstrip())
            except ValueError:
                continue
            if isinstance(value, dict):
                return value
    return {}


def _texts(message) -> list[str]:
    if isinstance(message.content, str):
        return [message.content]
    return [t for t in (getattr(b, "text", None) for b in message.content or []) if t]


def _submitted(request: ModelRequest) -> set[str]:
    """Which commands this run has already asked for."""
    asked = set()
    for message in request.messages:
        blocks = message.content if isinstance(message.content, list) else []
        for block in blocks:
            if isinstance(block, ToolUseBlock) and block.name == "submit_command":
                command = (block.input or {}).get("command")
                if command:
                    asked.add(command)
    return asked


def _succeeded(request: ModelRequest, command: str) -> bool:
    """Whether the company actually did it — a refusal is an answer, and the plan must not
    claim what was refused."""
    for message in request.messages:
        blocks = message.content if isinstance(message.content, list) else []
        for block in blocks:
            if not isinstance(block, ToolResultBlock) or block.is_error:
                continue
            content = block.content if isinstance(block.content, str) else json.dumps(block.content)
            if f'"command": "{command}"' in content and '"decision": "done"' in content:
                return True
    return False


DEFAULT_METRIC = "model_calls"
"""What a company with no business of its own can still count. Core vocabulary only: this
module stands in for the CEO, and the CEO does not know what industry it is in (T-611)."""


def _goal_metric(snapshot: dict[str, Any]) -> str:
    """The metric to aim at, read from what the company actually measures.

    A newsroom's snapshot carries ``newsroom.published_articles`` because the newsroom
    registered a KPI hook; a company with no domain carries none, and the goal is about the
    work itself. Either way the name comes from the data, never from this file.
    """
    for unit in snapshot.get("portfolio", []):
        for metric, value in (unit.get("kpis_last_cycle") or {}).items():
            if "." in metric and isinstance(value, int):
                return metric
    for metric, value in ((snapshot.get("last_cycle") or {}).get("kpis") or {}).items():
        if "." in metric and isinstance(value, int):
            return metric
    return DEFAULT_METRIC


def _first_running_business(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    for unit in snapshot.get("portfolio", []):
        if unit.get("state") == "ACTIVE" and unit.get("id"):
            return unit
    return None


def _affordable(snapshot: dict[str, Any]) -> Decimal:
    """What the company can put behind one business today: its daily cap, or a default."""
    capital = snapshot.get("capital") or {}
    cap = capital.get("daily_cap")
    try:
        return Decimal(str(cap)) if cap is not None else DEFAULT_ALLOCATION
    except Exception:  # noqa: BLE001 - a malformed number is not worth failing a cycle over
        return DEFAULT_ALLOCATION
