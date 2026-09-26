"""Application settings.

Single source of configuration. Model ids, API keys and URLs live here (from env / .env),
never in code. Every process (api, worker, alembic, tests) calls ``get_settings()`` once;
a missing or inconsistent value fails fast with a readable message.
"""

from __future__ import annotations

import os
import socket
import uuid
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class SettingsError(RuntimeError):
    """Raised when required configuration is missing or inconsistent."""


def env_file() -> str:
    """Locate the .env file regardless of the current working directory.

    Public because a domain reads the same file for its own settings: the core parses what it
    owns and tells the layer above where the configuration lives, rather than carrying that
    layer's knobs (ARCHITECTURE_V2_1 §9).

    Order: AUTORA_ENV_FILE env var → repo root → ./.env relative to cwd.
    This file sits at <root>/backend/autora/infra/settings.py, so the root is
    parents[3] (also /app inside the Docker images).
    """
    override = os.environ.get("AUTORA_ENV_FILE")
    if override:
        return override
    repo_root = Path(__file__).resolve().parents[3]
    candidate = repo_root / ".env"
    return str(candidate) if candidate.is_file() else ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=env_file(),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Runtime environment ---
    autora_env: Literal["dev", "test", "prod"] = "dev"

    # --- Database ---
    database_url: str
    """SQLAlchemy async URL, e.g. postgresql+asyncpg://user:pass@host:5434/db"""
    db_echo: bool = False

    # --- Model provider (T-207/T-208, D-005). Ids come from env, never from code. ---
    model_provider: Literal["fake", "anthropic", "nvidia", "gemini", "openai"] = "fake"
    """fake: simulated, free. anthropic: Claude API. nvidia: NVIDIA Build (OpenAI-compatible).
    gemini: Google Gemini API through its OpenAI-compatible endpoint (D-039). openai: OpenAI's own
    Chat Completions (D-053). Every key may be set at once; this chooses which one is used."""
    anthropic_api_key: SecretStr | None = None
    nvidia_api_key: SecretStr | None = None
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_timeout_seconds: float = Field(default=180.0, gt=0)
    """Per request. On timeout the router's fallback model is tried at once (no retry)."""
    gemini_api_key: SecretStr | None = None
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    gemini_timeout_seconds: float = Field(default=180.0, gt=0)
    """Per request, as for NVIDIA."""
    gemini_reasoning_effort: Literal["low", "medium", "high", ""] = "low"
    """How much Gemini thinks before it answers. Its thinking is billed as output tokens and is
    on by default — one request thought 10,119 tokens to copy a table. "low" keeps it for the
    agents' judgement calls at a fraction of the cost; "" leaves it to Google."""
    openai_api_key: SecretStr | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_timeout_seconds: float = Field(default=180.0, gt=0)
    """Per request, as for NVIDIA."""
    openai_reasoning_effort: Literal["none", "minimal", "low", "medium", "high", ""] = "low"
    """As GEMINI_REASONING_EFFORT: its reasoning is billed as output. "" leaves it to OpenAI."""
    official_trades_enabled: bool = False
    """Transcribe officials' transaction reports ourselves (D-051). Off (D-052): a small company
    points readers to the trackers that already publish these, rather than paying a model to
    read hundreds of scanned pages; the code stays for the day it is worth it."""
    model_daily_cap_usd: Decimal = Field(default=Decimal("3"), ge=0)
    """Every company's model calls together, per UTC day: a call that would pass it is refused
    (the cost guard). The bill's own safety net, apart from each company's budgets, which are
    governance. 0 turns it off."""
    frontier_model_id: str | None = None
    fast_model_id: str | None = None
    model_prices: dict[str, dict[str, float]] = {}
    """USD per million tokens by model id, from MODEL_PRICES as JSON, e.g.
    {"<model id>": {"input": 5, "output": 25, "cache_read": 0.5, "cache_write": 6.25}}."""
    anthropic_server_fallbacks: bool = True
    """Server-side refusal fallbacks (``fallbacks: "default"``); see T-208."""

    # --- Money (D-023) ---
    base_currency: str = Field(default="TWD", pattern="^[A-Z]{3}$")
    """The one currency the ledger, budgets and reports are kept in. The meter (model calls,
    tool costs, per-run caps) stays in USD, because that is what providers charge in."""
    fx_rates: dict[str, Decimal] = {"USD": Decimal("32")}
    """Units of the base currency per one unit of each other currency, from FX_RATES as JSON,
    e.g. {"USD": "32"}. Fixed and updated by hand; every converted ledger row keeps the rate
    it was converted at, so changing it never rewrites the past."""

    # --- Tools (T-500, D-003) ---
    tools_profile: Literal["fixture", "live"] = "fixture"
    """fixture: tools read local fixtures (tests, simulation); live: real web (Tavily, fetch)."""
    tavily_api_key: SecretStr | None = None
    tavily_search_depth: Literal["basic", "advanced"] = "basic"
    """basic costs 1 credit per search, advanced 2."""
    tavily_cost_per_credit: Decimal = Field(default=Decimal("0.008"), ge=0)
    """USD per Tavily credit (pay-as-you-go price); recorded as each search's cost."""
    tavily_timeout_seconds: float = Field(default=15.0, gt=0)
    tavily_requests_per_minute: int = Field(default=60, ge=1)
    # --- Embeddings (T-503): the ``embed`` binding, separate from the agents' model ---
    embed_provider: Literal["fake", "nvidia"] = "fake"
    """fake: deterministic hashing vectors (no network); nvidia: NVIDIA Build's /embeddings."""
    embed_model_id: str | None = None
    """Required unless fake. Its vectors must have the dimension the database stores (2048)."""

    fetch_timeout_seconds: float = Field(default=15.0, gt=0)
    """Live page and feed fetches (T-501, T-502)."""
    fetch_max_bytes: int = Field(default=5_000_000, ge=1)
    fetch_contact_email: str | None = None
    """Who to write to about the newsroom's fetching. SEC refuses automated requests that do not
    name one, in exactly the form "Name email" (D-036); other sites are just as happy with it."""

    # --- The public site's market strip (D-048) ---
    fred_api_key: SecretStr | None = None
    """FRED (St. Louis Fed), free: the US indices' previous close, the 10-year yield and oil.
    Without it the strip shows only Taiwan and crypto."""
    finnhub_api_key: SecretStr | None = None
    """Finnhub, free for personal use: US stocks' latest price on the strip. Without it they are
    left out. Its paid plan (or another licensed feed) before the site is public."""

    # --- Blob storage (T-210) ---
    blob_store_dir: Path = Path(__file__).resolve().parents[3] / "data" / "blobs"
    """LocalFS blob root. Relative paths resolve against the current directory."""

    # --- Readers and the site (D-024, D-025) ---
    site_base_url: str = "http://localhost:3000"
    """Where the public site lives. Login links and payment returns point back into it."""
    email_provider: Literal["console", "resend"] = "console"
    """console: print the message (dev, tests, no network). resend: send it for real."""
    resend_api_key: SecretStr | None = None
    email_from: str = "AiSiWhale <onboarding@resend.dev>"

    # --- Payments: PAYUNi (D-024) ---
    payuni_env: Literal["sandbox", "production"] = "sandbox"
    payuni_mer_id: str | None = None
    payuni_hash_key: SecretStr | None = None
    payuni_hash_iv: SecretStr | None = None
    payuni_return_url: str = "http://localhost:3000/news/zh-TW/membership/return"
    """Where the reader's browser comes back to after paying."""
    payuni_notify_url: str = "http://localhost:8000/api/payments/payuni/notify"
    """Where PAYUNi posts the result, server to server. The one that decides anything."""

    @property
    def payuni_base_url(self) -> str:
        host = "api" if self.payuni_env == "production" else "sandbox-api"
        return f"https://{host}.payuni.com.tw/api"

    # --- API ---
    api_bearer_token: SecretStr = SecretStr("change-me")
    cors_origins: list[str] = ["http://localhost:3000"]
    """CORS_ORIGINS as a JSON list: browser origins allowed to call the API (the web app)."""

    # --- Worker (T-213) ---
    worker_id: str = Field(default_factory=lambda: f"{socket.gethostname()}-{os.getpid()}")
    worker_concurrency: int = Field(default=4, ge=1)
    """Agent runs executed at the same time by one worker process."""
    worker_poll_seconds: float = Field(default=1.0, gt=0)
    worker_maintenance_seconds: float = Field(default=15.0, gt=0)
    """How often the worker reaps expired leases, expires approvals and fires schedules."""
    worker_company_ids: list[uuid.UUID] = []
    """WORKER_COMPANY_IDS as a JSON list: only run these companies' agents (empty: all)."""
    task_lease_seconds: float = Field(default=300.0, gt=0)
    task_retry_base_seconds: float = Field(default=10.0, gt=0)

    @field_validator(
        "anthropic_api_key",
        "nvidia_api_key",
        "gemini_api_key",
        "openai_api_key",
        "tavily_api_key",
        "fred_api_key",
        "finnhub_api_key",
        "resend_api_key",
        "payuni_mer_id",
        "payuni_hash_key",
        "payuni_hash_iv",
        mode="before",
    )
    @classmethod
    def _blank_key_is_no_key(cls, value: object) -> object:
        """``KEY=`` in .env (the example's blank line) means "not set", not an empty key."""
        if isinstance(value, str) and not value.strip():
            return None
        if isinstance(value, SecretStr) and not value.get_secret_value().strip():
            return None
        return value

    @model_validator(mode="after")
    def _check_consistency(self) -> Settings:
        problems: list[str] = []
        if not self.database_url.startswith("postgresql+asyncpg://"):
            problems.append("DATABASE_URL must use the postgresql+asyncpg:// scheme")
        if self.model_provider != "fake":
            key = {
                "anthropic": self.anthropic_api_key,
                "nvidia": self.nvidia_api_key,
                "gemini": self.gemini_api_key,
                "openai": self.openai_api_key,
            }
            if key[self.model_provider] is None:
                problems.append(
                    f"{self.model_provider.upper()}_API_KEY is required when "
                    f"MODEL_PROVIDER={self.model_provider}"
                )
            if not self.frontier_model_id:
                problems.append(
                    f"FRONTIER_MODEL_ID is required when MODEL_PROVIDER={self.model_provider}"
                )
            for model_id in {self.frontier_model_id, self.fast_model_id} - {None, ""}:
                if model_id not in self.model_prices:
                    problems.append(f"MODEL_PRICES has no entry for {model_id}")
        if self.email_provider == "resend" and self.resend_api_key is None:
            problems.append("RESEND_API_KEY is required when EMAIL_PROVIDER=resend")
        if self.embed_provider != "fake":
            if not self.embed_model_id:
                problems.append(
                    f"EMBED_MODEL_ID is required when EMBED_PROVIDER={self.embed_provider}"
                )
            if self.embed_provider == "nvidia" and self.nvidia_api_key is None:
                problems.append("NVIDIA_API_KEY is required when EMBED_PROVIDER=nvidia")
        if self.tools_profile == "live" and self.tavily_api_key is None:
            problems.append("TAVILY_API_KEY is required when TOOLS_PROFILE=live")
        if self.autora_env == "prod" and self.api_bearer_token.get_secret_value() in (
            "",
            "change-me",
        ):
            problems.append("API_BEARER_TOKEN must be set to a real secret when AUTORA_ENV=prod")
        if problems:
            raise ValueError("; ".join(problems))
        return self


def _format_validation_error(exc: ValidationError) -> str:
    lines = ["Invalid configuration:"]
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "(model)"
        env_name = loc.upper() if loc != "(model)" else loc
        msg = err["msg"]
        if err["type"] == "missing":
            msg = f"environment variable {env_name} is required"
        lines.append(f"  - {env_name}: {msg}")
    lines.append("See .env.example for the full list.")
    return "\n".join(lines)


def load_settings(**overrides: object) -> Settings:
    """Build Settings from env / .env (+ explicit overrides). Raises SettingsError."""
    try:
        return Settings(**overrides)  # type: ignore[arg-type]
    except ValidationError as exc:
        raise SettingsError(_format_validation_error(exc)) from exc


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide cached settings. Tests call ``get_settings.cache_clear()``."""
    return load_settings()
