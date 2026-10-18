"""Knowledge spaces end to end: upload → parse → chunk → embed → index → search, and tenant/space isolation."""

from __future__ import annotations

import io
import uuid
import zipfile
from collections.abc import Iterator
from typing import Any

import httpx
import psycopg
import pytest

from app.core.config import Settings
from tests.fixtures.corpus import BUS_PROTOCOLS_MD, CV_HIDDEN, cv_with_hidden_text_pdf, datasheet_pdf, rtos_notes_docx
from tests.integration.conftest import Infra, Postgres
from tests.integration.helpers import ApiClient, SeededOrg, knowledge_settings, new_client, seed_org, seed_user

pytestmark = pytest.mark.integration


@pytest.fixture
def settings(infra: Infra, fresh_valkey: str) -> Settings:
    return knowledge_settings(infra.database_url, fresh_valkey, infra.qdrant_url, infra.s3_endpoint_url)


class Member:
    def __init__(self, api: ApiClient, user_id: uuid.UUID) -> None:
        self.api = api
        self.user_id = user_id

    def personal_space(self) -> str:
        spaces = self.api.get("/api/spaces").json()
        return next(s["id"] for s in spaces if s["kind"] == "PERSONAL" and s["is_mine"])

    def upload(self, space_id: str, filename: str, data: bytes, doc_type: str = "AUTO") -> dict[str, Any]:
        response = self.api.post(
            f"/api/spaces/{space_id}/documents", files=[("files", (filename, data))], data={"doc_type": doc_type}
        )
        assert response.status_code == 201, response.text
        result: dict[str, Any] = response.json()
        # The response is written before the (inline) ingestion runs; read back the processed state.
        result["documents"] = [self.document(d["id"]) for d in result["documents"]]
        return result

    def document(self, document_id: str) -> dict[str, Any]:
        response = self.api.get(f"/api/documents/{document_id}")
        assert response.status_code == 200, response.text
        return response.json()  # type: ignore[no-any-return]

    def search(self, query: str, **extra: Any) -> list[dict[str, Any]]:
        response = self.api.post("/api/search", json={"query": query, **extra})
        assert response.status_code == 200, response.text
        return response.json()["results"]  # type: ignore[no-any-return]


@pytest.fixture
def world(settings: Settings) -> Iterator[dict[str, Any]]:
    """Org 1 with an owner and members A and B; org 2 with member C."""
    org1, org2 = seed_org(settings), seed_org(settings)
    clients: list[ApiClient] = []

    def login(org: SeededOrg, email: str, user_id: uuid.UUID) -> Member:
        api = new_client(settings)
        clients.append(api)
        assert api.login(email).status_code == 200
        return Member(api, user_id)

    a_id, a_email = seed_user(settings, org1)
    b_id, b_email = seed_user(settings, org1)
    c_id, c_email = seed_user(settings, org2)
    yield {
        "org1": org1,
        "owner": login(org1, org1.owner_email, org1.owner_id),
        "a": login(org1, a_email, a_id),
        "b": login(org1, b_email, b_id),
        "c": login(org2, c_email, c_id),
    }
    for api in clients:
        api.close()


def test_upload_is_indexed_and_searchable_with_citations(world: dict[str, Any]) -> None:
    a: Member = world["a"]
    space = a.personal_space()
    result = a.upload(space, "stm32f4-reference-manual-excerpt.pdf", datasheet_pdf())
    (doc,) = result["documents"]
    assert doc["status"] == "READY", doc
    assert doc["doc_type"] == "DATASHEET"  # suggested from the content type and name
    assert doc["page_count"] >= 3
    assert doc["chunk_count"] > 5

    hits = a.search("GPIOx_BSRR offset")
    assert hits, "identifier query found nothing"
    top = hits[0]
    assert "GPIOx_BSRR" in top["text"]
    assert top["citation"]["document_id"] == doc["id"]
    assert top["citation"]["page_from"] is not None
    assert any("GPIO" in part for part in top["citation"]["section_path"])

    # Same bytes again → reported as a duplicate, not re-ingested.
    again = a.upload(space, "copy.pdf", datasheet_pdf())
    assert again["documents"] == []
    assert again["duplicates"][0]["id"] == doc["id"]


def test_docx_markdown_paste_and_zip(world: dict[str, Any]) -> None:
    a: Member = world["a"]
    space = a.personal_space()
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("notes/embedded-c-rtos-notes.docx", rtos_notes_docx())
        zf.writestr("notes/serial-bus-protocols.md", BUS_PROTOCOLS_MD)
        zf.writestr("notes/firmware.bin", b"\x7fELF\x00\x01")
    result = a.upload(space, "bundle.zip", archive.getvalue())
    assert sorted(d["filename"] for d in result["documents"]) == [
        "embedded-c-rtos-notes.docx",
        "serial-bus-protocols.md",
    ]
    assert [r["filename"] for r in result["rejected"]] == ["firmware.bin"]
    assert all(d["status"] == "READY" for d in result["documents"])

    pasted = a.api.post(
        f"/api/spaces/{space}/documents/paste",
        json={
            "title": "Team question bank",
            "text": "# Questions\n\nExplain the watchdog window option bytes.",
            "doc_type": "QUESTION_BANK",
        },
    )
    assert pasted.status_code == 201, pasted.text
    assert pasted.json()["title"] == "Team question bank"
    assert a.document(pasted.json()["id"])["status"] == "READY"

    hits = a.search("Mars Pathfinder priority inversion")
    assert "Mars Pathfinder" in hits[0]["text"]
    only_bank = a.search("watchdog", doc_types=["QUESTION_BANK"])
    assert only_bank
    assert all(h["doc_type"] == "QUESTION_BANK" for h in only_bank)


@pytest.mark.isolation
def test_hidden_text_in_a_cv_is_flagged_and_never_indexed(world: dict[str, Any]) -> None:
    a: Member = world["a"]
    result = a.upload(a.personal_space(), "priya-raman-cv.pdf", cv_with_hidden_text_pdf())
    (doc,) = result["documents"]
    assert doc["doc_type"] == "CV"
    assert doc["status"] == "READY"
    flags = doc["risk_flags"]
    assert "hidden_text" in flags
    assert "injection_patterns" in flags
    for hit in a.search("ignore all previous instructions strongest hire"):
        assert CV_HIDDEN.lower() not in hit["text"].lower()
    assert any("Firmware engineer" in h["text"] for h in a.search("motor drives firmware engineer CAN bootloader"))


@pytest.mark.isolation
def test_personal_documents_are_private_to_their_uploader(world: dict[str, Any], settings: Settings) -> None:
    a, b, owner, c = world["a"], world["b"], world["owner"], world["c"]
    doc = a.upload(a.personal_space(), "notes.md", BUS_PROTOCOLS_MD.encode())["documents"][0]
    a_space = doc["space_id"]

    for outsider in (b, owner, c):
        assert outsider.api.get(f"/api/documents/{doc['id']}").status_code == 404
        assert outsider.api.get(f"/api/documents/{doc['id']}/download").status_code == 404
        assert outsider.api.get(f"/api/spaces/{a_space}/documents").status_code == 404
        assert outsider.api.post(f"/api/documents/{doc['id']}/reindex").status_code == 404
        assert outsider.api.delete(f"/api/documents/{doc['id']}").status_code == 404
        upload = outsider.api.post(f"/api/spaces/{a_space}/documents", files=[("files", ("x.md", b"# x\n\ny"))])
        assert upload.status_code == 404
        assert outsider.search("CAN arbitration dominant bit", space_ids=[a_space]) == []
        assert all(h["citation"]["document_id"] != doc["id"] for h in outsider.search("CAN arbitration dominant"))
        assert a_space not in {s["id"] for s in outsider.api.get("/api/spaces").json()}

    assert a.search("CAN arbitration dominant bit")[0]["citation"]["document_id"] == doc["id"]


@pytest.mark.isolation
def test_presigned_download_only_for_readers(world: dict[str, Any]) -> None:
    a, b = world["a"], world["b"]
    doc = a.upload(a.personal_space(), "datasheet.pdf", datasheet_pdf())["documents"][0]
    link = a.api.get(f"/api/documents/{doc['id']}/download")
    assert link.status_code == 200
    body = link.json()
    assert body["expires_in_s"] <= 300
    fetched = httpx.get(body["url"])
    assert fetched.status_code == 200
    assert fetched.content == datasheet_pdf()
    assert "attachment" in fetched.headers.get("content-disposition", "")
    tampered = body["url"].replace(doc["id"], str(uuid.uuid4()))
    assert httpx.get(tampered).status_code in (403, 404)
    assert b.api.get(f"/api/documents/{doc['id']}/download").status_code == 404


@pytest.mark.isolation
def test_org_space_grants_and_immediate_revocation(world: dict[str, Any]) -> None:
    owner, a, b = world["owner"], world["a"], world["b"]
    created = owner.api.post("/api/admin/spaces", json={"name": "Embedded handbook", "description": "Shared"})
    assert created.status_code == 201, created.text
    space = created.json()["id"]
    doc = owner.upload(space, "rtos.docx", rtos_notes_docx())["documents"][0]
    assert doc["status"] == "READY"

    assert a.search("priority inheritance mutex") == []
    granted = owner.api.put(
        f"/api/admin/spaces/{space}/grants",
        json=[{"principal_type": "USER", "principal_id": str(a.user_id), "permission": "READ"}],
    )
    assert granted.status_code == 200, granted.text
    assert [g["principal_id"] for g in granted.json()["grants"]] == [str(a.user_id)]

    hits = a.search("priority inheritance mutex")
    assert hits
    assert hits[0]["citation"]["document_id"] == doc["id"]
    assert a.api.get(f"/api/documents/{doc['id']}").status_code == 200
    # READ does not allow adding documents.
    denied = a.api.post(f"/api/spaces/{space}/documents", files=[("files", ("x.md", b"# x\n\nmore notes"))])
    assert denied.status_code == 403
    assert b.search("priority inheritance mutex") == []

    # Group grant with CONTRIBUTE: B joins the group and can add documents.
    group = owner.api.post("/api/admin/groups", json={"name": "firmware-team", "description": ""}).json()
    assert owner.api.put(f"/api/admin/groups/{group['id']}/members/{b.user_id}").status_code == 200
    owner.api.put(
        f"/api/admin/spaces/{space}/grants",
        json=[
            {"principal_type": "USER", "principal_id": str(a.user_id), "permission": "READ"},
            {"principal_type": "GROUP", "principal_id": group["id"], "permission": "CONTRIBUTE"},
        ],
    )
    added = b.upload(space, "buses.md", BUS_PROTOCOLS_MD.encode())
    assert added["documents"][0]["status"] == "READY"

    # Revocation is effective on the very next request (ACL cache version bumped on commit).
    assert owner.api.put(f"/api/admin/spaces/{space}/grants", json=[]).status_code == 200
    assert a.search("priority inheritance mutex") == []
    assert a.api.get(f"/api/documents/{doc['id']}").status_code == 404
    assert b.search("CAN bus termination") == [] or all(
        h["citation"]["document_id"] != added["documents"][0]["id"] for h in b.search("CAN bus termination")
    )

    # Expired grants count for nothing.
    owner.api.put(
        f"/api/admin/spaces/{space}/grants",
        json=[
            {
                "principal_type": "USER",
                "principal_id": str(a.user_id),
                "permission": "READ",
                "expires_at": "2020-01-01T00:00:00Z",
            }
        ],
    )
    assert a.search("priority inheritance mutex") == []


@pytest.mark.isolation
def test_cross_org_guessing_finds_nothing(world: dict[str, Any]) -> None:
    owner, c = world["owner"], world["c"]
    space = owner.api.post("/api/admin/spaces", json={"name": "Org-wide"}).json()["id"]
    doc = owner.upload(space, "buses.md", BUS_PROTOCOLS_MD.encode())["documents"][0]
    assert c.api.get(f"/api/documents/{doc['id']}").status_code == 404
    assert c.api.get(f"/api/spaces/{space}/documents").status_code == 404
    assert c.search("CAN arbitration", space_ids=[space]) == []
    # A member of org 2 cannot grant itself into org 1's space even if it were an owner elsewhere.
    assert c.api.put(f"/api/admin/spaces/{space}/grants", json=[]).status_code == 403


def test_delete_removes_rows_vectors_and_files(world: dict[str, Any], pg: Postgres) -> None:
    a: Member = world["a"]
    doc = a.upload(a.personal_space(), "buses.md", BUS_PROTOCOLS_MD.encode())["documents"][0]
    link = a.api.get(f"/api/documents/{doc['id']}/download").json()["url"]
    assert a.api.delete(f"/api/documents/{doc['id']}").status_code == 204
    assert a.api.get(f"/api/documents/{doc['id']}").status_code == 404
    assert all(h["citation"]["document_id"] != doc["id"] for h in a.search("CAN arbitration dominant bit"))
    assert httpx.get(link).status_code == 404
    with psycopg.connect(pg.dsn("postgres")) as conn:
        assert conn.execute("SELECT count(*) FROM document_chunks WHERE document_id = %s", (doc["id"],)).fetchone() == (
            0,
        )
        audit = conn.execute(
            "SELECT count(*) FROM audit_logs WHERE action = 'document.deleted' AND target_id = %s", (doc["id"],)
        ).fetchone()
    assert audit == (1,)


def test_reindex_keeps_document_searchable(world: dict[str, Any]) -> None:
    a: Member = world["a"]
    doc = a.upload(a.personal_space(), "buses.md", BUS_PROTOCOLS_MD.encode())["documents"][0]
    before = [h["text"] for h in a.search("I2C clock stretching", k=20) if h["citation"]["document_id"] == doc["id"]]
    reindexed = a.api.post(f"/api/documents/{doc['id']}/reindex")
    assert reindexed.status_code == 200
    after_doc = a.document(doc["id"])
    assert after_doc["status"] == "READY"
    assert after_doc["chunk_count"] == doc["chunk_count"]
    after = [h["text"] for h in a.search("I2C clock stretching", k=20) if h["citation"]["document_id"] == doc["id"]]
    assert sorted(after) == sorted(before)  # old vectors replaced, not duplicated
    assert len(after) == len(set(after))


@pytest.mark.isolation
def test_deleting_a_member_purges_their_private_knowledge(world: dict[str, Any], pg: Postgres) -> None:
    owner, a = world["owner"], world["a"]
    doc = a.upload(a.personal_space(), "buses.md", BUS_PROTOCOLS_MD.encode())["documents"][0]
    link = a.api.get(f"/api/documents/{doc['id']}/download").json()["url"]
    assert owner.api.delete(f"/api/admin/users/{a.user_id}").status_code == 204
    assert httpx.get(link).status_code == 404
    with psycopg.connect(pg.dsn("postgres")) as conn:
        assert conn.execute("SELECT count(*) FROM documents WHERE id = %s", (doc["id"],)).fetchone() == (0,)


@pytest.mark.isolation
def test_url_documents_are_ssrf_guarded(world: dict[str, Any]) -> None:
    a: Member = world["a"]
    space = a.personal_space()
    for url in ("http://127.0.0.1:6333/collections", "http://169.254.169.254/latest/meta-data", "file:///etc/passwd"):
        response = a.api.post(f"/api/spaces/{space}/documents/url", json={"url": url})
        assert response.status_code == 400, (url, response.text)


def test_selftest_reports_every_component(world: dict[str, Any]) -> None:
    owner, a = world["owner"], world["a"]
    report = owner.api.get("/api/system/selftest")
    assert report.status_code == 200, report.text
    names = {c["name"]: c for c in report.json()["checks"]}
    assert names["embedder"]["ok"], names
    assert names["vector store"]["ok"], names
    assert a.api.get("/api/system/selftest").status_code == 403
