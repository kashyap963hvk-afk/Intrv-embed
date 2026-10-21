"""Load test: N concurrent text interviews through the real HTTP + WebSocket API (PROMPT.md Phase 10).

The org's model is a scripted FAKE profile that answers instantly, so the numbers measure the platform itself
(engine, database, locks, socket fan-out), not an LLM. Use a dedicated org on a staging stack:

    make create-owner ORG=loadtest EMAIL=load@example.com NAME="Load Test"     # password on stdin
    INTRV_EMAIL=load@example.com INTRV_PASSWORD=... make loadtest N=50

Rate limits apply per owner (60 interview creations per hour), so run one batch of up to 50 per hour.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

PLAN = {
    "topics": [
        {
            "title": f"Topic {i}",
            "section": section,
            "competencies": ["emb.debugging_tools"],
            "seed_question": f"Question {i}: how did you debug the hardest firmware fault you have met?",
            "probe_angles": ["What did you measure first?"],
            "expected_signals": ["instrumentation"],
        }
        for i, section in enumerate(["CV_WALKTHROUGH", "TECHNICAL", "TECHNICAL", "BEHAVIORAL"], start=1)
    ]
}
CONTROL = {"answer_quality": "adequate", "next_action": "probe", "acknowledgement": "none"}
SCRIPT = [
    {"on": "system", "match": "minute interview for the role", "reply": json.dumps(PLAN)},
    {"on": "system", "match": "You are the turn controller", "reply": json.dumps(CONTROL)},
]
ANSWER = "I attached a logic analyser to the SPI bus, found the chip select glitch and fixed the GPIO timing."


@dataclass
class Stats:
    first_delta: list[float] = field(default_factory=list)  # answer sent → first interviewer words
    full_turn: list[float] = field(default_factory=list)  # answer sent → turn.end
    errors: list[str] = field(default_factory=list)
    busy_retries: int = 0
    completed: int = 0


def pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))] if ordered else float("nan")


async def csrf_client(base: str) -> httpx.AsyncClient:
    client = httpx.AsyncClient(base_url=base, timeout=60)
    token = (await client.get("/api/auth/csrf")).json()["csrf_token"]
    client.headers["X-CSRF-Token"] = token
    return client


async def until_turn_end(ws: Any) -> tuple[float | None, dict[str, Any]]:
    first: float | None = None
    while True:
        frame = json.loads(await ws.recv())
        if frame["type"] == "turn.delta" and first is None:
            first = time.monotonic()
        if frame["type"] in {"turn.end", "error"}:
            return first, frame


async def send_until_done(ws: Any, message: dict[str, Any], stats: Stats) -> tuple[float | None, dict[str, Any]]:
    """Send and wait for the turn, retrying on `busy` like the web app does."""
    for _ in range(30):
        await ws.send(json.dumps(message))
        first, end = await until_turn_end(ws)
        if end["type"] == "error" and end.get("code") == "busy":
            stats.busy_retries += 1
            await asyncio.sleep(1 + random.random())  # noqa: S311 - jitter, not security
            continue
        return first, end
    return None, {"type": "error", "message": "still busy after 30 retries"}


async def interview(
    base: str, ws_base: str, owner: httpx.AsyncClient, pack: str, answers: int, think: float, stats: Stats
) -> None:
    try:
        created = await owner.post(
            "/api/interviews", json={"role_pack_id": pack, "settings": {"turn_mode": "two_call"}}
        )
        created.raise_for_status()
        session_id = created.json()["id"]
        for _ in range(120):
            if (await owner.get(f"/api/interviews/{session_id}")).json()["status"] == "PLANNED":
                break
            await asyncio.sleep(0.5)
        invite = await owner.post(f"/api/interviews/{session_id}/invite", json={})
        token = invite.json()["url"].rsplit("/", 1)[1]
        candidate = await csrf_client(base)
        try:
            (await candidate.post(f"/api/join/{token}/consent", json={"accepted": True})).raise_for_status()
        finally:
            await candidate.aclose()
        async with connect(f"{ws_base}/ws/interview/{session_id}?token={token}", max_size=2**22) as ws:
            await ws.recv()
            await send_until_done(ws, {"type": "start"}, stats)
            for n in range(answers):
                if think:
                    await asyncio.sleep(think * (0.5 + random.random()))  # noqa: S311 - pacing, not security
                sent = time.monotonic()
                answer = {"type": "answer", "text": ANSWER, "client_id": f"{session_id}-{n}"}
                first, end = await send_until_done(ws, answer, stats)
                if end["type"] == "error":
                    stats.errors.append(end.get("message", "error"))
                    continue
                stats.full_turn.append(time.monotonic() - sent)
                if first is not None:
                    stats.first_delta.append(first - sent)
            await ws.send(json.dumps({"type": "end"}))
        stats.completed += 1
    except (httpx.HTTPError, WebSocketException, OSError, KeyError, ValueError) as exc:
        stats.errors.append(f"{type(exc).__name__}: {exc}"[:200])


def report(n: int, answers: int, think: float, wall: float, stats: Stats) -> str:
    def row(name: str, values: list[float]) -> str:
        if not values:
            return f"| {name} | n/a | n/a | n/a |"
        ms = [v * 1000 for v in values]
        return f"| {name} | {statistics.median(ms):.0f} | {pct(ms, 0.95):.0f} | {max(ms):.0f} |"

    lines = [
        f"- Concurrent interviews: {n}, answers each: {answers}, think time: ~{think:g} s, wall time: {wall:.1f} s",
        f"- Completed: {stats.completed}/{n}, turns measured: {len(stats.full_turn)}, errors: {len(stats.errors)}, "
        f"busy retries: {stats.busy_retries}",
        "",
        "| Stage | p50 ms | p95 ms | max ms |",
        "|---|---|---|---|",
        row("Answer → first interviewer words", stats.first_delta),
        row("Answer → turn complete", stats.full_turn),
    ]
    lines += [f"- error: {e}" for e in sorted(set(stats.errors))[:10]]
    return "\n".join(lines)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default=os.environ.get("INTRV_API", "http://localhost:8000"))
    parser.add_argument("-n", "--interviews", type=int, default=50)
    parser.add_argument("--answers", type=int, default=5)
    parser.add_argument("--think", type=float, default=0.0, help="mean seconds a candidate takes per answer")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    ws_base = args.base.replace("http", "ws", 1)

    owner = await csrf_client(args.base)
    login = await owner.post(
        "/api/auth/login",
        json={
            "email": os.environ["INTRV_EMAIL"],
            "password": os.environ["INTRV_PASSWORD"],
            "org_slug": os.environ.get("INTRV_ORG", "loadtest"),
        },
    )
    login.raise_for_status()
    profile = await owner.post(
        "/api/llm/profiles", json={"name": "Load test (scripted)", "kind": "FAKE", "model": "fake", "script": SCRIPT}
    )
    profile.raise_for_status()
    profile_id = profile.json()["id"]
    try:
        pack = next(p["id"] for p in (await owner.get("/api/role-packs")).json() if p["slug"] == "embedded-systems")
        stats = Stats()
        started = time.monotonic()
        await asyncio.gather(
            *(
                interview(args.base, ws_base, owner, pack, args.answers, args.think, stats)
                for _ in range(args.interviews)
            )
        )
        text = report(args.interviews, args.answers, args.think, time.monotonic() - started, stats)
    finally:
        await owner.delete(f"/api/llm/profiles/{profile_id}")
        await owner.aclose()
    print(text)
    if args.report:
        args.report.write_text(text + "\n", encoding="utf-8")
    return 0 if not stats.errors else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
