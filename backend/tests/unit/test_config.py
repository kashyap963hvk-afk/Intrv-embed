from __future__ import annotations

import base64

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from tests.conftest import make_settings, settings_values


def test_valid_settings_load() -> None:
    settings = make_settings()
    assert settings.app_env == "test"
    assert len(settings.master_key) == 32
    assert settings.cors_allowed_origins == ["http://localhost:3000"]


def test_csv_lists_are_split_and_trailing_slash_removed() -> None:
    settings = make_settings(
        cors_allowed_origins="https://app.example.com/, http://localhost:3000",
        turn_urls="turn:a:3478,turn:b:3478",
    )
    assert settings.cors_allowed_origins == ["https://app.example.com", "http://localhost:3000"]
    assert settings.turn_urls == ["turn:a:3478", "turn:b:3478"]


@pytest.mark.parametrize("origin", ["*", "app.example.com", "https://app.example.com/path", "ftp://x.com"])
def test_rejects_bad_cors_origins(origin: str) -> None:
    with pytest.raises(ValidationError, match="cors_allowed_origins"):
        make_settings(cors_allowed_origins=origin)


@pytest.mark.parametrize(
    "key",
    ["not-base64!!", base64.b64encode(b"short").decode(), base64.b64encode(b"x" * 33).decode()],
)
def test_master_key_must_be_32_bytes(key: str) -> None:
    with pytest.raises(ValidationError, match="master_key_base64"):
        make_settings(master_key_base64=key)


@pytest.mark.parametrize("secret", ["change-me", "a" * 31])
def test_jwt_secret_strength(secret: str) -> None:
    with pytest.raises(ValidationError, match="jwt_secret"):
        make_settings(jwt_secret=secret)


def test_database_url_requires_asyncpg_driver() -> None:
    with pytest.raises(ValidationError, match="postgresql\\+asyncpg"):
        make_settings(database_url="postgresql://u:p@h/db")


def test_valkey_url_requires_redis_scheme() -> None:
    with pytest.raises(ValidationError, match="redis://"):
        make_settings(valkey_url="http://valkey:6379")


def test_production_requires_https() -> None:
    with pytest.raises(ValidationError, match="production requires https"):
        make_settings(app_env="prod")
    prod = make_settings(
        app_env="prod",
        public_app_url="https://app.example.com",
        api_public_url="https://api.example.com",
        cors_allowed_origins="https://app.example.com",
    )
    assert prod.is_prod


def test_gguf_dirs_require_private_endpoint_mode() -> None:
    with pytest.raises(ValidationError, match="gguf_allowed_dirs"):
        make_settings(gguf_allowed_dirs="/models")
    settings = make_settings(gguf_allowed_dirs="/models:/mnt/usb", allow_private_llm_endpoints=True)
    assert settings.gguf_allowed_dirs == ["/models", "/mnt/usb"]


def test_settings_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in settings_values().items():
        monkeypatch.setenv(name.upper(), str(value))
    monkeypatch.setenv("STUN_URLS", "stun:one:3478,stun:two:3478")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.stun_urls == ["stun:one:3478", "stun:two:3478"]
    assert settings.s3_secret_key is not None
    assert "test-secret" not in repr(settings)


def test_empty_environment_values_mean_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in settings_values().items():
        monkeypatch.setenv(name.upper(), str(value))
    monkeypatch.setenv("SMTP_USER", "")
    monkeypatch.setenv("COOKIE_DOMAIN", "")
    monkeypatch.setenv("SMTP_PORT", "")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.smtp_user is None
    assert settings.cookie_domain is None
    assert settings.smtp_port == 587
