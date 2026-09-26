"""Build the model gateway from configuration (T-208)."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from autora.infra.settings import Settings
from autora.runtime.models.embeddings import Embedder
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

    if settings.model_provider == "nvidia":
        from autora.runtime.models.providers.openai_compat import OpenAICompatibleProvider

        assert settings.nvidia_api_key is not None  # Settings validates this
        return {
            "nvidia": OpenAICompatibleProvider(
                name="nvidia",
                base_url=settings.nvidia_base_url,
                api_key=settings.nvidia_api_key.get_secret_value(),
                timeout_s=settings.nvidia_timeout_seconds,
            )
        }

    if settings.model_provider == "gemini":
        from autora.runtime.models.providers.openai_compat import OpenAICompatibleProvider

        # Google's OpenAI-compatible endpoint: the same adapter as NVIDIA's (D-039)
        assert settings.gemini_api_key is not None  # Settings validates this
        return {
            "gemini": OpenAICompatibleProvider(
                name="gemini",
                base_url=settings.gemini_base_url,
                api_key=settings.gemini_api_key.get_secret_value(),
                timeout_s=settings.gemini_timeout_seconds,
                # its thinking is billed as output and is on unless asked otherwise
                extra_body=(
                    {"reasoning_effort": settings.gemini_reasoning_effort}
                    if settings.gemini_reasoning_effort
                    else None
                ),
            )
        }

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


FAKE_EMBED_MODEL = "hashing-v1"


def embedder_from_settings(settings: Settings | None, *, dim: int) -> Embedder:
    """The ``embed`` binding (T-503): EMBED_PROVIDER / EMBED_MODEL_ID, priced from MODEL_PRICES
    (``input`` per million tokens; unpriced means free)."""
    from decimal import Decimal

    from autora.runtime.models.embeddings import (
        Embedder,
        HashingEmbeddings,
        OpenAICompatibleEmbeddings,
    )

    if settings is None or settings.embed_provider == "fake":
        return Embedder(provider=HashingEmbeddings(dim), model_id=FAKE_EMBED_MODEL, dim=dim)
    assert settings.embed_model_id and settings.nvidia_api_key  # Settings validates this
    price = settings.model_prices.get(settings.embed_model_id, {}).get("input", 0)
    return Embedder(
        provider=OpenAICompatibleEmbeddings(
            name="nvidia",
            base_url=settings.nvidia_base_url,
            api_key=settings.nvidia_api_key.get_secret_value(),
            timeout_s=min(settings.nvidia_timeout_seconds, 60.0),
        ),
        model_id=settings.embed_model_id,
        dim=dim,
        price_per_mtok=Decimal(str(price)),
    )
