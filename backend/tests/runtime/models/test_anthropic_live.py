"""T-208: one real call through the Anthropic provider.

Run with ``pytest backend -m integration``. Reads the same configuration as the app
(``.env`` or environment): ANTHROPIC_API_KEY and FRONTIER_MODEL_ID; skipped without them.
"""

import uuid

import pytest
from anthropic import AsyncAnthropic
from pydantic import BaseModel

from autora.infra.settings import SettingsError, load_settings
from autora.runtime.models.providers.anthropic import AnthropicProvider
from autora.runtime.models.router import FREE, ModelBinding
from autora.runtime.models.types import (
    CallContext,
    Message,
    ModelRequest,
    TextBlock,
    ToolDefinition,
    ToolResultBlock,
)


def _config() -> tuple[str, str] | None:
    """(api key, frontier model id) from settings, whatever MODEL_PROVIDER says."""
    try:
        settings = load_settings()
    except SettingsError:
        return None
    if settings.anthropic_api_key is None or not settings.frontier_model_id:
        return None
    return settings.anthropic_api_key.get_secret_value(), settings.frontier_model_id


CONFIG = _config()
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(CONFIG is None, reason="needs ANTHROPIC_API_KEY and FRONTIER_MODEL_ID"),
]


class Headline(BaseModel):
    title_zh_tw: str
    title_en: str


def _binding() -> ModelBinding:
    return ModelBinding(provider="anthropic", model_id=CONFIG[1], price=FREE)


def _context() -> CallContext:
    return CallContext(company_id=uuid.uuid4(), role="writer")


async def test_structured_output_live():
    provider = AnthropicProvider(AsyncAnthropic(api_key=CONFIG[0]))
    response = await provider.complete(
        ModelRequest(
            capability="drafting",
            context=_context(),
            messages=[
                Message(
                    role="user",
                    content=[TextBlock(text="Write a headline about a new EU AI regulation.")],
                )
            ],
            output_model=Headline,
            max_output_tokens=2000,
        ),
        _binding(),
    )
    assert response.stop_reason == "end_turn"
    text = "".join(b.text for b in response.content if isinstance(b, TextBlock))
    Headline.model_validate_json(text)
    assert response.usage.input_tokens > 0


async def test_tool_loop_live():
    """Thinking blocks from turn one must be accepted back unchanged in turn two."""
    provider = AnthropicProvider(AsyncAnthropic(api_key=CONFIG[0]))
    tool = ToolDefinition(
        name="get_time",
        description="Current UTC time",
        input_schema={"type": "object", "properties": {}},
    )
    messages = [Message(role="user", content=[TextBlock(text="What time is it? Use the tool.")])]

    def request():
        return ModelRequest(
            capability="drafting",
            context=_context(),
            messages=messages,
            tools=[tool],
            max_output_tokens=2000,
        )

    first = await provider.complete(request(), _binding())
    assert first.stop_reason == "tool_use"
    [use] = [b for b in first.content if b.type == "tool_use"]
    messages.append(Message(role="assistant", content=first.content))
    messages.append(
        Message(
            role="user",
            content=[ToolResultBlock(tool_use_id=use.id, content="2026-09-18T06:00:00Z")],
        )
    )
    second = await provider.complete(request(), _binding())
    assert second.stop_reason == "end_turn"
