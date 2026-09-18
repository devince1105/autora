"""T-208: Anthropic provider, against a mocked HTTP transport (no network, no key).

A real call is in ``test_anthropic_live``, marked ``integration`` and skipped without a key.
"""

import json
import uuid
from decimal import Decimal

import httpx2 as httpx  # the transport library the anthropic SDK is built on
import pytest
from anthropic import AsyncAnthropic
from pydantic import BaseModel

from autora.infra.settings import SettingsError, load_settings
from autora.runtime.models.factory import providers_from_settings
from autora.runtime.models.providers.anthropic import FALLBACK_BETA, AnthropicProvider
from autora.runtime.models.providers.base import ProviderError
from autora.runtime.models.router import FREE, ModelBinding, Price, router_from_settings
from autora.runtime.models.types import (
    CallContext,
    Message,
    ModelRequest,
    OpaqueBlock,
    TextBlock,
    ToolDefinition,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
)

BINDING = ModelBinding(provider="anthropic", model_id="test-frontier", price=FREE)
THINKING = {"type": "thinking", "thinking": "", "signature": "sig-abc"}


class Headline(BaseModel):
    title: str


class Recorder:
    """httpx transport handler: records requests, replies with a scripted response."""

    def __init__(self, status: int = 200, body: dict | None = None):
        self.status = status
        self.body = body or _message([{"type": "text", "text": "hello"}])
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, json=self.body)

    @property
    def last(self) -> dict:
        return json.loads(self.requests[-1].content)


def _message(content, stop_reason="end_turn", model="test-frontier", **usage):
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 5, **usage},
    }


def _provider(recorder: Recorder, **kw) -> AnthropicProvider:
    client = AsyncAnthropic(
        api_key="test-key",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(recorder)),
        max_retries=0,
    )
    return AnthropicProvider(client, **kw)


def _request(messages=None, **kw) -> ModelRequest:
    return ModelRequest(
        capability="drafting",
        context=CallContext(company_id=uuid.uuid4(), role="writer"),
        messages=messages or [Message(role="user", content=[TextBlock(text="hi")])],
        **kw,
    )


# --- request mapping ---------------------------------------------------------------------


async def test_request_mapping():
    rec = Recorder()
    tool = ToolDefinition(name="search_news", description="Search", input_schema={"type": "object"})
    await _provider(rec).complete(
        _request(system="You are a writer.", tools=[tool], max_output_tokens=1234), BINDING
    )
    body = rec.last
    assert body["model"] == "test-frontier"
    assert body["max_tokens"] == 1234
    assert body["system"] == "You are a writer."
    assert body["messages"] == [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
    assert body["tools"] == [
        {"name": "search_news", "description": "Search", "input_schema": {"type": "object"}}
    ]
    assert body["cache_control"] == {"type": "ephemeral"}
    assert body["fallbacks"] == "default"
    assert "thinking" not in body, "adaptive thinking is the default; never configure it"
    assert FALLBACK_BETA in rec.requests[-1].headers["anthropic-beta"]


async def test_server_fallbacks_can_be_disabled():
    rec = Recorder()
    await _provider(rec, server_fallbacks=False).complete(_request(), BINDING)
    assert "fallbacks" not in rec.last
    assert "anthropic-beta" not in rec.requests[-1].headers


async def test_structured_output_sends_a_json_schema():
    rec = Recorder(body=_message([{"type": "text", "text": '{"title": "x"}'}]))
    await _provider(rec).complete(_request(output_model=Headline), BINDING)
    fmt = rec.last["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["schema"]["properties"]["title"]["type"] == "string"
    assert fmt["schema"]["required"] == ["title"]
    assert fmt["schema"]["additionalProperties"] is False


async def test_invalid_structured_reply_is_left_to_the_gateway():
    """A refusal has no JSON; the provider must not raise (the gateway decides)."""
    rec = Recorder(body=_message([{"type": "text", "text": "I can't"}], stop_reason="refusal"))
    response = await _provider(rec).complete(_request(output_model=Headline), BINDING)
    assert response.stop_reason == "refusal"
    assert response.content == [TextBlock(text="I can't")]


async def test_tool_loop_history_round_trips_opaque_blocks_unchanged():
    rec = Recorder()
    history = [
        Message(role="user", content=[TextBlock(text="find news")]),
        Message(
            role="assistant",
            content=[
                OpaqueBlock(provider="anthropic", data=THINKING),
                OpaqueBlock(provider="other-vendor", data={"type": "secret"}),
                ToolUseBlock(id="tu_1", name="search_news", input={"q": "AI"}),
            ],
        ),
        Message(
            role="user",
            content=[ToolResultBlock(tool_use_id="tu_1", content="[]", is_error=False)],
        ),
    ]
    await _provider(rec).complete(_request(messages=history), BINDING)
    assistant, tool_result = rec.last["messages"][1:]
    assert assistant["content"] == [
        THINKING,  # exactly as received, other vendors' blocks dropped
        {"type": "tool_use", "id": "tu_1", "name": "search_news", "input": {"q": "AI"}},
    ]
    assert tool_result["content"] == [
        {"type": "tool_result", "tool_use_id": "tu_1", "content": "[]", "is_error": False}
    ]


async def test_blocks_before_a_mid_output_fallback_are_not_echoed():
    rec = Recorder()
    fallback = {"type": "fallback", "model": "test-fast"}
    history = [
        Message(role="user", content=[TextBlock(text="q")]),
        Message(
            role="assistant",
            content=[
                OpaqueBlock(provider="anthropic", data=THINKING),
                TextBlock(text="partial"),
                ToolUseBlock(id="tu_0", name="x", input={}),
                OpaqueBlock(provider="anthropic", data=fallback),
                OpaqueBlock(provider="anthropic", data={**THINKING, "signature": "sig-2"}),
                TextBlock(text="final"),
            ],
        ),
        Message(role="user", content=[TextBlock(text="and?")]),
    ]
    await _provider(rec).complete(_request(messages=history), BINDING)
    assert rec.last["messages"][1]["content"] == [
        {"type": "text", "text": "partial"},
        fallback,
        {**THINKING, "signature": "sig-2"},
        {"type": "text", "text": "final"},
    ]


async def test_count_tokens_sends_the_same_request_without_generation_options():
    rec = Recorder(body={"input_tokens": 321})
    tokens = await _provider(rec).count_tokens(
        _request(system="sys", output_model=Headline), BINDING
    )
    assert tokens == 321
    assert rec.requests[-1].url.path.endswith("/messages/count_tokens")
    body = rec.last
    assert body["system"] == "sys" and "output_config" in body
    assert not {"max_tokens", "fallbacks", "cache_control"} & body.keys()


# --- response mapping --------------------------------------------------------------------


async def test_response_mapping():
    rec = Recorder(
        body=_message(
            [
                THINKING,
                {"type": "text", "text": "Searching."},
                {"type": "tool_use", "id": "tu_1", "name": "search_news", "input": {"q": "AI"}},
            ],
            stop_reason="tool_use",
            model="test-fast",
            cache_read_input_tokens=100,
            cache_creation_input_tokens=7,
        )
    )
    response = await _provider(rec).complete(_request(), BINDING)
    assert response.content == [
        OpaqueBlock(provider="anthropic", data=THINKING),
        TextBlock(text="Searching."),
        ToolUseBlock(id="tu_1", name="search_news", input={"q": "AI"}),
    ]
    assert response.stop_reason == "tool_use"
    assert response.usage == Usage(
        input_tokens=10, output_tokens=5, cache_read_tokens=100, cache_write_tokens=7
    )
    assert response.model_id == "test-fast", "the served model, not the requested one"


@pytest.mark.parametrize(
    ("api", "ours"),
    [
        ("end_turn", "end_turn"),
        ("max_tokens", "max_tokens"),
        ("model_context_window_exceeded", "max_tokens"),
        ("refusal", "refusal"),
        ("pause_turn", "other"),
        ("stop_sequence", "other"),
    ],
)
async def test_stop_reasons(api, ours):
    rec = Recorder(body=_message([{"type": "text", "text": "x"}], stop_reason=api))
    assert (await _provider(rec).complete(_request(), BINDING)).stop_reason == ours


# --- errors ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "error_class", "fallback_allowed"),
    [
        (429, "RateLimitError", True),
        (500, "InternalServerError", True),
        (529, "OverloadedError", True),
        (400, "BadRequestError", False),
        (401, "AuthenticationError", False),
        (403, "PermissionDeniedError", False),
        (404, "NotFoundError", False),
        (413, "RequestTooLargeError", False),
    ],
)
async def test_error_mapping(status, error_class, fallback_allowed):
    rec = Recorder(status=status, body={"type": "error", "error": {"type": "x", "message": "boom"}})
    with pytest.raises(ProviderError) as exc:
        await _provider(rec).complete(_request(), BINDING)
    assert exc.value.error_class == error_class
    assert exc.value.fallback_allowed is fallback_allowed


async def test_connection_errors_allow_fallback():
    def fail(request):
        raise httpx.ConnectError("down")

    client = AsyncAnthropic(
        api_key="k",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(fail)),
        max_retries=0,
    )
    with pytest.raises(ProviderError) as exc:
        await AnthropicProvider(client).complete(_request(), BINDING)
    assert exc.value.fallback_allowed is True


# --- configuration -----------------------------------------------------------------------

PRICES = {
    "test-frontier": {"input": 5, "output": 25, "cache_read": 0.5, "cache_write": 6.25},
    "test-fast": {"input": 1, "output": 5},
}


def _settings(**kw):
    base = {
        "database_url": "postgresql+asyncpg://u:p@localhost:5434/autora",
        "model_provider": "anthropic",
        "anthropic_api_key": "test-key",
        "frontier_model_id": "test-frontier",
        "model_prices": PRICES,
    }
    return load_settings(_env_file=None, **(base | kw))


def test_prices_are_required_for_configured_models():
    with pytest.raises(SettingsError, match="MODEL_PRICES has no entry for test-fast"):
        _settings(
            fast_model_id="test-fast", model_prices={"test-frontier": PRICES["test-frontier"]}
        )


def test_model_prices_are_read_from_json_env(monkeypatch):
    monkeypatch.setenv("MODEL_PRICES", json.dumps(PRICES))
    settings = load_settings(
        _env_file=None,
        database_url="postgresql+asyncpg://u:p@localhost:5434/autora",
        model_provider="anthropic",
        anthropic_api_key="test-key",
        frontier_model_id="test-frontier",
    )
    assert settings.model_prices["test-fast"] == {"input": 1, "output": 5}


def test_router_from_anthropic_settings():
    router = router_from_settings(_settings(fast_model_id="test-fast"))
    alias = router.resolve("writer", "drafting")
    frontier = router.aliases[alias]
    assert (alias, frontier.provider, frontier.model_id) == (
        "frontier",
        "anthropic",
        "test-frontier",
    )
    assert frontier.price == Price(
        input=5, output=25, cache_read=Decimal("0.5"), cache_write=Decimal("6.25")
    )
    assert router.chain("frontier") == ["frontier", "fast"]


def test_single_model_has_no_fallback_alias():
    router = router_from_settings(_settings())
    assert router.chain("frontier") == ["frontier"]


def test_served_model_is_priced_by_its_own_price():
    router = router_from_settings(_settings(fast_model_id="test-fast"))
    frontier = router.aliases["frontier"]
    assert router.price_for("test-fast", frontier.price).input == 1
    assert router.price_for("unlisted", frontier.price) == frontier.price


def test_provider_factory():
    providers = providers_from_settings(_settings())
    assert isinstance(providers["anthropic"], AnthropicProvider)
    assert providers["anthropic"].server_fallbacks is True
