"""Content risk signals recorded on documents and shown to human reviewers.

Hidden text in CVs ("white text" stuffed with keywords or instructions for AI screeners) and
prompt-injection phrases are flagged. Hidden text is also removed from what the AI sees.
"""

from __future__ import annotations

import re

INJECTION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"ignore (all |any )?(previous|prior|above|earlier) (instructions|prompts|messages)",
        r"disregard (all |any )?(previous|prior|above) (instructions|prompts)",
        r"\byou are now\b",
        r"\bsystem prompt\b",
        r"\b(rate|score|grade|rank) (this|the|me|this candidate|the candidate)\b"
        r".{0,40}\b(10|ten|highest|top|perfect)\b",
        r"\b(hire|shortlist|advance) (this|the) candidate\b",
        r"<\s*/?\s*(system|assistant|instructions?)\s*>",
        r"\bnew instructions?\s*:",
    )
]


def injection_matches(text: str, limit: int = 10) -> list[str]:
    found: list[str] = []
    for pattern in INJECTION_PATTERNS:
        for match in pattern.finditer(text):
            snippet = text[max(0, match.start() - 30) : match.end() + 30].replace("\n", " ").strip()
            found.append(snippet)
            if len(found) >= limit:
                return found
    return found
