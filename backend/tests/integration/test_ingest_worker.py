"""Ingestion through a real Celery worker (the deployed path), not the inline dispatcher."""

from __future__ import annotations

import time

import pytest
from celery.contrib.testing.worker import start_worker

from app.core.config import get_settings
from tests.fixtures.corpus import BUS_PROTOCOLS_MD
from tests.integration.conftest import Infra
from tests.integration.helpers import knowledge_settings, new_client, seed_org, seed_user, settings_env
from workers import runtime
from workers.config import QUEUES, create_celery

pytestmark = pytest.mark.integration


def test_upload_is_processed_by_the_worker(infra: Infra, fresh_valkey: str, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = knowledge_settings(
        infra.database_url, fresh_valkey, infra.qdrant_url, infra.s3_endpoint_url, ingest_inline=False
    )
    # The worker builds its clients from the environment, exactly as in a deployment.
    for key, value in settings_env(settings).items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()

    org = seed_org(settings)
    _, email = seed_user(settings, org)
    api = new_client(settings)
    try:
        assert api.login(email).status_code == 200
        space = next(s["id"] for s in api.get("/api/spaces").json() if s["kind"] == "PERSONAL")
        celery = create_celery(settings)
        celery.loader.import_default_modules()
        with start_worker(celery, pool="solo", perform_ping_check=False, queues=list(QUEUES)):
            response = api.post(
                f"/api/spaces/{space}/documents", files=[("files", ("buses.md", BUS_PROTOCOLS_MD.encode()))]
            )
            assert response.status_code == 201, response.text
            doc_id = response.json()["documents"][0]["id"]
            deadline = time.monotonic() + 60
            while (status := api.get(f"/api/documents/{doc_id}").json()["status"]) not in {"READY", "FAILED"}:
                assert time.monotonic() < deadline, status
                time.sleep(0.3)
        assert status == "READY"
        hits = api.post("/api/search", json={"query": "CAN bus-off error counter"}).json()["results"]
        assert hits
        assert hits[0]["citation"]["document_id"] == doc_id
    finally:
        api.close()
        get_settings.cache_clear()
        runtime._deps = None
