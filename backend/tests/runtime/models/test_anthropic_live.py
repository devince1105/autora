"""T-208: one real call through the Anthropic provider.

Run with ``pytest backend -m integration``; needs ANTHROPIC_API_KEY and FRONTIER_MODEL_ID.
"""

import os
import uuid

import pytest
from anthropic import AsyncAnthropic
from pydantic import BaseModel

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

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (os.getenv("ANTHROPIC_API_KEY") and os.getenv("FRONTIER_MODEL_ID")),
        reason="needs ANTHROPIC_API_KEY and FRONTIER_MODEL_ID",
    ),
]


class Headline(BaseModel):
    title_zh_tw: str
    title_en: str


def _binding() -> ModelBinding:
    return ModelBinding(provider="anthropic", model_id=os.environ["FRONTIER_MODEL_ID"], price=FREE)


def _context() -> CallContext:
    return CallContext(company_id=uuid.uuid4(), role="writer")


async def test_structured_output_live():
    provider = AnthropicProvider(AsyncAnthropic())
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
    provider = AnthropicProvider(AsyncAnthropic())
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
