"""Application settings.

Single source of configuration. Model ids, API keys and URLs live here (from env / .env),
never in code. Every process (api, worker, alembic, tests) calls ``get_settings()`` once;
a missing or inconsistent value fails fast with a readable message.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class SettingsError(RuntimeError):
    """Raised when required configuration is missing or inconsistent."""


def _default_env_file() -> str:
    """Locate the .env file regardless of the current working directory.

    Order: AUTORA_ENV_FILE env var → repo root → ./.env relative to cwd.
    This file sits at <root>/packages/autora/autora/infra/settings.py, so the root is
    parents[4] (also /app inside the Docker images).
    """
    override = os.environ.get("AUTORA_ENV_FILE")
    if override:
        return override
    repo_root = Path(__file__).resolve().parents[4]
    candidate = repo_root / ".env"
    return str(candidate) if candidate.is_file() else ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_default_env_file(),
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

    # --- Model provider (T-207/T-208). Ids come from env, never from code. ---
    model_provider: Literal["fake", "anthropic"] = "fake"
    anthropic_api_key: SecretStr | None = None
    frontier_model_id: str | None = None
    fast_model_id: str | None = None

    # --- Tools (T-500, D-003) ---
    tools_profile: Literal["fixture", "live"] = "fixture"
    tavily_api_key: SecretStr | None = None

    # --- API ---
    api_bearer_token: SecretStr = SecretStr("change-me")

    @model_validator(mode="after")
    def _check_consistency(self) -> Settings:
        problems: list[str] = []
        if not self.database_url.startswith("postgresql+asyncpg://"):
            problems.append("DATABASE_URL must use the postgresql+asyncpg:// scheme")
        if self.model_provider == "anthropic":
            if self.anthropic_api_key is None:
                problems.append("ANTHROPIC_API_KEY is required when MODEL_PROVIDER=anthropic")
            if not self.frontier_model_id:
                problems.append("FRONTIER_MODEL_ID is required when MODEL_PROVIDER=anthropic")
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
