from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from tests.conftest import make_settings
from tests.integration.conftest import S3_ACCESS_KEY, S3_BUCKET, S3_SECRET_KEY, Infra

pytestmark = pytest.mark.integration


def _settings(infra: Infra, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": infra.database_url,
        "valkey_url": infra.valkey_url,
        "qdrant_url": infra.qdrant_url,
        "s3_endpoint_url": infra.s3_endpoint_url,
        "s3_bucket": S3_BUCKET,
        "s3_access_key": S3_ACCESS_KEY,
        "s3_secret_key": S3_SECRET_KEY,
        "readiness_timeout_s": 5.0,
        **overrides,
    }
    return make_settings(**values)


def test_readyz_ok_against_real_dependencies(infra: Infra) -> None:
    with TestClient(create_app(_settings(infra))) as client:
        response = client.get("/readyz")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok"
    assert {name: check["status"] for name, check in body["checks"].items()} == {
        "postgres": "ok",
        "valkey": "ok",
        "qdrant": "ok",
        "s3": "ok",
    }


def test_readyz_degraded_when_bucket_missing(infra: Infra) -> None:
    with TestClient(create_app(_settings(infra, s3_bucket="does-not-exist"))) as client:
        response = client.get("/readyz")
    assert response.status_code == 503
    checks = response.json()["checks"]
    assert checks["s3"]["status"] == "error"
    assert checks["postgres"]["status"] == "ok"


def test_readyz_degraded_with_wrong_s3_secret(infra: Infra) -> None:
    with TestClient(create_app(_settings(infra, s3_secret_key="wrong-secret"))) as client:
        response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["checks"]["s3"]["status"] == "error"
