"""Model routing: (role, capability) -> alias -> binding (provider, model id, price).

Routes are looked up most-specific first: ``role.capability``, ``*.capability``, ``role.*``,
``*.*``. Fallbacks are per alias and only used when a provider error allows it (outage,
overload), never to dodge a budget or a refusal.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, ConfigDict, Field

from autora.infra.settings import Settings
from autora.runtime.models.types import Usage

_MTOK = Decimal(1_000_000)
_CENT_MICROS = Decimal("0.000001")


class ModelConfigError(Exception):
    pass


class Price(BaseModel):
    """USD per million tokens."""

    model_config = ConfigDict(frozen=True)

    input: Decimal = Field(ge=0)
    output: Decimal = Field(ge=0)
    cache_read: Decimal = Field(default=Decimal(0), ge=0)
    cache_write: Decimal = Field(default=Decimal(0), ge=0)

    def cost(self, usage: Usage) -> Decimal:
        total = (
            usage.input_tokens * self.input
            + usage.output_tokens * self.output
            + usage.cache_read_tokens * self.cache_read
            + usage.cache_write_tokens * self.cache_write
        ) / _MTOK
        return total.quantize(_CENT_MICROS, rounding=ROUND_HALF_UP)


FREE = Price(input=Decimal(0), output=Decimal(0))

FAKE_MODEL_ID = "fake-frontier"
"""The model id simulation runs on. Priced through MODEL_PRICES like any other."""


class ModelBinding(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str
    model_id: str
    price: Price


class ModelRouter:
    def __init__(
        self,
        aliases: Mapping[str, ModelBinding],
        routes: Mapping[str, str],
        fallbacks: Mapping[str, list[str]] | None = None,
        prices: Mapping[str, Price] | None = None,
    ):
        self.aliases = dict(aliases)
        self.prices = dict(prices or {})
        """Price by model id, for replies served by a different model than requested."""
        self.routes = dict(routes)
        self.fallbacks = {k: list(v) for k, v in (fallbacks or {}).items()}
        self._validate()

    def _validate(self) -> None:
        if not self.aliases:
            raise ModelConfigError("at least one model alias is required")
        for key, alias in self.routes.items():
            if key.count(".") != 1:
                raise ModelConfigError(f"route key must be 'role.capability': {key!r}")
            if alias not in self.aliases:
                raise ModelConfigError(f"route {key!r} points at unknown alias {alias!r}")
        for alias, chain in self.fallbacks.items():
            for target in [alias, *chain]:
                if target not in self.aliases:
                    raise ModelConfigError(f"fallback chain of {alias!r} names unknown {target!r}")
            if alias in chain:
                raise ModelConfigError(f"alias {alias!r} cannot fall back to itself")

    def resolve(self, role: str, capability: str) -> str:
        for key in (f"{role}.{capability}", f"*.{capability}", f"{role}.*", "*.*"):
            if key in self.routes:
                return self.routes[key]
        raise ModelConfigError(f"no route for role={role!r} capability={capability!r}")

    def binding(self, alias: str) -> ModelBinding:
        try:
            return self.aliases[alias]
        except KeyError:
            raise ModelConfigError(f"unknown model alias {alias!r}") from None

    def chain(self, alias: str) -> list[str]:
        return [alias, *self.fallbacks.get(alias, [])]

    def price_for(self, model_id: str, default: Price) -> Price:
        """Price of the model that actually served a reply (server-side fallbacks can differ)."""
        for binding in self.aliases.values():
            if binding.model_id == model_id:
                return binding.price
        return self.prices.get(model_id, default)


def router_from_settings(settings: Settings) -> ModelRouter:
    """Build the router from configuration. Model ids only ever come from settings."""
    if settings.model_provider == "fake":
        # Deterministic simulation: everything routes to the scripted fake provider, free —
        # unless MODEL_PRICES says what the simulated model costs.
        #
        # **A free model cannot exhaust a budget.** The guard refuses a call when spend plus its
        # estimate passes a cap, and both are zero at every price of zero, so the budget gate
        # (AC-13) could never fire in simulation. Letting the configuration price the simulated
        # model is what makes that gate testable without spending real money.
        priced = settings.model_prices.get(FAKE_MODEL_ID)
        return ModelRouter(
            aliases={
                "frontier": ModelBinding(
                    provider="fake",
                    model_id=FAKE_MODEL_ID,
                    price=Price(**priced) if priced else FREE,
                )
            },
            routes={"*.*": "frontier"},
        )
    if settings.model_provider in ("anthropic", "nvidia", "gemini", "openai"):
        provider = settings.model_provider
        prices = {model_id: Price(**entry) for model_id, entry in settings.model_prices.items()}
        frontier = settings.frontier_model_id
        assert frontier is not None  # Settings validates this
        fast = settings.fast_model_id or frontier
        aliases = {
            "frontier": ModelBinding(provider=provider, model_id=frontier, price=prices[frontier]),
            "fast": ModelBinding(provider=provider, model_id=fast, price=prices[fast]),
        }
        fallbacks = {"frontier": ["fast"]} if fast != frontier else {}
        return ModelRouter(
            aliases=aliases, routes={"*.*": "frontier"}, fallbacks=fallbacks, prices=prices
        )
    raise ModelConfigError(f"unsupported MODEL_PROVIDER={settings.model_provider!r}")
