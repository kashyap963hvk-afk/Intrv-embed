"""Retrieval quality gate over the embedded-systems golden set (eval/rag/golden.jsonl).

Runs with the configured embedder; in CI that is the lexical test embedder, so these numbers are a floor:
the real models (bge-small / bge-m3 + reranker) score higher. Leakage must be exactly zero regardless.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from app.core.config import Settings
from tests.fixtures.corpus import BUS_PROTOCOLS_MD, corpus, golden_queries
from tests.integration.conftest import Infra
from tests.integration.helpers import ApiClient, knowledge_settings, new_client, seed_org, seed_user

pytestmark = pytest.mark.integration

K = 6
MIN_RECALL = 0.8


@pytest.fixture(scope="module")
def indexed(infra: Infra, valkey: str) -> Iterator[dict[str, Any]]:
    settings: Settings = knowledge_settings(infra.database_url, valkey, infra.qdrant_url, infra.s3_endpoint_url)
    org = seed_org(settings)
    _, reader_email = seed_user(settings, org)
    _, other_email = seed_user(settings, org)
    reader, other = new_client(settings), new_client(settings)
    assert reader.login(reader_email).status_code == 200
    assert other.login(other_email).status_code == 200

    def personal(api: ApiClient) -> str:
        return next(s["id"] for s in api.get("/api/spaces").json() if s["kind"] == "PERSONAL")

    space = personal(reader)
    doc_ids: dict[str, str] = {}
    for item in corpus():
        response = reader.post(
            f"/api/spaces/{space}/documents",
            files=[("files", (item.filename, item.data))],
            data={"doc_type": item.doc_type},
        )
        assert response.status_code == 201, response.text
        doc_ids[item.key] = response.json()["documents"][0]["id"]
    for doc_id in doc_ids.values():
        assert reader.get(f"/api/documents/{doc_id}").json()["status"] == "READY"

    # A colleague's private copy of similar material: must never appear in the reader's results.
    decoy = other.post(
        f"/api/spaces/{personal(other)}/documents",
        files=[("files", ("my-bus-notes.md", (BUS_PROTOCOLS_MD + "\nPrivate notes of a colleague.").encode()))],
    )
    assert decoy.status_code == 201
    yield {"reader": reader, "doc_ids": doc_ids, "decoy_id": decoy.json()["documents"][0]["id"]}
    reader.close()
    other.close()


def _search(api: ApiClient, query: str) -> list[dict[str, Any]]:
    response = api.post("/api/search", json={"query": query, "k": K})
    assert response.status_code == 200, response.text
    return response.json()["results"]  # type: ignore[no-any-return]


@pytest.mark.isolation
def test_golden_set_recall_and_zero_leakage(indexed: dict[str, Any]) -> None:
    reader, doc_ids, decoy = indexed["reader"], indexed["doc_ids"], indexed["decoy_id"]
    queries = golden_queries()
    assert len(queries) >= 40
    misses: list[str] = []
    leaked = 0
    for row in queries:
        results = _search(reader, row.query)
        leaked += sum(1 for r in results if r["citation"]["document_id"] not in doc_ids.values())
        assert all(r["citation"]["document_id"] != decoy for r in results)
        found = any(
            r["citation"]["document_id"] == doc_ids[row.doc] and row.expect.lower() in r["text"].lower()
            for r in results
        )
        if not found:
            misses.append(row.query)
    recall = 1 - len(misses) / len(queries)
    print(f"\nrecall@{K} = {recall:.3f} over {len(queries)} queries; misses: {misses}")
    assert leaked == 0
    assert recall >= MIN_RECALL, misses


@pytest.mark.parametrize("register", ["GPIOx_OSPEEDR", "RCC_APB1ENR", "GPIOx_IDR"])
def test_register_name_hits_the_table_chunk(indexed: dict[str, Any], register: str) -> None:
    top = _search(indexed["reader"], register)[0]
    assert top["citation"]["document_id"] == indexed["doc_ids"]["datasheet"]
    assert f"| {register} |" in top["text"], top["text"][:500]
