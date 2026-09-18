"""Provider-neutral request/response types for model calls (logs/platform/09_MODEL_GATEWAY.md).

Agents build ``ModelRequest``s and read ``ModelResponse``s; only provider adapters translate to a
vendor API. No model id appears here or in any agent: requests name a *capability*, the router
turns (role, capability) into an alias, and configuration turns the alias into a model id.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class TextBlock(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ToolUseBlock(BaseModel):
    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    input: dict[str, Any]


class ToolResultBlock(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: str
    is_error: bool = False


class OpaqueBlock(BaseModel):
    """A provider-specific block kept verbatim (e.g. Anthropic ``thinking``, ``fallback``).

    Agents never read it. It stays in the conversation history so the provider that produced it
    gets it back unchanged on the next turn (required for thinking blocks in tool loops); other
    providers skip it.
    """

    type: Literal["opaque"] = "opaque"
    provider: str
    data: dict[str, Any]


ContentBlock = Annotated[
    TextBlock | ToolUseBlock | ToolResultBlock | OpaqueBlock, Field(discriminator="type")
]


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: list[ContentBlock]

    @classmethod
    def user(cls, text: str) -> Message:
        return cls(role="user", content=[TextBlock(text=text)])


class ToolDefinition(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]


class CallContext(BaseModel):
    """Who is calling and on whose budget. Drives routing, cost attribution and fake scripts."""

    model_config = ConfigDict(frozen=True)

    company_id: uuid.UUID
    role: str
    project_id: uuid.UUID | None = None
    agent_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None
    task_name: str | None = None
    attempt: int = 1
    step_seq: int = 0


Capability = Literal[
    "reasoning", "research_extraction", "drafting", "editing", "verification", "embedding"
]


class ModelRequest(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    capability: Capability
    context: CallContext
    messages: list[Message]
    system: str | None = None
    tools: list[ToolDefinition] = []
    output_model: type[BaseModel] | None = None
    """When set, the final text must be JSON valid against this model; the gateway validates it."""
    max_output_tokens: int = Field(default=4096, ge=1)


class Usage(BaseModel):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)


StopReason = Literal["end_turn", "tool_use", "max_tokens", "refusal", "other"]


class ProviderResponse(BaseModel):
    """What an adapter returns: vendor output translated, nothing computed yet."""

    content: list[ContentBlock]
    stop_reason: StopReason
    usage: Usage
    model_id: str


class ModelResponse(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    model_call_id: uuid.UUID
    content: list[ContentBlock]
    stop_reason: StopReason
    usage: Usage
    provider: str
    alias: str
    model_id: str
    cost_usd: Decimal
    latency_ms: int
    parsed: BaseModel | None = None
    output_issues: list[str] = []
    """Why ``output_model`` validation failed; fed back to the model by the repair step."""

    @property
    def text(self) -> str:
        return "".join(b.text for b in self.content if isinstance(b, TextBlock))

    @property
    def tool_uses(self) -> list[ToolUseBlock]:
        return [b for b in self.content if isinstance(b, ToolUseBlock)]
