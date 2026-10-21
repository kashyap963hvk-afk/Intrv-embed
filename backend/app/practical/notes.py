"""What the interviewer sees of the candidate's practical work on the current topic (ARCHITECTURE §14.3): verified
test results, the reviewer's summary and the whiteboard reader's description. Kept free of engine imports so the
engine can load it."""

from __future__ import annotations

from app.db.models import PracticalSubmission

MAX_NOTE_CHARS = 1500


def interviewer_notes(rows: list[PracticalSubmission]) -> list[str]:
    notes: list[str] = []
    for row in rows:
        review = row.review or {}
        if row.kind == "WHITEBOARD":
            description = str(review.get("description") or "").strip()
            notes.append(f"Diagram: {description}" if description else "Diagram submitted (no description available).")
            continue
        results = row.results or {}
        parts = ["Code submission"]
        if results.get("total"):
            parts.append(f"tests passed {results.get('passed', 0)}/{results['total']}")
        if results.get("compiled") is False:
            parts.append("did not compile")
        if results.get("warnings"):
            parts.append(f"compiler warnings {len(results['warnings'])}")
        if review.get("summary"):
            parts.append(f"reviewer: {review['summary']}")
        notes.append("; ".join(parts))
    return [note[:MAX_NOTE_CHARS] for note in notes[-4:]]
