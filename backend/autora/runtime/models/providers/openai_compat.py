"""OpenAI-compatible chat completions provider: NVIDIA Build and similar endpoints (D-005).

One class, parameterised by name and base URL, speaks ``POST {base_url}/chat/completions``:
NVIDIA's API catalog (``https://integrate.api.nvidia.com/v1``), a self-hosted NIM, or any other
OpenAI-compatible server. Plain httpx, no vendor SDK.

Translation choices:

- **Tool calls** use ``tools`` / ``tool_calls``; a tool result becomes a ``role: tool`` message.
  Arguments the model produced as invalid JSON are passed through as ``{"_unparsed_arguments":
  "<raw>"}`` so the tool registry rejects them and the model sees why.
- **Structured output**: with no tools offered the schema is enforced by the server
  (``response_format: json_schema``). With tools offered it is stated in the system prompt
  instead, because constrained decoding on such servers can suppress tool calls. Either way the
  gateway validates the reply and the runner repairs it (T-207 / T-211).
- **Reasoning** text (``reasoning_content`` / ``reasoning``) is kept as an ``OpaqueBlock`` for the
  trace and not sent back: these APIs do not expect it in later turns.
- A tool call's **``extra_content``** is kept as an ``OpaqueBlock`` too, and *is* sent back, on the
  same call, next turn. Gemini puts a ``thought_signature`` there and refuses (HTTP 400) a
  history whose tool calls come back without it (D-039); servers that send none get none.
- **Usage**: cached prompt tokens are reported as ``cache_read_tokens`` and not also as input.
- **Errors**: timeouts, connection errors, 429 and 5xx allow the router's fallback alias; other
  4xx do not. 429, 5xx and connection errors are retried briefly first (honouring
  ``Retry-After``: free tiers rate-limit per minute). A **timeout is not retried**: a model that
  did not answer within ``timeout_s`` is overloaded, and waiting that long again only delays the
  fallback (measured on NVIDIA's free glm-5.3 endpoint: 224 s for a one-word reply).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import httpx

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

NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"

TOOL_CALL_EXTRA = "tool_call_extra"
"""An ``OpaqueBlock`` holding one tool call's ``extra_content``, to be sent back with it."""

_STOP_REASONS: dict[str, StopReason] = {
    "stop": "end_turn",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "length": "max_tokens",
    "content_filter": "refusal",
}
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        api_key: str,
        client: httpx.AsyncClient | None = None,
        timeout_s: float = 180.0,
        max_retries: int = 2,
        max_retry_wait_s: float = 30.0,
        extra_body: dict[str, Any] | None = None,
    ):
        self.name = name
        self.extra_body = dict(extra_body or {})
        """Sent with every request: a provider's own knobs (Gemini's ``reasoning_effort``)."""
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self.max_retry_wait_s = max_retry_wait_s
        self.timeout_s = timeout_s
        self._headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
        self.client = client or httpx.AsyncClient(timeout=timeout_s)

    async def complete(self, request: ModelRequest, binding: ModelBinding) -> ProviderResponse:
        body = self._body(request, binding)
        data = await self._post(body)
        return self._response(data, binding)

    # --- request ---------------------------------------------------------------------------

    def _body(self, request: ModelRequest, binding: ModelBinding) -> dict[str, Any]:
        system = request.system or ""
        body: dict[str, Any] = {
            "model": binding.model_id,
            "max_tokens": request.max_output_tokens,
            "stream": False,
            **self.extra_body,
        }
        if request.tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.input_schema,
                    },
                }
                for t in request.tools
            ]
        if request.output_model is not None:
            schema = request.output_model.model_json_schema()
            if request.tools:
                system = (
                    f"{system}\n\nWhen you give your final answer (not a tool call), reply with "
                    "only a JSON object, no prose and no code fence, valid against this JSON "
                    f"schema:\n{json.dumps(schema, ensure_ascii=False)}"
                ).strip()
            else:
                body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": request.output_model.__name__, "schema": schema},
                }
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}] if system else []
        for message in request.messages:
            messages.extend(self._messages(message))
        body["messages"] = messages
        return body

    def _messages(self, message: Message) -> list[dict[str, Any]]:
        if message.role == "assistant":
            text = "".join(b.text for b in message.content if isinstance(b, TextBlock))
            extras = {
                b.data["tool_call_id"]: b.data["extra_content"]
                for b in message.content
                if isinstance(b, OpaqueBlock)
                and b.provider == self.name
                and b.data.get("type") == TOOL_CALL_EXTRA
            }
            calls = [
                {
                    "id": b.id,
                    "type": "function",
                    "function": {"name": b.name, "arguments": json.dumps(b.input)},
                }
                | ({"extra_content": extras[b.id]} if b.id in extras else {})
                for b in message.content
                if isinstance(b, ToolUseBlock)
            ]
            out: dict[str, Any] = {"role": "assistant", "content": text or None}
            if calls:
                out["tool_calls"] = calls
            return [out]

        results = [
            {"role": "tool", "tool_call_id": b.tool_use_id, "content": b.content}
            for b in message.content
            if isinstance(b, ToolResultBlock)
        ]
        text = "".join(b.text for b in message.content if isinstance(b, TextBlock))
        return results + ([{"role": "user", "content": text}] if text else [])

    # --- transport -------------------------------------------------------------------------

    async def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}/chat/completions"
        for attempt in range(self.max_retries + 1):
            try:
                response = await self.client.post(url, json=body, headers=self._headers)
            except httpx.TimeoutException as exc:
                raise ProviderError(
                    "Timeout", str(exc) or f"no reply within {self.timeout_s}s",
                    fallback_allowed=True,
                ) from exc  # fmt: skip
            except httpx.TransportError as exc:
                error = ProviderError(type(exc).__name__, str(exc), fallback_allowed=True)
            else:
                if response.status_code < 400:
                    return response.json()
                error = ProviderError(
                    _error_class(response.status_code),
                    f"HTTP {response.status_code}: {_error_message(response)}",
                    fallback_allowed=response.status_code in _RETRYABLE_STATUS,
                )
                if response.status_code not in _RETRYABLE_STATUS:
                    raise error
                if attempt < self.max_retries:
                    await asyncio.sleep(self._wait(response, attempt))
                    continue
                raise error
            if attempt < self.max_retries:
                await asyncio.sleep(self._wait(None, attempt))
                continue
            raise error
        raise AssertionError("unreachable")

    def _wait(self, response: httpx.Response | None, attempt: int) -> float:
        header = response.headers.get("retry-after") if response is not None else None
        try:
            wait = float(header) if header is not None else 2.0**attempt
        except ValueError:
            wait = 2.0**attempt
        return min(max(wait, 0.0), self.max_retry_wait_s)

    # --- response --------------------------------------------------------------------------

    def _response(self, data: dict[str, Any], binding: ModelBinding) -> ProviderResponse:
        try:
            choice = data["choices"][0]
            message = choice.get("message") or {}
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(
                "InvalidResponse", f"no choices in response: {str(data)[:500]}",
                fallback_allowed=True,
            ) from exc  # fmt: skip

        content: list[ContentBlock] = []
        reasoning = message.get("reasoning_content") or message.get("reasoning")
        if reasoning:
            content.append(
                OpaqueBlock(provider=self.name, data={"type": "reasoning", "text": reasoning})
            )
        if message.get("content"):
            content.append(TextBlock(text=message["content"]))
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            call_id = call.get("id") or f"call_{uuid.uuid4().hex[:12]}"
            if call.get("extra_content"):
                content.append(
                    OpaqueBlock(
                        provider=self.name,
                        data={
                            "type": TOOL_CALL_EXTRA,
                            "tool_call_id": call_id,
                            "extra_content": call["extra_content"],
                        },
                    )
                )
            content.append(
                ToolUseBlock(
                    id=call_id,
                    name=function.get("name", ""),
                    input=_arguments(function.get("arguments")),
                )
            )

        has_calls = any(isinstance(b, ToolUseBlock) for b in content)
        stop = _STOP_REASONS.get(choice.get("finish_reason") or "", "other")
        if has_calls and stop == "end_turn":
            stop = "tool_use"  # some servers report "stop" alongside tool calls

        usage = data.get("usage") or {}
        cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
        return ProviderResponse(
            content=content,
            stop_reason=stop,
            usage=Usage(
                input_tokens=max((usage.get("prompt_tokens") or 0) - cached, 0),
                output_tokens=_billed_output(usage),
                cache_read_tokens=cached,
            ),
            model_id=data.get("model") or binding.model_id,
        )


def _billed_output(usage: dict[str, Any]) -> int:
    """Output tokens as the provider bills them. OpenAI's convention counts a model's reasoning
    inside ``completion_tokens``; Gemini's compatible endpoint leaves its thinking out of it and
    only in ``total_tokens`` — yet bills it as output. Whatever the total holds beyond prompt and
    completion is that thinking: counted here, or every Gemini call looks several times cheaper
    than it is (it did: 103K output tokens recorded where the bill was far higher)."""
    prompt = usage.get("prompt_tokens") or 0
    completion = usage.get("completion_tokens") or 0
    total = usage.get("total_tokens") or 0
    return completion + max(total - prompt - completion, 0)


def _arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {"_unparsed_arguments": str(raw)}
    return value if isinstance(value, dict) else {"_unparsed_arguments": str(raw)}


def _error_class(status: int) -> str:
    return {
        400: "BadRequest",
        401: "AuthenticationError",
        403: "PermissionDenied",
        404: "NotFound",
        422: "UnprocessableEntity",
        429: "RateLimited",
    }.get(status, "ServerError" if status >= 500 else f"HTTP{status}")


def _error_message(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.text[:500]
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error)[:500]
        return str(data.get("detail") or data.get("message") or data)[:500]
    return str(data)[:500]
