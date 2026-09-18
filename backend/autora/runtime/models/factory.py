"""Build the model gateway from configuration (T-208)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from autora.infra.settings import Settings
from autora.runtime.models.gateway import CostGuard, ModelGateway, NoCostGuard
from autora.runtime.models.providers.base import ModelProvider
from autora.runtime.models.router import router_from_settings


def providers_from_settings(settings: Settings) -> dict[str, ModelProvider]:
    if settings.model_provider == "fake":
        from autora.runtime.models.providers.fake import FakeModelProvider

        return {"fake": FakeModelProvider()}

    from anthropic import AsyncAnthropic

    from autora.runtime.models.providers.anthropic import AnthropicProvider

    assert settings.anthropic_api_key is not None  # Settings validates this
    client = AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value())
    return {
        "anthropic": AnthropicProvider(client, server_fallbacks=settings.anthropic_server_fallbacks)
    }


def gateway_from_settings(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    cost_guard: CostGuard | None = None,
) -> ModelGateway:
    return ModelGateway(
        router=router_from_settings(settings),
        providers=providers_from_settings(settings),
        session_factory=session_factory,
        cost_guard=cost_guard or NoCostGuard(),
    )
