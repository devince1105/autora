"""D-005: real calls to NVIDIA Build through the OpenAI-compatible provider.

Run with ``pytest backend -m integration``. Needs NVIDIA_API_KEY (``.env`` or environment);
the model is FRONTIER_MODEL_ID when MODEL_PROVIDER=nvidia, otherwise ``z-ai/glm-5.3``.
Free tier: rate-limited, inputs and outputs are recorded by NVIDIA (trial terms).
"""

import json
import uuid

import pytest
from pydantic import BaseModel

from autora.infra.settings import SettingsError, load_settings
from autora.runtime.models.providers.openai_compat import OpenAICompatibleProvider
from autora.runtime.models.router import FREE, ModelBinding
from autora.runtime.models.types import (
    CallContext,
    Message,
    ModelRequest,
    TextBlock,
    ToolDefinition,
    ToolResultBlock,
    ToolUseBlock,
)

DEFAULT_MODEL = "z-ai/glm-5.3"


def _config():
    try:
        settings = load_settings()
    except SettingsError:
        return None
    if settings.nvidia_api_key is None:
        return None
    model = settings.frontier_model_id if settings.model_provider == "nvidia" else DEFAULT_MODEL
    return settings, model


CONFIG = _config()
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(CONFIG is None, reason="needs NVIDIA_API_KEY"),
]


class Headline(BaseModel):
    title_zh_tw: str
    title_en: str


def _provider() -> OpenAICompatibleProvider:
    settings, _ = CONFIG
    return OpenAICompatibleProvider(
        name="nvidia",
        base_url=settings.nvidia_base_url,
        api_key=settings.nvidia_api_key.get_secret_value(),
    )


def _binding() -> ModelBinding:
    return ModelBinding(provider="nvidia", model_id=CONFIG[1], price=FREE)


def _request(messages, **kw) -> ModelRequest:
    return ModelRequest(
        capability="drafting",
        context=CallContext(company_id=uuid.uuid4(), role="writer"),
        messages=messages,
        max_output_tokens=4096,
        **kw,
    )


def _text(response) -> str:
    return "".join(b.text for b in response.content if isinstance(b, TextBlock))


async def test_structured_output_live():
    response = await _provider().complete(
        _request(
            [Message.user("為一則關於歐盟 AI 法案的新聞寫標題，繁體中文與英文各一個。")],
            system="You are a newsroom editor. Traditional Chinese (zh-TW) only, never Simplified.",
            output_model=Headline,
        ),
        _binding(),
    )
    assert response.stop_reason == "end_turn", _text(response)
    headline = Headline.model_validate_json(_text(response))
    assert headline.title_zh_tw and headline.title_en
    print(f"\n{response.model_id}: {headline.model_dump_json()} usage={response.usage}")


async def test_tool_loop_live():
    """Tool call, tool result, then a final JSON answer with the schema stated in the prompt."""
    tool = ToolDefinition(
        name="echo_note",
        description="Write your note. Returns its note_id.",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    )

    class Report(BaseModel):
        note_id: str
        message: str

    messages = [Message.user("Call echo_note once with a one-sentence note about AI, then report.")]
    provider, binding = _provider(), _binding()

    first = await provider.complete(_request(messages, tools=[tool], output_model=Report), binding)
    uses = [b for b in first.content if isinstance(b, ToolUseBlock)]
    assert first.stop_reason == "tool_use" and uses, _text(first)
    assert "text" in uses[0].input

    messages += [
        Message(role="assistant", content=first.content),
        Message(
            role="user",
            content=[
                ToolResultBlock(
                    tool_use_id=uses[0].id,
                    content=json.dumps({"note_id": "n-123", "text": uses[0].input["text"]}),
                )
            ],
        ),
    ]
    second = await provider.complete(_request(messages, tools=[tool], output_model=Report), binding)
    assert second.stop_reason == "end_turn", _text(second)
    report = Report.model_validate_json(_text(second).strip().removeprefix("```json").strip("`\n "))
    assert report.note_id == "n-123"
