"""Build the model gateway from configuration (T-208)."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from autora.infra.settings import Settings
from autora.runtime.models.gateway import CostGuard, ModelGateway, NoCostGuard
from autora.runtime.models.providers.base import ModelProvider
from autora.runtime.models.providers.fake import FakeModelProvider, FakeTurn
from autora.runtime.models.router import router_from_settings
from autora.runtime.models.types import ModelRequest


def providers_from_settings(
    settings: Settings, *, fake_default: Callable[[ModelRequest], FakeTurn] | None = None
) -> dict[str, ModelProvider]:
    """``fake_default`` answers requests with no scripted reply: the domains' simulations."""
    if settings.model_provider == "fake":
        return {"fake": FakeModelProvider(default=fake_default)}

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
    *,
    fake_default: Callable[[ModelRequest], FakeTurn] | None = None,
) -> ModelGateway:
    return ModelGateway(
        router=router_from_settings(settings),
        providers=providers_from_settings(settings, fake_default=fake_default),
        session_factory=session_factory,
        cost_guard=cost_guard or NoCostGuard(),
    )
