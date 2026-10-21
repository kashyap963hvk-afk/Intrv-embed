"""Practical rounds: the embedded pack's tasks run through a Piston-compatible runner (real gcc), the Code Reviewer's
claims are checked against what the candidate wrote, graders see the verified results, and the whiteboard degrades
gracefully without a vision model."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from app.core.config import Settings
from app.practical.piston import PistonClient, diagnostics, with_warnings
from app.rolepacks.service import system_specs
from tests.fakes.piston_server import running_piston
from tests.integration.conftest import Infra, Postgres
from tests.integration.helpers import knowledge_settings, new_client, seed_org
from tests.integration.test_interviews import PLAN, control, pack_id, say, until_turn_end, wait_report

pytestmark = pytest.mark.integration

REFERENCE_BITFIELDS = """#include <stdio.h>
#include <stdint.h>

static uint32_t mask_of(unsigned width) { return width >= 32u ? 0xFFFFFFFFu : ((1u << width) - 1u); }

uint32_t extract_bits(uint32_t reg, unsigned pos, unsigned width) { return (reg >> pos) & mask_of(width); }

uint32_t insert_bits(uint32_t reg, unsigned pos, unsigned width, uint32_t val)
{
    uint32_t mask = mask_of(width) << pos;
    return (reg & ~mask) | ((val << pos) & mask);
}

int main(void)
{
    unsigned long r, p, w, v;
    while (scanf("%lx %lu %lu %lx", &r, &p, &w, &v) == 4) {
        printf("%08lx %08lx\\n", (unsigned long)extract_bits((uint32_t)r, (unsigned)p, (unsigned)w),
               (unsigned long)insert_bits((uint32_t)r, (unsigned)p, (unsigned)w, (uint32_t)v));
    }
    return 0;
}
"""
FIXED_ISR = """static volatile bool data_ready = false;
static volatile uint32_t sample_count = 0;
/* double buffer: the ISR fills one half while main processes the other */
"""
EXPLANATION = (
    "data_ready must be volatile or the -O2 loop never re-reads it. "
    "sample_count grows forever and is passed as the count, so process reads past the 64 element buffer. "
    "main reads the buffer while the ISR overwrites it, so I added a double buffer with a snapshot."
)
REVIEW = {
    "found": [
        {"bug": 1, "quote": "data_ready must be volatile"},
        {"bug": 3, "quote": "process reads past the 64 element buffer"},
        {"bug": 2, "quote": "added a double buffer"},
        {"bug": 5, "quote": "I used a queue for lost events"},  # not in the submission: must be dropped
    ],
    "missed": [4],
    "new_bugs": [],
    "fix_correct": True,
    "reasoning": "high",
    "summary": "Found the volatile, overflow and tearing bugs with sound fixes.",
}


@pytest.fixture(scope="module")
def piston() -> Iterator[str]:
    with running_piston() as (url, _app):
        yield url


def tasks() -> dict[str, Any]:
    spec = next(s for s in system_specs("seed/role_packs").values() if s.slug == "embedded-systems")
    return {t.id: t for t in spec.practical_tasks}


async def _check(url: str, task_id: str, code: str) -> Any:
    async with httpx.AsyncClient() as http:
        return await PistonClient(url, http).check(tasks()[task_id], code)


def test_bitfield_reference_passes_and_starter_fails(piston: str) -> None:
    task = tasks()["write_bitfield_helpers"]
    passing = asyncio.run(_check(piston, task.id, REFERENCE_BITFIELDS))
    assert (passing.compiled, passing.passed, len(passing.tests)) == (True, 5, 5)
    starter = asyncio.run(_check(piston, task.id, task.starter_code))
    assert starter.compiled
    assert starter.passed < len(task.tests)  # the empty starter fails the tests
    assert any("unused parameter" in w for w in starter.warnings)  # static analysis via pragmas, line numbers kept


def test_debug_snippet_compiles_for_static_analysis_only(piston: str) -> None:
    report = asyncio.run(_check(piston, "debug_isr_adc", tasks()["debug_isr_adc"].starter_code))
    assert report.compiled  # only the hardware stubs fail to link
    assert report.tests == []
    warnings, errors = diagnostics(
        "main.c:3:5: warning: unused variable 'x'\n/usr/bin/ld: main.o: undefined reference to `adc_init'\n"
    )
    assert warnings == ["c:3:5: warning: unused variable 'x'"]
    assert errors == []
    assert with_warnings("int x;", "c").endswith("#line 1\nint x;")


@pytest.fixture
def settings(infra: Infra, fresh_valkey: str, piston: str) -> Settings:
    return knowledge_settings(
        infra.database_url, fresh_valkey, infra.qdrant_url, infra.s3_endpoint_url, piston_url=piston
    )


def test_practical_round_end_to_end(pg: Postgres, settings: Settings) -> None:  # noqa: PLR0915 - one story
    script = [
        {"on": "system", "match": "minute interview for the role", "reply": json.dumps(PLAN)},
        {"on": "system", "match": "You are the turn controller", "reply": control("adequate", "probe")},
        {"on": "system", "match": "Review an interview candidate's submission", "reply": json.dumps(REVIEW)},
        {  # the interviewer sees the verified review of the submission (ARCHITECTURE §14.3)
            "on": "system",
            "match": "practical work just submitted.*reviewer: Found the volatile, overflow and tearing bugs",
            "reply": "You covered volatile and the overflow well. How would you size the buffers for bursts?",
        },
        {
            "on": "system",
            "match": "You grade one interview topic.*practical submission for debug_isr_adc.*known bugs found: 1, 3, 2",
            "reply": json.dumps(
                {
                    "grades": [
                        {
                            "competency": c,
                            "anchor_level": 3,
                            "score": 7.5,
                            "evidence": [{"turn_seq": 0, "quote": "data_ready must be volatile"}],
                        }
                        for c in ("emb.c_language", "emb.interrupts_concurrency")
                    ]
                }
            ),
        },
    ]
    org = seed_org(settings)
    owner, candidate = new_client(settings), new_client(settings)
    try:
        assert owner.login(org.owner_email).status_code == 200
        profile = {"name": "Scripted", "kind": "FAKE", "model": "fake", "script": script, "scope": "org"}
        assert owner.post("/api/llm/profiles", json=profile).status_code == 201
        created = owner.post(
            "/api/interviews",
            json={
                "role_pack_id": pack_id(owner),
                "role_variant": "firmware_mcu",
                "settings": {
                    "turn_mode": "two_call",
                    "follow_up_depth": 0,
                    "practical_tasks": ["debug_isr_adc", "write_bitfield_helpers", "datasheet_timing"],
                },
            },
        ).json()
        session_id = created["id"]
        detail = owner.get(f"/api/interviews/{session_id}").json()
        sections = [t["section"] for t in detail["plan"]["topics"]]
        assert sections == sorted(sections, key=["CV_WALKTHROUGH", "TECHNICAL", "PRACTICAL", "BEHAVIORAL"].index)
        practical = [t for t in detail["plan"]["topics"] if t["section"] == "PRACTICAL"]
        assert [t["task_id"] for t in practical] == ["debug_isr_adc", "write_bitfield_helpers"]
        assert any("datasheet_timing: skipped" in n for n in detail["plan"]["notes"])  # no datasheet available

        token = owner.post(f"/api/interviews/{session_id}/invite", json={}).json()["url"].rsplit("/", 1)[1]
        assert candidate.post(f"/api/join/{token}/consent", json={"accepted": True}).status_code == 200
        assert candidate.get(f"/api/join/{token}/practical").status_code == 409  # no task before it starts
        with candidate.websocket(f"/ws/interview/{session_id}?token={token}") as ws:
            ws.receive_json()
            ws.send_json({"type": "start"})
            until_turn_end(ws)
            ws.receive_json()
            for answer in ("I owned the bootloader.", "DMA in circular mode.", "Watermarks on every task stack."):
                say(ws, answer)  # depth 0: each answer moves to the next topic

            task = candidate.get(f"/api/join/{token}/practical").json()
            assert task["task_id"] == "debug_isr_adc"
            assert "known_bugs" not in task  # never shown to the candidate
            run = candidate.post(f"/api/join/{token}/practical/run", json={"code": task["starter_code"]}).json()
            assert run["compiled"] is True
            submitted = candidate.post(
                f"/api/join/{token}/practical/submit",
                json={"code": task["starter_code"] + FIXED_ISR, "explanation": EXPLANATION},
            ).json()
            assert submitted["reviewed"] is True
            ws.send_json(
                {
                    "type": "answer",
                    "text": EXPLANATION,
                    "client_id": "p1",
                    "meta": {"submission_id": submitted["submission_id"]},
                }
            )
            reply, _ = until_turn_end(ws)
            assert "size the buffers for bursts" in reply["text"]
            ws.receive_json()

            bitfields = candidate.get(f"/api/join/{token}/practical").json()
            assert bitfields["task_id"] == "write_bitfield_helpers"
            result = candidate.post(f"/api/join/{token}/practical/run", json={"code": REFERENCE_BITFIELDS}).json()
            assert (result["passed"], result["total"]) == (5, 5)
            diagram = candidate.post(
                f"/api/join/{token}/whiteboard", files={"file": ("d.png", b"\x89PNG\r\n\x1a\n", "image/png")}
            ).json()
            assert diagram["vision_available"] is False  # no vision model: the interview just continues
            ws.send_json({"type": "end"})
            ws.receive_json()

        report = wait_report(owner, session_id)
        submissions = report["practical"]
        review = next(s for s in submissions if s["task_id"] == "debug_isr_adc")["review"]
        assert sorted(f["bug"] for f in review["found"]) == [1, 2, 3]  # the invented quote was dropped
        assert review["missed"] == [4, 5]
        assert next(s for s in submissions if s["kind"] == "WHITEBOARD")["image_url"]
        scores = {c["id"]: c for c in report["scorecard"]["data"]["competencies"]}
        assert scores["emb.interrupts_concurrency"]["assessed"] is True  # graded from the practical evidence
    finally:
        owner.close()
        candidate.close()
