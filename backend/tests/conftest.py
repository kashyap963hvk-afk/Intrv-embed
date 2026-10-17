from __future__ import annotations

import base64
import os
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.readiness import DependencyCheck
from app.main import create_app


def settings_values(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "app_env": "test",
        "log_json": True,
        "database_url": "postgresql+asyncpg://intrv:intrv@127.0.0.1:1/intrv",
        "valkey_url": "redis://127.0.0.1:1/0",
        "qdrant_url": "http://127.0.0.1:1",
        "s3_endpoint_url": "http://127.0.0.1:1",
        "s3_bucket": "intrv-test",
        "s3_access_key": "test-access",
        "s3_secret_key": "test-secret",
        "master_key_base64": base64.b64encode(os.urandom(32)).decode(),
        "jwt_secret": base64.b64encode(os.urandom(48)).decode(),
        "cors_allowed_origins": "http://localhost:3000",
    }
    values.update(overrides)
    return values


def make_settings(**overrides: Any) -> Settings:
    """Build settings from explicit values only (no environment, no .env file)."""
    return Settings.model_validate(settings_values(**overrides))


async def _ok() -> None:
    return None


def ok_checks() -> list[DependencyCheck]:
    return [DependencyCheck(name, _ok) for name in ("postgres", "valkey", "qdrant", "s3")]


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as test_client:
        app.state.readiness_checks = ok_checks()
        yield test_client
