"""Live smoke (PROMPT.md Phase 5): a real model + the embedded pack + a firmware CV → at least 70% of the
technical questions are anchored in specific CV claims. Skipped unless LIVE_OPENAI_COMPAT_* are set (see
tests/live/test_live_llm.py)."""

from __future__ import annotations

import os
import re
from typing import Any

import pytest

from app.core.config import Settings
from tests.integration.conftest import Infra
from tests.integration.helpers import knowledge_settings, new_client, seed_org
from tests.integration.test_questions import CV_MD, embedded_pack, paste

pytestmark = [pytest.mark.integration, pytest.mark.live]

BASE_URL = os.environ.get("LIVE_OPENAI_COMPAT_BASE_URL")
API_KEY = os.environ.get("LIVE_OPENAI_COMPAT_API_KEY")
MODEL = os.environ.get("LIVE_OPENAI_COMPAT_MODEL")
CV_TERMS = {"adc", "isr", "latency", "dma", "stm32f4", "40%", "can", "bootloader", "a/b", "freertos", "motor"}


def anchored(item: dict[str, Any]) -> bool:
    words = set(re.findall(r"[a-z0-9/%]+", item["text"].lower()))
    return bool(item.get("evidence_refs")) and len(words & CV_TERMS) >= 1


@pytest.mark.skipif(not (BASE_URL and MODEL), reason="LIVE_OPENAI_COMPAT_* not set")
def test_real_model_grounds_questions_in_the_cv(infra: Infra, fresh_valkey: str) -> None:
    settings: Settings = knowledge_settings(infra.database_url, fresh_valkey, infra.qdrant_url, infra.s3_endpoint_url)
    org = seed_org(settings)
    api = new_client(settings)
    try:
        assert api.login(org.owner_email).status_code == 200
        created = api.post(
            "/api/llm/profiles",
            json={"name": "live", "base_url": BASE_URL, "api_key": API_KEY, "model": MODEL, "trains_on_data": False},
        )
        assert created.status_code == 201, created.text
        cv_id = paste(api, "CV", CV_MD, "CV")
        qset = api.post(
            "/api/question-sets",
            json={"cv_document_id": cv_id, "role_pack_id": embedded_pack(api)["id"], "count": 12, "seniority": "MID"},
        ).json()
        detail = api.get(f"/api/question-sets/{qset['id']}").json()
        assert detail["status"] == "READY", detail["error"]
        technical = [i["data"] for i in detail["items"] if i["data"]["category"] in {"technical", "cv_deep_dive"}]
        ratio = sum(anchored(i) for i in technical) / max(1, len(technical))
        assert ratio >= 0.7, [i["text"] for i in technical]
    finally:
        api.close()
