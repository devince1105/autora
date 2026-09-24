"""D-005: OpenAI-compatible provider (NVIDIA Build) against a mocked HTTP transport."""

import json
import uuid

import httpx
import pytest
from pydantic import BaseModel

from autora.infra.settings import SettingsError, load_settings
from autora.runtime.models.factory import providers_from_settings
from autora.runtime.models.providers.anthropic import AnthropicProvider
from autora.runtime.models.providers.base import ProviderError
from autora.runtime.models.providers.openai_compat import OpenAICompatibleProvider
from autora.runtime.models.router import FREE, ModelBinding, router_from_settings
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

BINDING = ModelBinding(provider="nvidia", model_id="vendor/test-model", price=FREE)
TOOL = ToolDefinition(name="echo_note", description="Write a note", input_schema={"type": "object"})


class Report(BaseModel):
    note_id: str


def _completion(message: dict, finish_reason="stop", model="vendor/test-model", **usage):
    return {
        "id": "cmpl-1",
        "object": "chat.completion",
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", **message},
                     "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 120, "completion_tokens": 30, **usage},
    }  # fmt: skip


class Server:
    """Scripted responses (status, body, headers) in order; records request bodies."""

    def __init__(self, *responses):
        self.responses = list(responses) or [(200, _completion({"content": "hi"}), {})]
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        status, body, headers = (
            self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        )
        return httpx.Response(status, json=body, headers=headers)

    @property
    def last(self) -> dict:
        return json.loads(self.requests[-1].content)


def _provider(server: Server, **kw) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        name="nvidia",
        base_url="https://integrate.example/v1/",
        api_key="nvapi-test",
        client=httpx.AsyncClient(transport=httpx.MockTransport(server)),
        max_retry_wait_s=0,
        **kw,
    )


def _request(messages=None, **kw) -> ModelRequest:
    return ModelRequest(
        capability="reasoning",
        context=CallContext(company_id=uuid.uuid4(), role="writer"),
        messages=messages or [Message.user("hi")],
        **kw,
    )


# --- request -------------------------------------------------------------------------------


async def test_request_mapping_and_auth():
    server = Server()
    await _provider(server).complete(
        _request(system="You write.", tools=[TOOL], max_output_tokens=777), BINDING
    )
    request = server.requests[-1]
    assert str(request.url) == "https://integrate.example/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer nvapi-test"
    body = server.last
    assert body["model"] == "vendor/test-model" and body["max_tokens"] == 777
    assert body["stream"] is False
    assert body["messages"] == [
        {"role": "system", "content": "You write."},
        {"role": "user", "content": "hi"},
    ]
    assert body["tools"] == [
        {"type": "function", "function": {"name": "echo_note", "description": "Write a note",
                                          "parameters": {"type": "object"}}}
    ]  # fmt: skip


async def test_structured_output_without_tools_uses_response_format():
    server = Server()
    await _provider(server).complete(_request(output_model=Report), BINDING)
    fmt = server.last["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["name"] == "Report"
    assert fmt["json_schema"]["schema"]["required"] == ["note_id"]


async def test_structured_output_with_tools_goes_in_the_system_prompt():
    """Constrained decoding could stop the model from calling tools; state the schema instead."""
    server = Server()
    await _provider(server).complete(
        _request(system="You write.", tools=[TOOL], output_model=Report), BINDING
    )
    body = server.last
    assert "response_format" not in body
    system = body["messages"][0]["content"]
    assert system.startswith("You write.") and '"note_id"' in system and "JSON object" in system


async def test_tool_loop_history_mapping():
    server = Server()
    history = [
        Message.user("write a note"),
        Message(
            role="assistant",
            content=[
                OpaqueBlock(provider="nvidia", data={"type": "reasoning", "text": "hmm"}),
                TextBlock(text="Writing."),
                ToolUseBlock(id="call_1", name="echo_note", input={"text": "hello"}),
            ],
        ),
        Message(role="user", content=[ToolResultBlock(tool_use_id="call_1", content='{"id": 1}')]),
    ]
    await _provider(server).complete(_request(messages=history), BINDING)
    assert server.last["messages"] == [
        {"role": "user", "content": "write a note"},
        {"role": "assistant", "content": "Writing.", "tool_calls": [
            {"id": "call_1", "type": "function",
             "function": {"name": "echo_note", "arguments": '{"text": "hello"}'}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": '{"id": 1}'},
    ], "reasoning is not sent back"  # fmt: skip


async def test_a_tool_call_s_thought_signature_goes_back_with_it():
    """D-039: Gemini answers 400 to a history whose tool calls lost their thought_signature."""
    signature = {"google": {"thought_signature": "Es8CCswC"}}
    server = Server(
        (200, _completion(
            {"content": None,
             "tool_calls": [{"id": "call_7", "type": "function", "extra_content": signature,
                             "function": {"name": "echo_note", "arguments": '{"text": "x"}'}},
                            {"id": "call_8", "type": "function",
                             "function": {"name": "echo_note", "arguments": '{"text": "y"}'}}]},
            finish_reason="tool_calls",
        ), {})
    )  # fmt: skip
    provider = _provider(server)
    first = await provider.complete(_request(tools=[TOOL]), BINDING)

    history = [
        Message.user("write a note"),
        Message(role="assistant", content=first.content),
        Message(role="user", content=[
            ToolResultBlock(tool_use_id="call_7", content="{}"),
            ToolResultBlock(tool_use_id="call_8", content="{}"),
        ]),
    ]  # fmt: skip
    await provider.complete(_request(messages=history, tools=[TOOL]), BINDING)
    calls = server.last["messages"][1]["tool_calls"]
    assert calls[0]["extra_content"] == signature, "sent back, unchanged, on the same call"
    assert "extra_content" not in calls[1], "a call that had none gets none"


async def test_another_provider_s_signature_is_not_sent():
    history = [
        Message.user("write a note"),
        Message(role="assistant", content=[
            OpaqueBlock(provider="gemini", data={
                "type": "tool_call_extra", "tool_call_id": "call_1", "extra_content": {"x": 1}}),
            ToolUseBlock(id="call_1", name="echo_note", input={}),
        ]),
    ]  # fmt: skip
    server = Server()
    await _provider(server).complete(_request(messages=history), BINDING)  # this one is "nvidia"
    assert "extra_content" not in server.last["messages"][1]["tool_calls"][0]


# --- response ------------------------------------------------------------------------------


async def test_tool_call_response():
    server = Server(
        (200, _completion(
            {"content": None, "reasoning_content": "I should write it.",
             "tool_calls": [{"id": "call_9", "type": "function",
                             "function": {"name": "echo_note", "arguments": '{"text": "x"}'}}]},
            finish_reason="tool_calls", model="vendor/served-model",
            prompt_tokens_details={"cached_tokens": 20},
        ), {})
    )  # fmt: skip
    response = await _provider(server).complete(_request(tools=[TOOL]), BINDING)
    assert response.content == [
        OpaqueBlock(provider="nvidia", data={"type": "reasoning", "text": "I should write it."}),
        ToolUseBlock(id="call_9", name="echo_note", input={"text": "x"}),
    ]
    assert response.stop_reason == "tool_use"
    assert response.usage == Usage(input_tokens=100, output_tokens=30, cache_read_tokens=20)
    assert response.model_id == "vendor/served-model"


async def test_tool_calls_with_stop_finish_reason_and_bad_arguments():
    server = Server(
        (200, _completion({"tool_calls": [
            {"type": "function", "function": {"name": "echo_note", "arguments": "{not json"}}]},
            finish_reason="stop"), {})
    )  # fmt: skip
    response = await _provider(server).complete(_request(tools=[TOOL]), BINDING)
    [use] = response.content
    assert response.stop_reason == "tool_use"
    assert use.input == {"_unparsed_arguments": "{not json"} and use.id.startswith("call_")


@pytest.mark.parametrize(
    ("finish", "ours"),
    [("stop", "end_turn"), ("length", "max_tokens"), ("content_filter", "refusal"),
     ("weird", "other"), (None, "other")],
)  # fmt: skip
async def test_finish_reasons(finish, ours):
    server = Server((200, _completion({"content": "x"}, finish_reason=finish), {}))
    assert (await _provider(server).complete(_request(), BINDING)).stop_reason == ours


async def test_missing_choices_is_a_provider_error():
    server = Server((200, {"error": "oops"}, {}))
    with pytest.raises(ProviderError) as exc:
        await _provider(server).complete(_request(), BINDING)
    assert exc.value.error_class == "InvalidResponse" and exc.value.fallback_allowed


# --- errors and retries --------------------------------------------------------------------


async def test_rate_limit_is_retried_honouring_retry_after():
    server = Server(
        (429, {"error": {"message": "slow down"}}, {"retry-after": "0"}),
        (200, _completion({"content": "ok"}), {}),
    )
    response = await _provider(server).complete(_request(), BINDING)
    assert response.content == [TextBlock(text="ok")] and len(server.requests) == 2


@pytest.mark.parametrize(
    ("status", "error_class", "fallback_allowed", "calls"),
    [
        (429, "RateLimited", True, 3),
        (503, "ServerError", True, 3),
        (400, "BadRequest", False, 1),
        (401, "AuthenticationError", False, 1),
        (404, "NotFound", False, 1),
    ],
)
async def test_error_mapping(status, error_class, fallback_allowed, calls):
    server = Server((status, {"error": {"message": "boom"}}, {}))
    with pytest.raises(ProviderError) as exc:
        await _provider(server).complete(_request(), BINDING)
    assert (exc.value.error_class, exc.value.fallback_allowed) == (error_class, fallback_allowed)
    assert "boom" in exc.value.message and len(server.requests) == calls


async def test_timeouts_go_to_the_fallback_without_retrying():
    calls = []

    def slow(request):
        calls.append(request)
        raise httpx.ReadTimeout("no reply")

    provider = OpenAICompatibleProvider(
        name="nvidia", base_url="https://x/v1", api_key="k", timeout_s=5,
        client=httpx.AsyncClient(transport=httpx.MockTransport(slow)),
    )  # fmt: skip
    with pytest.raises(ProviderError) as exc:
        await provider.complete(_request(), BINDING)
    assert (exc.value.error_class, exc.value.fallback_allowed) == ("Timeout", True)
    assert len(calls) == 1, "a timed-out request is not sent again"


async def test_connection_errors_allow_fallback():
    def down(request):
        raise httpx.ConnectError("refused")

    provider = OpenAICompatibleProvider(
        name="nvidia", base_url="https://x/v1", api_key="k", max_retries=0,
        client=httpx.AsyncClient(transport=httpx.MockTransport(down)),
    )  # fmt: skip
    with pytest.raises(ProviderError) as exc:
        await provider.complete(_request(), BINDING)
    assert exc.value.fallback_allowed is True


# --- configuration and switching -----------------------------------------------------------

PRICES = {"vendor/big": {"input": 0, "output": 0}, "vendor/fast": {"input": 0, "output": 0}}
BASE = {"database_url": "postgresql+asyncpg://u:p@localhost:5434/autora"}


def _settings(**kw):
    return load_settings(_env_file=None, **(BASE | kw))


def test_nvidia_settings_require_key_and_model():
    with pytest.raises(SettingsError) as exc:
        _settings(model_provider="nvidia")
    assert "NVIDIA_API_KEY" in str(exc.value) and "FRONTIER_MODEL_ID" in str(exc.value)


def test_nvidia_provider_and_router():
    settings = _settings(
        model_provider="nvidia", nvidia_api_key="nvapi-x", frontier_model_id="vendor/big",
        fast_model_id="vendor/fast", model_prices=PRICES,
    )  # fmt: skip
    [provider] = providers_from_settings(settings).values()
    assert isinstance(provider, OpenAICompatibleProvider) and provider.name == "nvidia"
    assert provider.base_url == "https://integrate.api.nvidia.com/v1"
    assert provider.timeout_s == 180
    router = router_from_settings(settings)
    assert router.aliases["frontier"].provider == "nvidia"
    assert router.aliases["frontier"].model_id == "vendor/big"
    assert router.chain("frontier") == ["frontier", "fast"]


def test_gemini_settings_require_key_and_model():
    with pytest.raises(SettingsError) as exc:
        _settings(model_provider="gemini")
    assert "GEMINI_API_KEY" in str(exc.value) and "FRONTIER_MODEL_ID" in str(exc.value)


def test_gemini_goes_through_google_s_openai_compatible_endpoint():
    """D-039: the same adapter as NVIDIA's, pointed at Google."""
    settings = _settings(
        model_provider="gemini", gemini_api_key="AIza-x", frontier_model_id="vendor/big",
        fast_model_id="vendor/fast", model_prices=PRICES,
    )  # fmt: skip
    [provider] = providers_from_settings(settings).values()
    assert isinstance(provider, OpenAICompatibleProvider) and provider.name == "gemini"
    assert provider.base_url == "https://generativelanguage.googleapis.com/v1beta/openai"
    router = router_from_settings(settings)
    assert router.aliases["frontier"].provider == "gemini"
    assert router.chain("frontier") == ["frontier", "fast"]


def test_switching_provider_is_one_setting():
    """Both keys can stay configured; MODEL_PROVIDER picks one."""
    both = {"nvidia_api_key": "nvapi-x", "anthropic_api_key": "sk-x", "gemini_api_key": "AIza-x",
            "model_prices": PRICES, "frontier_model_id": "vendor/big"}  # fmt: skip
    nvidia = providers_from_settings(_settings(model_provider="nvidia", **both))
    anthropic = providers_from_settings(_settings(model_provider="anthropic", **both))
    gemini = providers_from_settings(_settings(model_provider="gemini", **both))
    assert list(nvidia) == ["nvidia"] and list(anthropic) == ["anthropic"]
    assert list(gemini) == ["gemini"]
    assert isinstance(anthropic["anthropic"], AnthropicProvider)
