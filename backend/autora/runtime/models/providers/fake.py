"""Scripted model provider for tests and deterministic demos (logs/3d-office/06 §6).

Replies are looked up by ``(role, task_name, attempt)`` and consumed in order, so one script can
say "the first draft is rejected, the repair passes". Everything else in the system (gateway,
model_calls, cost guard, events, trace) runs for real.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from autora.runtime.models.providers.base import ProviderError
from autora.runtime.models.router import ModelBinding
from autora.runtime.models.types import (
    ContentBlock,
    ModelRequest,
    ProviderResponse,
    TextBlock,
    ToolUseBlock,
    Usage,
)

ScriptKey = tuple[str, str | None, int]


class FakeToolUse(BaseModel):
    name: str
    input: dict[str, Any] = {}


class FakeTurn(BaseModel):
    text: str | None = None
    structured: dict[str, Any] | None = None
    """Rendered as the JSON text of the reply (for requests with an output_model)."""
    tool_uses: list[FakeToolUse] = []
    input_tokens: int = Field(default=100, ge=0)
    output_tokens: int = Field(default=50, ge=0)
    delay_s: float = Field(default=0, ge=0)
    error: str | None = None
    """Raise a ProviderError with this error_class instead of replying."""
    fallback_allowed: bool = True


class FakeScriptExhausted(ProviderError):
    def __init__(self, key: ScriptKey, used: int):
        super().__init__(
            "FakeScriptExhausted",
            f"no scripted reply #{used + 1} for role={key[0]!r} task={key[1]!r} attempt={key[2]}",
            fallback_allowed=False,
        )


class FakeModelProvider:
    name = "fake"

    def __init__(self, default: Callable[[ModelRequest], FakeTurn] | None = None):
        self._script: dict[ScriptKey, list[FakeTurn]] = defaultdict(list)
        self._used: dict[ScriptKey, int] = defaultdict(int)
        self._default = default
        self.requests: list[ModelRequest] = []

    def script(self, role: str, task_name: str | None, attempt: int, *turns: FakeTurn) -> None:
        self._script[(role, task_name, attempt)].extend(turns)

    async def complete(self, request: ModelRequest, binding: ModelBinding) -> ProviderResponse:
        self.requests.append(request)
        ctx = request.context
        key: ScriptKey = (ctx.role, ctx.task_name, ctx.attempt)
        turns = self._script.get(key, [])
        index = self._used[key]
        if index < len(turns):
            turn = turns[index]
            self._used[key] += 1
        elif self._default is not None:
            turn = self._default(request)
        else:
            raise FakeScriptExhausted(key, index)

        if turn.delay_s:
            await asyncio.sleep(turn.delay_s)
        if turn.error:
            raise ProviderError(
                turn.error, "scripted failure", fallback_allowed=turn.fallback_allowed
            )

        content: list[ContentBlock] = []
        if turn.structured is not None:
            content.append(TextBlock(text=json.dumps(turn.structured, ensure_ascii=False)))
        elif turn.text is not None:
            content.append(TextBlock(text=turn.text))
        for n, use in enumerate(turn.tool_uses):
            call_id = f"fake_{ctx.run_id or 'adhoc'}_{ctx.step_seq}_{n}"
            content.append(ToolUseBlock(id=call_id, name=use.name, input=use.input))

        return ProviderResponse(
            content=content,
            stop_reason="tool_use" if turn.tool_uses else "end_turn",
            usage=Usage(input_tokens=turn.input_tokens, output_tokens=turn.output_tokens),
            model_id=binding.model_id,
        )
