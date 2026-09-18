"""The simulated model for echo tasks (``MODEL_PROVIDER=fake``).

It reads its prompt like a real model would: the first message carries the task input as JSON
after ``Input:``, and the last message tells it whether ``echo_note`` has already answered.

``params.pause`` makes one reply slow, for the crash-recovery test (T-215):
``{"task": "echo_research", "attempt": 1, "seconds": 30}`` delays the final reply of that task's
first attempt, i.e. after its note is committed.
"""

from __future__ import annotations

import json
from typing import Any

from autora.domains.echo.workflow import ROLES
from autora.runtime.models.providers.fake import FakeToolUse, FakeTurn
from autora.runtime.models.types import ModelRequest, TextBlock, ToolResultBlock


def respond(request: ModelRequest) -> FakeTurn | None:
    """A reply for echo tasks; None for anything else."""
    ctx = request.context
    if ctx.task_name not in ROLES:
        return None
    params = _task_input(request).get("params", {})
    topic = params.get("topic", "the topic")

    note = _last_tool_result(request)
    if note is None:
        return FakeTurn(
            text="I'll write my note.",
            tool_uses=[FakeToolUse(name="echo_note", input={"text": f"{ctx.role} on {topic}"})],
        )
    if note.is_error:
        return FakeTurn(text=f"echo_note failed: {note.content}")

    pause = params.get("pause") or {}
    delay = 0.0
    if pause.get("task") == ctx.task_name and pause.get("attempt", 1) == ctx.attempt:
        delay = float(pause.get("seconds", 0))
    written = json.loads(note.content)
    return FakeTurn(
        structured={"note_id": written["note_id"], "message": written["text"]}, delay_s=delay
    )


def _task_input(request: ModelRequest) -> dict[str, Any]:
    first = request.messages[0].content[0]
    if not isinstance(first, TextBlock) or "Input:\n" not in first.text:
        return {}
    raw = first.text.split("Input:\n", 1)[1]
    value, _ = json.JSONDecoder().raw_decode(raw)
    return value if isinstance(value, dict) else {}


def _last_tool_result(request: ModelRequest) -> ToolResultBlock | None:
    last = request.messages[-1]
    if last.role != "user":
        return None
    results = [b for b in last.content if isinstance(b, ToolResultBlock)]
    return results[-1] if results else None
