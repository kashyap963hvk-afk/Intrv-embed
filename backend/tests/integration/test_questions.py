"""Phase 5 end to end with a scripted model (FakeLLM): role packs, CV analysis, JD gaps, question generation
with critic and dedupe, exports, editing, the Role Builder, isolation and failure handling."""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest

from app.core.config import Settings
from tests.integration.conftest import Infra, Postgres
from tests.integration.helpers import ApiClient, knowledge_settings, new_client, seed_org, seed_user

pytestmark = pytest.mark.integration

PROFILE = {
    "name": "Priya Raman",
    "contact": {"email": "priya.raman@example.com", "phone": "+44 7700 900123", "links": ["https://github.com/priya"]},
    "total_years": 6,
    "roles": [
        {
            "company": "Motor Drives Ltd",
            "title": "Firmware Engineer",
            "start": "2020",
            "end": "present",
            "achievements": ["Cut ADC ISR latency by 40% on STM32F4 by moving sampling to DMA"],
            "tech": ["C", "FreeRTOS", "STM32F4"],
        }
    ],
    "projects": [
        {
            "name": "CAN bootloader",
            "tech": ["CAN", "C"],
            "claims": ["Wrote a CAN bootloader with A/B images"],
            "metrics": [],
        }
    ],
    "skills": [{"name": "FreeRTOS", "evidence_refs": ["roles[0]"]}],
    "vague_claims": [{"text": "worked on embedded systems", "why_vague": "no mechanism, scope or metric"}],
    "seniority_estimate": "MID",
    "domain_tags": ["embedded", "firmware", "made-up-tag"],
    "blind_summary": "Priya Raman is a firmware engineer with six years on motor controllers.",
}
GAPS = {
    "requirements": [
        {"text": "Low-power design", "competency_id": "emb.low_power", "status": "missing", "evidence_refs": []},
        {"text": "FreeRTOS", "competency_id": "emb.rtos", "status": "met", "evidence_refs": ["skills[0]", "bogus[3]"]},
    ]
}
ISR_QUESTIONS = {
    "questions": [
        {
            "text": "You cut ADC ISR latency by 40% on the STM32F4 by moving to DMA. What was the bottleneck before?",
            "competency": "wrong.on.purpose",
            "category": "technical",
            "difficulty": 4,
            "rationale": "Verifies the 40% latency claim in roles[0].achievements[0].",
            "evidence_refs": ["roles[0].achievements[0]", "bogus[9]"],
            "expected_signals": ["DMA half/full callbacks", "ISR time budget"],
            "red_flags": ["cannot say what the ISR did"],
            "follow_ups": ["How did you measure it?", "What changes with D-cache on an M7?"],
            "ideal_answer": "Per-sample interrupts cost cycles; DMA in circular mode batches samples.",
            "citations": ["not-a-retrieved-document"],
        },
        {
            "text": "Walk me through how your ring buffer stays safe between the ADC DMA callback and the main loop.",
            "competency": "emb.interrupts_concurrency",
            "category": "debugging",
            "difficulty": 4,
            "rationale": "Follows the DMA claim into shared-data safety.",
            "evidence_refs": ["roles[0].achievements[0]"],
            "expected_signals": ["SPSC", "volatile indices", "memory barriers"],
            "red_flags": ["disables interrupts everywhere"],
            "follow_ups": ["What if the consumer is slower?"],
            "ideal_answer": "Single producer, single consumer with separate indices.",
            "citations": [],
        },
    ]
}
GENERIC = {
    "questions": [
        {
            "text": "Describe the hardest firmware bug you fixed and how you found it.",
            "competency": "x",
            "category": "technical",
            "difficulty": 3,
            "rationale": "Generic probe of debugging depth.",
            "evidence_refs": ["projects[0].claims[0]"],
            "expected_signals": ["systematic method"],
            "red_flags": [],
            "follow_ups": ["What tool helped most?"],
            "ideal_answer": "",
            "citations": [],
        }
    ]
}
CRITIC = {"verdicts": [{"index": 2, "action": "drop", "reason": "generic, duplicates question 0"}]}
BUILT_PACK = {
    "slug": "data-engineer",
    "name": "Data Engineering",
    "description": "Pipelines and warehousing",
    "domain_tags": ["data"],
    "competencies": [
        {
            "id": "de.sql",
            "name": "SQL",
            "anchors": ["basic selects", "joins and windows", "query plans", "teaches tuning"],
            "signals": ["window functions"],
            "seed_questions": [{"text": "How would you find duplicate orders?", "difficulty": 2}],
        },
        {
            "id": "beh.ownership",
            "name": "Ownership",
            "anchors": {"1": "a", "2": "b", "3": "c", "4": "d"},
        },
    ],
    "roles": [{"id": "de", "name": "Data Engineer", "weights": {"de.sql": 80, "beh.ownership": 20}}],
}

SCRIPT = [
    {"on": "system", "match": "Extract a structured profile", "reply": json.dumps(PROFILE)},
    {"on": "system", "match": "Compare the job description", "reply": json.dumps(GAPS)},
    {"on": "system", "match": "Review these generated interview questions", "reply": json.dumps(CRITIC)},
    {"on": "system", "match": "competency=emb.interrupts_concurrency", "reply": json.dumps(ISR_QUESTIONS)},
    {"on": "system", "match": "Generate exactly", "reply": json.dumps(GENERIC)},
    {"on": "system", "match": "Create an interview Role Pack", "reply": json.dumps(BUILT_PACK)},
]

CV_MD = """# Priya Raman

priya.raman@example.com · +44 7700 900123

## Experience
Firmware Engineer, Motor Drives Ltd (2020 - present). Cut ADC ISR latency by 40% on STM32F4 by moving
sampling to DMA. FreeRTOS, CAN.

## Projects
CAN bootloader with A/B images.
"""
JD_MD = "# Senior Firmware Engineer\n\nWe need FreeRTOS, low-power design and CAN experience."


@pytest.fixture
def settings(infra: Infra, fresh_valkey: str) -> Settings:
    return knowledge_settings(infra.database_url, fresh_valkey, infra.qdrant_url, infra.s3_endpoint_url)


@pytest.fixture
def team(settings: Settings) -> Iterator[dict[str, Any]]:
    org = seed_org(settings)
    _, member_email = seed_user(settings, org)
    owner, member = new_client(settings), new_client(settings)
    assert owner.login(org.owner_email).status_code == 200
    assert member.login(member_email).status_code == 200
    profile = owner.post(
        "/api/llm/profiles",
        json={
            "name": "Scripted",
            "kind": "FAKE",
            "model": "fake",
            "script": SCRIPT,
            "trains_on_data": False,
            "shared": True,
        },
    )
    assert profile.status_code == 201, profile.text
    yield {"owner": owner, "member": member}
    owner.close()
    member.close()


def paste(api: ApiClient, title: str, text: str, doc_type: str) -> str:
    space = next(s["id"] for s in api.get("/api/spaces").json() if s["kind"] == "PERSONAL")
    response = api.post(
        f"/api/spaces/{space}/documents/paste", json={"title": title, "text": text, "doc_type": doc_type}
    )
    assert response.status_code == 201, response.text
    doc_id: str = response.json()["id"]
    assert api.get(f"/api/documents/{doc_id}").json()["status"] == "READY"
    return doc_id


def embedded_pack(api: ApiClient) -> dict[str, Any]:
    packs = api.get("/api/role-packs").json()
    return next(p for p in packs if p["slug"] == "embedded-systems")


def test_role_packs_system_fork_edit_validate(team: dict[str, Any]) -> None:
    owner, member = team["owner"], team["member"]
    system = embedded_pack(member)
    assert system["visibility"] == "SYSTEM"
    assert system["editable"] is False
    detail = member.get(f"/api/role-packs/{system['id']}").json()
    assert "firmware_mcu" in {v["id"] for v in detail["variants"]}
    assert detail["yaml"].startswith("slug: embedded-systems")
    assert member.put(f"/api/role-packs/{system['id']}", json={"yaml": detail["yaml"]}).status_code == 403

    bad = detail["yaml"].replace("emb.c_language: 0.14", "emb.c_language: 0.54", 1)
    checked = member.post("/api/role-packs/validate", json={"yaml": bad}).json()
    assert checked["valid"] is False
    assert any("sum to 1.0" in p["message"] for p in checked["problems"])
    assert member.post("/api/role-packs", json={"yaml": bad}).status_code == 422

    fork = member.post(f"/api/role-packs/{system['id']}/fork", json={"name": "Our firmware pack"})
    assert fork.status_code == 201
    forked = fork.json()
    assert forked["visibility"] == "PRIVATE"
    assert forked["editable"] is True
    edited = forked["spec"]
    edited["description"] = "Tuned for motor control"
    updated = member.put(f"/api/role-packs/{forked['id']}", json={"spec": edited})
    assert updated.status_code == 200
    assert updated.json()["version"] == 2
    assert [v["version"] for v in member.get(f"/api/role-packs/{forked['id']}/versions").json()] == [2, 1]

    # Private packs stay private; members cannot publish to the org, owners can.
    assert forked["id"] not in {p["id"] for p in owner.get("/api/role-packs").json()}
    assert member.post(f"/api/role-packs/{forked['id']}/publish").status_code == 403
    own = owner.post(f"/api/role-packs/{system['id']}/fork", json={}).json()
    published = owner.post(f"/api/role-packs/{own['id']}/publish").json()
    assert published["visibility"] == "ORG"
    assert published["id"] in {p["id"] for p in member.get("/api/role-packs").json()}
    assert (
        member.put(f"/api/role-packs/{own['id']}", json={"spec": own["spec"] | {"description": "x"}}).status_code == 403
    )


def test_question_set_end_to_end(pg: Postgres, team: dict[str, Any]) -> None:  # noqa: PLR0915 - one story
    owner, member = team["owner"], team["member"]
    cv_id = paste(owner, "Priya Raman CV", CV_MD, "CV")
    jd_id = paste(owner, "Senior Firmware JD", JD_MD, "JD")
    pack = embedded_pack(owner)

    created = owner.post(
        "/api/question-sets",
        json={
            "cv_document_id": cv_id,
            "jd_document_id": jd_id,
            "role_pack_id": pack["id"],
            "role_variant": "firmware_mcu",
            "count": 10,
            "seniority": "MID",
        },
    )
    assert created.status_code == 202, created.text
    qset = owner.get(f"/api/question-sets/{created.json()['id']}").json()
    assert qset["status"] == "READY", qset
    items = qset["items"]
    assert len(items) == 10  # count respected even after critic drops and dedupe

    analysis = qset["analysis"]
    assert analysis["critic"]["dropped"] == 1
    assert analysis["critic"]["duplicates"] >= 1  # the generic question returned for every competency
    assert analysis["critic"]["topped_up"] >= 1
    assert any(r["competency_id"] == "emb.low_power" for r in analysis["gap_matrix"]["requirements"])
    assert analysis["gap_matrix"]["requirements"][1]["evidence_refs"] == ["skills[0]"]  # bogus ref removed

    profile = owner.get("/api/cv/profiles", params={"document_id": cv_id}).json()[0]
    index = set(profile["evidence_index"])
    texts = [i["data"]["text"] for i in items]
    assert len(set(texts)) == len(texts)
    for item in items:
        data = item["data"]
        assert data["rationale"]
        assert set(data["evidence_refs"]) <= index  # only real CV sections
        assert data["citations"] == [] or all(c != "not-a-retrieved-document" for c in data["citations"])
        assert data["source"] in {"generated", "role_pack"}
    isr = next(i for i in items if i["data"]["text"].startswith("You cut ADC ISR latency"))
    assert isr["data"]["competency"] == "emb.interrupts_concurrency"  # forced to the planned slot
    assert isr["data"]["evidence_refs"] == ["roles[0].achievements[0]"]  # bogus reference removed
    assert isr["data"]["citations"] == []  # only retrieved documents may be cited
    assert sum(1 for i in items if i["data"]["evidence_refs"]) >= 2

    # CV privacy: contact details only encrypted; domain tags limited to known ones; blind summary scrubbed.
    assert profile["contact"]["email"] == "priya.raman@example.com"
    assert "made-up-tag" not in profile["domain_tags"]
    assert "Priya" not in profile["blind_summary"]
    assert "email" not in json.dumps(profile["structured"])
    with psycopg.connect(pg.dsn("postgres")) as conn:
        row = conn.execute(
            "SELECT pii_encrypted, structured::text FROM candidate_profiles WHERE id = %s", (profile["id"],)
        ).fetchone()
    assert row is not None
    assert b"priya.raman@example.com" not in bytes(row[0])
    assert "priya.raman@example.com" not in row[1]

    # Exports.
    for fmt, magic in (("md", b"# "), ("pdf", b"%PDF"), ("docx", b"PK")):
        exported = owner.get(f"/api/question-sets/{qset['id']}/export", params={"format": fmt})
        assert exported.status_code == 200
        assert exported.content.startswith(magic)
    markdown = owner.get(f"/api/question-sets/{qset['id']}/export", params={"format": "md"}).text
    assert "Confidential" in markdown
    with zipfile.ZipFile(
        io.BytesIO(owner.get(f"/api/question-sets/{qset['id']}/export", params={"format": "docx"}).content)
    ) as z:
        assert "ADC ISR latency" in z.read("word/document.xml").decode()

    # Editing: pin, edit, reorder, regenerate, delete.
    first, second = items[0], items[1]
    pinned = owner.patch(f"/api/question-sets/{qset['id']}/items/{first['id']}", json={"pinned": True, "difficulty": 5})
    assert pinned.json()["pinned"] is True
    assert pinned.json()["data"]["difficulty"] == 5
    assert owner.post(f"/api/question-sets/{qset['id']}/items/{first['id']}/regenerate", json={}).status_code == 400
    regenerated = owner.post(
        f"/api/question-sets/{qset['id']}/items/{second['id']}/regenerate", json={"hint": "harder"}
    )
    assert regenerated.status_code == 200, regenerated.text
    assert regenerated.json()["data"]["regenerated"] is True
    order = [i["id"] for i in reversed(items)]
    reordered = owner.post(f"/api/question-sets/{qset['id']}/items/reorder", json={"item_ids": order}).json()
    assert [i["id"] for i in reordered["items"]] == order
    assert owner.delete(f"/api/question-sets/{qset['id']}/items/{items[-1]['id']}").status_code == 204
    assert owner.get(f"/api/question-sets/{qset['id']}").json()["item_count"] == 9

    # Isolation: a colleague sees nothing.
    assert member.get("/api/question-sets").json() == []
    assert member.get(f"/api/question-sets/{qset['id']}").status_code == 404
    assert member.get(f"/api/question-sets/{qset['id']}/export").status_code == 404
    assert member.get(f"/api/cv/profiles/{profile['id']}").status_code == 404
    stolen = member.post("/api/question-sets", json={"cv_document_id": cv_id, "role_pack_id": pack["id"], "count": 5})
    assert stolen.status_code == 404  # the CV is in the owner's private space


def test_role_builder_repairs_and_validates(team: dict[str, Any]) -> None:
    owner = team["owner"]
    base = embedded_pack(owner)
    built = owner.post(
        "/api/role-packs/build",
        json={"description": "Senior data engineer for streaming pipelines", "base_pack_id": base["id"]},
    )
    assert built.status_code == 200, built.text
    draft = built.json()
    assert draft["valid"] is True, draft["problems"]
    assert draft["spec"]["roles"][0]["weights"] == {"de.sql": 0.8, "beh.ownership": 0.2}  # percentages normalised
    assert draft["diff"]["added_competencies"] == ["de.sql"]
    saved = owner.post("/api/role-packs", json={"yaml": draft["yaml"]})
    assert saved.status_code == 201


def test_generation_without_a_model_fails_cleanly(settings: Settings) -> None:
    org = seed_org(settings)
    api = new_client(settings)
    try:
        assert api.login(org.owner_email).status_code == 200
        cv_id = paste(api, "CV", CV_MD, "CV")
        pack = embedded_pack(api)
        created = api.post("/api/question-sets", json={"cv_document_id": cv_id, "role_pack_id": pack["id"], "count": 5})
        assert created.status_code == 202
        qset = api.get(f"/api/question-sets/{created.json()['id']}").json()
        assert qset["status"] == "FAILED"
        assert "No model is configured" in qset["error"]
    finally:
        api.close()
