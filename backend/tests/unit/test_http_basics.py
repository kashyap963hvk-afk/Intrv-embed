"""Error format, request ids, CORS, metrics and readiness behavior of the HTTP layer."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.core.errors import NotFoundError
from app.core.readiness import DependencyCheck
from app.main import create_app
from tests.conftest import make_settings, ok_checks

PROBLEM = "application/problem+json"


class Payload(BaseModel):
    api_key: str
    count: int


def _add_test_routes(app: FastAPI) -> None:
    @app.get("/test/not-found")
    async def not_found() -> None:
        raise NotFoundError("document 42 does not exist")

    @app.get("/test/boom")
    async def boom() -> None:
        raise RuntimeError("postgres://admin:hunter2@db/internal leaked?")

    @app.post("/test/validate")
    async def validate(payload: Payload) -> Payload:
        return payload


@pytest.fixture
def test_client() -> Iterator[TestClient]:
    app = create_app(make_settings())
    _add_test_routes(app)
    with TestClient(app, raise_server_exceptions=False) as client:
        app.state.readiness_checks = ok_checks()
        yield client


def test_healthz(test_client: TestClient) -> None:
    response = test_client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_app_error_is_problem_json(test_client: TestClient) -> None:
    response = test_client.get("/test/not-found")
    assert response.status_code == 404
    assert response.headers["content-type"] == PROBLEM
    body = response.json()
    assert body["type"] == "urn:intrv:problem:not-found"
    assert body["detail"] == "document 42 does not exist"
    assert body["instance"] == "/test/not-found"
    assert body["request_id"] == response.headers["x-request-id"]


def test_unknown_route_is_problem_json(test_client: TestClient) -> None:
    response = test_client.get("/nope")
    assert response.status_code == 404
    assert response.headers["content-type"] == PROBLEM
    assert response.json()["type"] == "urn:intrv:problem:not-found"


def test_unhandled_error_hides_details_and_keeps_request_id(test_client: TestClient) -> None:
    response = test_client.get("/test/boom", headers={"X-Request-ID": "trace-abc-123"})
    assert response.status_code == 500
    assert response.headers["content-type"] == PROBLEM
    assert response.headers["x-request-id"] == "trace-abc-123"
    body = response.json()
    assert body["title"] == "Internal server error"
    assert body["request_id"] == "trace-abc-123"
    assert "hunter2" not in response.text


def test_validation_error_does_not_echo_input(test_client: TestClient) -> None:
    response = test_client.post("/test/validate", json={"api_key": "sk-supersecret", "count": "many"})
    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM
    body = response.json()
    assert body["errors"][0]["loc"] == ["body", "count"]
    assert "sk-supersecret" not in response.text


def test_request_id_generated_when_missing_or_invalid(test_client: TestClient) -> None:
    generated = test_client.get("/healthz").headers["x-request-id"]
    assert len(generated) == 32
    replaced = test_client.get("/healthz", headers={"X-Request-ID": "bad id\nwith newline"})
    assert replaced.headers["x-request-id"] != "bad id\nwith newline"
    kept = test_client.get("/healthz", headers={"X-Request-ID": "client-req-0001"})
    assert kept.headers["x-request-id"] == "client-req-0001"


def test_cors_allows_configured_origin_with_credentials(test_client: TestClient) -> None:
    response = test_client.options(
        "/healthz",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"},
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert response.headers["access-control-allow-credentials"] == "true"


def test_cors_rejects_unknown_origin(test_client: TestClient) -> None:
    response = test_client.get("/healthz", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in response.headers


def test_metrics_use_route_templates(test_client: TestClient) -> None:
    test_client.get("/healthz")
    test_client.get("/does-not-exist-1234")
    text = test_client.get("/metrics").text
    assert 'intrv_http_requests_total{method="GET",route="/healthz",status="200"}' in text
    assert 'route="<unmatched>"' in text
    assert "does-not-exist-1234" not in text


def test_metrics_can_be_disabled() -> None:
    with TestClient(create_app(make_settings(metrics_enabled=False)), raise_server_exceptions=False) as client:
        assert client.get("/metrics").status_code == 404


def test_docs_disabled_in_production() -> None:
    prod = make_settings(
        app_env="prod",
        public_app_url="https://app.example.com",
        api_public_url="https://api.example.com",
        cors_allowed_origins="https://app.example.com",
    )
    with TestClient(create_app(prod), raise_server_exceptions=False) as client:
        assert client.get("/openapi.json").status_code == 404
        assert client.get("/docs").status_code == 404


# --- readiness -------------------------------------------------------------------------------


def test_readyz_all_ok(test_client: TestClient) -> None:
    response = test_client.get("/readyz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert set(body["checks"]) == {"postgres", "valkey", "qdrant", "s3"}


async def _fail() -> None:
    raise ConnectionRefusedError("connect to qdrant-internal.svc:6333 refused")


async def _hang() -> None:
    await asyncio.sleep(10)


def _client_with_checks(checks: list[DependencyCheck], **overrides: object) -> TestClient:
    values: dict[str, object] = {"readiness_timeout_s": 0.2, **overrides}
    app = create_app(make_settings(**values))
    client = TestClient(app, raise_server_exceptions=False)
    client.__enter__()
    app.state.readiness_checks = checks
    return client


def test_readyz_reports_failure_and_timeout() -> None:
    client = _client_with_checks([DependencyCheck("qdrant", _fail), DependencyCheck("valkey", _hang)])
    try:
        response = client.get("/readyz")
    finally:
        client.__exit__(None, None, None)
    assert response.status_code == 503
    checks = response.json()["checks"]
    assert checks["qdrant"]["status"] == "error"
    assert "ConnectionRefusedError" in checks["qdrant"]["error"]
    assert checks["valkey"]["error"] == "timed out after 0.2s"


def test_readyz_hides_error_details_in_production() -> None:
    client = _client_with_checks(
        [DependencyCheck("qdrant", _fail)],
        app_env="prod",
        public_app_url="https://app.example.com",
        api_public_url="https://api.example.com",
        cors_allowed_origins="https://app.example.com",
    )
    try:
        response = client.get("/readyz")
    finally:
        client.__exit__(None, None, None)
    assert response.status_code == 503
    assert response.json()["checks"]["qdrant"]["error"] == "ConnectionRefusedError"
    assert "qdrant-internal" not in response.text
