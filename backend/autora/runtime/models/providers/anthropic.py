"""Anthropic provider (T-208). Translates provider-neutral requests to the Messages API.

Choices, all from the Claude API reference bundled with Claude Code:

- **No model ids here.** The model comes from the binding (configuration).
- **Thinking**: the ``thinking`` parameter is omitted. Current models then run adaptive thinking,
  and some reject explicit configurations. Thinking and other model-internal blocks come back as
  ``OpaqueBlock``s and are sent back **unchanged** on the next turn (required in tool loops).
- **Refusals** map to ``stop_reason="refusal"``; the agent decides what to do with them.
- **Server-side fallbacks** (``fallbacks: "default"``, beta ``server-side-fallback-2026-07-01``)
  are on by default: on a policy decline the API re-runs the request on a fallback model inside
  the same call. The served model is reported as ``model_id`` so cost uses its price.
  After a mid-output fallback, blocks before the last ``fallback`` marker that must not be echoed
  (thinking, redacted thinking, tool_use) are omitted when the turn is sent back.
- **Structured output** sends ``output_config.format`` (json_schema) with the SDK's
  ``transform_schema`` applied to the pydantic schema. ``messages.parse`` is not used: it raises
  on replies that are not valid JSON (refusals, max_tokens), and the gateway validates anyway.
- **Prompt caching**: automatic (top-level ``cache_control``).
- **Errors** become ``ProviderError``; outages, overload, rate limits and timeouts allow the
  router's fallback alias, invalid requests and auth failures do not. The SDK already retries
  transient errors before raising.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import anthropic
from anthropic import AsyncAnthropic

from autora.runtime.models.providers.base import ProviderError
from autora.runtime.models.router import ModelBinding
from autora.runtime.models.types import (
    ContentBlock,
    Message,
    ModelRequest,
    OpaqueBlock,
    ProviderResponse,
    StopReason,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
)

PROVIDER = "anthropic"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
_NOT_ECHOED_BEFORE_FALLBACK = {"thinking", "redacted_thinking", "tool_use"}

_STOP_REASONS: dict[str, StopReason] = {
    "end_turn": "end_turn",
    "tool_use": "tool_use",
    "max_tokens": "max_tokens",
    "model_context_window_exceeded": "max_tokens",
    "refusal": "refusal",
}


class AnthropicProvider:
    name = PROVIDER

    def __init__(self, client: AsyncAnthropic, *, server_fallbacks: bool = True):
        self.client = client
        self.server_fallbacks = server_fallbacks

    async def complete(self, request: ModelRequest, binding: ModelBinding) -> ProviderResponse:
        params = _params(request, binding)
        params["max_tokens"] = request.max_output_tokens
        params["cache_control"] = {"type": "ephemeral"}
        if self.server_fallbacks:
            params["betas"] = [FALLBACK_BETA]
            params["fallbacks"] = "default"
        async with _provider_errors():
            response = await self.client.beta.messages.create(**params)

        usage = response.usage
        return ProviderResponse(
            content=[_block(b) for b in response.content],
            stop_reason=_STOP_REASONS.get(response.stop_reason or "", "other"),
            usage=Usage(
                input_tokens=usage.input_tokens or 0,
                output_tokens=usage.output_tokens or 0,
                cache_read_tokens=getattr(usage, "cache_read_input_tokens", None) or 0,
                cache_write_tokens=getattr(usage, "cache_creation_input_tokens", None) or 0,
            ),
            model_id=response.model,
        )

    async def count_tokens(self, request: ModelRequest, binding: ModelBinding) -> int:
        """Exact input tokens for this request (free API call). Not used by the cost guard,
        which keeps its local, deliberately high estimate to avoid a round trip per call."""
        async with _provider_errors():
            counted = await self.client.beta.messages.count_tokens(**_params(request, binding))
        return counted.input_tokens


def _params(request: ModelRequest, binding: ModelBinding) -> dict[str, Any]:
    """The parts of a request shared by ``create`` and ``count_tokens``."""
    params: dict[str, Any] = {
        "model": binding.model_id,
        "messages": [_message(m) for m in request.messages],
    }
    if request.system:
        params["system"] = request.system
    if request.tools:
        params["tools"] = [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema}
            for t in request.tools
        ]
    if request.output_model is not None:
        schema = anthropic.transform_schema(request.output_model.model_json_schema())
        params["output_config"] = {"format": {"type": "json_schema", "schema": schema}}
    return params


@asynccontextmanager
async def _provider_errors() -> AsyncIterator[None]:
    try:
        yield
    except anthropic.APIConnectionError as exc:  # includes timeouts
        raise ProviderError(type(exc).__name__, str(exc), fallback_allowed=True) from exc
    except (anthropic.RateLimitError, anthropic.InternalServerError) as exc:
        raise ProviderError(type(exc).__name__, str(exc), fallback_allowed=True) from exc
    except (
        anthropic.BadRequestError,
        anthropic.AuthenticationError,
        anthropic.PermissionDeniedError,
        anthropic.NotFoundError,
        anthropic.UnprocessableEntityError,
    ) as exc:
        raise ProviderError(type(exc).__name__, str(exc), fallback_allowed=False) from exc
    except anthropic.APIStatusError as exc:
        # 529 overloaded and other 5xx: this model is the problem, a fallback may work.
        raise ProviderError(
            type(exc).__name__, str(exc), fallback_allowed=exc.status_code >= 500
        ) from exc


def _block(block: Any) -> ContentBlock:
    kind = getattr(block, "type", None)
    if kind == "text":
        return TextBlock(text=block.text)
    if kind == "tool_use":
        return ToolUseBlock(id=block.id, name=block.name, input=dict(block.input))
    # thinking, redacted_thinking, fallback, server tool blocks...: keep exactly as received.
    return OpaqueBlock(provider=PROVIDER, data=block.to_dict(mode="json"))


def _message(message: Message) -> dict[str, Any]:
    blocks = list(message.content)
    if message.role == "assistant":
        blocks = _after_fallback_boundary(blocks)
    content = [out for b in blocks if (out := _block_param(b)) is not None]
    return {"role": message.role, "content": content}


def _block_param(block: ContentBlock) -> dict[str, Any] | None:
    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text}
    if isinstance(block, ToolUseBlock):
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    if isinstance(block, ToolResultBlock):
        return {
            "type": "tool_result",
            "tool_use_id": block.tool_use_id,
            "content": block.content,
            "is_error": block.is_error,
        }
    if isinstance(block, OpaqueBlock) and block.provider == PROVIDER:
        return block.data
    return None  # another provider's internal block: not ours to send


def _after_fallback_boundary(blocks: list[ContentBlock]) -> list[ContentBlock]:
    """After a mid-output fallback, omit blocks before the last ``fallback`` marker that the API
    must not receive back (thinking, redacted thinking, tool_use)."""
    last = max(
        (
            i
            for i, b in enumerate(blocks)
            if isinstance(b, OpaqueBlock) and b.data.get("type") == "fallback"
        ),
        default=None,
    )
    if last is None:
        return blocks
    kept = []
    for i, block in enumerate(blocks):
        if i < last and _kind(block) in _NOT_ECHOED_BEFORE_FALLBACK:
            continue
        kept.append(block)
    return kept


def _kind(block: ContentBlock) -> str:
    return block.data.get("type", "") if isinstance(block, OpaqueBlock) else block.type
