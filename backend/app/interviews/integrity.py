"""Interview integrity signals (ADR 0005, ARCHITECTURE §15.1): browser events, answer-level signals and prompt
attacks. Signals are evidence for a human reviewer and a cue for the interviewer to probe; graders never see them."""

from __future__ import annotations

import re
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import IntegrityEvent, InterviewSession
from app.rag.risk import injection_matches

Severity = Literal["info", "low", "medium", "high"]
SEVERITY_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3}

BrowserKind = Literal[
    "tab_hidden",
    "window_blur",
    "fullscreen_exit",
    "paste",
    "copy",
    "extended_display",
    "devtools",
    "split_screen",
    "screen_share_stopped",
    "camera_face_absent",
    "camera_multiple_faces",
    "camera_gaze_away",
    "virtual_camera",
]
MAX_EVENTS_PER_BATCH = 50
LONG_ABSENCE_MS = 5000
LARGE_PASTE_CHARS = 200
FAST_TYPING_CPS = 20
FAST_TYPING_MIN_CHARS = 300
SILENCE_THEN_FLUENT_S = 90
FLUENT_CHARS = 600
REFERENCE_OVERLAP = 0.35
SHINGLE = 6
WORTH_A_LOOK = 3  # medium-or-higher signals before the rollup asks a reviewer to look


class BrowserEvent(BaseModel):
    kind: BrowserKind
    duration_ms: int | None = Field(default=None, ge=0, le=3_600_000)
    detail: dict[str, Any] = Field(default_factory=dict)


def browser_severity(event: BrowserEvent, mode: str) -> Severity:
    strict = mode == "STRICT"
    match event.kind:
        case "tab_hidden":
            return "medium" if (event.duration_ms or 0) >= LONG_ABSENCE_MS else "low"
        case "paste":
            return "medium" if int(event.detail.get("chars", 0)) >= LARGE_PASTE_CHARS else "low"
        case "extended_display" | "devtools" | "camera_multiple_faces" | "virtual_camera":
            return "medium"
        case "fullscreen_exit" | "screen_share_stopped" | "camera_face_absent" | "camera_gaze_away":
            return "medium" if strict else "low"
        case _:
            return "low"


async def record_browser_events(
    db: AsyncSession, session: InterviewSession, events: list[BrowserEvent], turn_seq: int | None
) -> int:
    mode = session.settings.get("integrity_mode", "STANDARD")
    if mode == "OFF":
        return 0
    allowed_camera = session.settings.get("camera_monitoring", "off") != "off"
    stored = 0
    for event in events[:MAX_EVENTS_PER_BATCH]:
        if event.kind.startswith("camera_") and not allowed_camera:
            continue  # never accept camera signals the candidate did not consent to
        db.add(
            IntegrityEvent(
                org_id=session.org_id,
                owner_user_id=session.owner_user_id,
                session_id=session.id,
                kind=event.kind,
                severity=browser_severity(event, mode),
                source="CAMERA" if event.kind.startswith(("camera_", "virtual_camera")) else "BROWSER",
                turn_seq=turn_seq,
                duration_ms=event.duration_ms,
                detail={k: v for k, v in event.detail.items() if isinstance(v, int | float | str | bool)},
            )
        )
        stored += 1
    return stored


def _shingles(text: str) -> set[tuple[str, ...]]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {tuple(words[i : i + SHINGLE]) for i in range(max(0, len(words) - SHINGLE + 1))}


def reference_overlap(answer: str, references: list[str]) -> float:
    mine = _shingles(answer)
    if not mine:
        return 0.0
    theirs: set[tuple[str, ...]] = set()
    for reference in references:
        theirs |= _shingles(reference)
    return len(mine & theirs) / len(mine)


def answer_signals(
    text: str, meta: dict[str, Any], *, references: list[str], response_delay_s: float | None
) -> list[dict[str, Any]]:
    """Signals computed on the server from one text answer (no client trust needed for overlap and injection)."""
    signals: list[dict[str, Any]] = []
    chars = len(text)
    typing_ms = meta.get("typing_ms")
    if isinstance(typing_ms, int | float) and typing_ms > 0 and chars >= FAST_TYPING_MIN_CHARS:
        speed = chars / (typing_ms / 1000)
        if speed > FAST_TYPING_CPS:
            signals.append(
                {"kind": "burst_typing", "severity": "medium", "detail": {"chars_per_second": round(speed, 1)}}
            )
    pasted = meta.get("paste_chars")
    if isinstance(pasted, int | float) and chars and pasted >= max(LARGE_PASTE_CHARS, chars * 0.5):
        signals.append(
            {"kind": "pasted_answer", "severity": "medium", "detail": {"pasted_chars": int(pasted), "chars": chars}}
        )
    if response_delay_s is not None and response_delay_s >= SILENCE_THEN_FLUENT_S and chars >= FLUENT_CHARS:
        signals.append(
            {"kind": "long_pause_then_fluent", "severity": "low", "detail": {"delay_s": round(response_delay_s)}}
        )
    overlap = reference_overlap(text, references)
    if overlap >= REFERENCE_OVERLAP:
        signals.append(
            {"kind": "matches_reference_text", "severity": "medium", "detail": {"overlap": round(overlap, 2)}}
        )
    hits = injection_matches(text)
    if hits:
        signals.append({"kind": "injection_attempt", "severity": "high", "detail": {"matches": hits[:3]}})
    return signals


async def record_answer_signals(
    db: AsyncSession, session: InterviewSession, turn_seq: int, signals: list[dict[str, Any]]
) -> None:
    if session.settings.get("integrity_mode", "STANDARD") == "OFF":
        signals = [s for s in signals if s["kind"] == "injection_attempt"]  # prompt attacks are always recorded
    for signal in signals:
        db.add(
            IntegrityEvent(
                org_id=session.org_id,
                owner_user_id=session.owner_user_id,
                session_id=session.id,
                kind=signal["kind"],
                severity=signal["severity"],
                source="AGENT" if signal["kind"] == "injection_attempt" else "ANSWER",
                turn_seq=turn_seq,
                detail=signal.get("detail", {}),
            )
        )


async def unaddressed(db: AsyncSession, session_id: uuid.UUID) -> list[IntegrityEvent]:
    return list(
        await db.scalars(
            select(IntegrityEvent)
            .where(IntegrityEvent.session_id == session_id, IntegrityEvent.addressed.is_(False))
            .order_by(IntegrityEvent.created_at)
        )
    )


async def mark_addressed(db: AsyncSession, session_id: uuid.UUID) -> None:
    await db.execute(
        update(IntegrityEvent)
        .where(IntegrityEvent.session_id == session_id, IntegrityEvent.addressed.is_(False))
        .values(addressed=True)
    )


def summarise(events: list[IntegrityEvent]) -> dict[str, Any]:
    """Reviewer rollup. Signals, not proof: the label is part of the data so every view carries it."""
    counts: dict[str, int] = {}
    away = {"tab_hidden": 0, "window_blur": 0}
    for event in events:
        counts[event.kind] = counts.get(event.kind, 0) + 1
        if event.kind in away and event.duration_ms:
            away[event.kind] += event.duration_ms
    # A tab switch usually fires both signals for one absence, so take the larger total (a lower bound), never the sum.
    away_ms = max(away.values())
    medium_plus = sum(1 for e in events if SEVERITY_RANK.get(e.severity, 0) >= SEVERITY_RANK["medium"])
    return {
        "label": "Signals, not proof. A human must review before any decision.",
        "rollup": "worth a look"
        if medium_plus >= WORTH_A_LOOK or counts.get("injection_attempt")
        else "nothing notable",
        "counts": counts,
        "medium_or_higher": medium_plus,
        "time_away_seconds": round(away_ms / 1000),
    }
