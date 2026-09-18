import pytest

from autora.infra.settings import SettingsError, load_settings

VALID_URL = "postgresql+asyncpg://u:p@localhost:5434/db"


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch):
    # Ignore the developer's real env / .env so assertions are deterministic.
    for key in (
        "DATABASE_URL",
        "MODEL_PROVIDER",
        "ANTHROPIC_API_KEY",
        "FRONTIER_MODEL_ID",
        "TOOLS_PROFILE",
        "TAVILY_API_KEY",
        "API_BEARER_TOKEN",
        "AUTORA_ENV",
    ):
        monkeypatch.delenv(key, raising=False)


def _load(**kw):
    return load_settings(_env_file=None, **kw)


def test_missing_database_url_fails_with_clear_message():
    with pytest.raises(SettingsError) as exc:
        _load()
    msg = str(exc.value)
    assert "DATABASE_URL" in msg
    assert "required" in msg
    assert ".env.example" in msg


def test_wrong_database_scheme_is_rejected():
    with pytest.raises(SettingsError) as exc:
        _load(database_url="postgresql://u:p@localhost/db")
    assert "postgresql+asyncpg" in str(exc.value)


def test_defaults_are_safe_for_dev():
    s = _load(database_url=VALID_URL)
    assert s.model_provider == "fake"
    assert s.tools_profile == "fixture"
    assert s.autora_env == "dev"


def test_anthropic_provider_requires_key_and_model_id():
    with pytest.raises(SettingsError) as exc:
        _load(database_url=VALID_URL, model_provider="anthropic")
    msg = str(exc.value)
    assert "ANTHROPIC_API_KEY" in msg
    assert "FRONTIER_MODEL_ID" in msg


def test_live_tools_require_tavily_key():
    with pytest.raises(SettingsError) as exc:
        _load(database_url=VALID_URL, tools_profile="live")
    assert "TAVILY_API_KEY" in str(exc.value)


def test_secrets_are_not_printed(monkeypatch):
    s = _load(database_url=VALID_URL, anthropic_api_key="sk-secret")
    assert "sk-secret" not in repr(s)
    assert s.anthropic_api_key is not None
    assert s.anthropic_api_key.get_secret_value() == "sk-secret"


def test_env_variables_are_read(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", VALID_URL)
    monkeypatch.setenv("MODEL_PROVIDER", "fake")
    s = _load()
    assert s.database_url == VALID_URL


@pytest.mark.parametrize("token", [None, "change-me", ""])
def test_prod_requires_real_bearer_token(token):
    overrides = {"database_url": VALID_URL, "autora_env": "prod"}
    if token is not None:
        overrides["api_bearer_token"] = token
    with pytest.raises(SettingsError, match="API_BEARER_TOKEN"):
        _load(**overrides)
    assert (
        _load(database_url=VALID_URL, autora_env="prod", api_bearer_token="s3cret").autora_env
        == "prod"
    )
