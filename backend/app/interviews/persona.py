"""Template persona text (prompt layer L2): variables and lint against the platform rules (L0)."""

from __future__ import annotations

import re
from collections.abc import Mapping

from app.agents import composer

PERSONA_VARIABLES = ("role_title", "candidate_first_name", "company", "seniority")
_PERSONA_VAR = re.compile(r"\{\{\s*(" + "|".join(PERSONA_VARIABLES) + r")\s*\}\}")
# Phrases in a user-edited persona (L2) that contradict the platform rules (L0). L0 still wins at runtime.
LINT_RULES: tuple[tuple[str, str], ...] = (
    (r"\b(tell|give|share|reveal)\b.{0,40}\b(score|rating|grade|rubric|evaluation)", "reveals scores or the rubric"),
    (r"\b(tell|give)\b.{0,30}\b(the )?(correct |right )?answers?\b", "gives candidates the answers"),
    (r"\bignore\b.{0,30}\b(rules|instructions|platform)", "asks the interviewer to ignore platform rules"),
    (
        r"\b(age|religion|marital|married|pregnan|children|nationality|citizenship|caste|ethnicity|disabilit|"
        r"health|political|union)\b",
        "mentions a protected characteristic (never ask about it)",
    ),
    (r"\b(several|multiple|many|three|two) questions\b", "asks several questions per turn (one per turn is enforced)"),
    (r"\b(pretend|claim) (to be|you are) (a )?human\b", "hides that the interviewer is an AI"),
)


def lint_persona(text: str) -> list[str]:
    return [f"The persona {why}." for pattern, why in LINT_RULES if re.search(pattern, text, re.IGNORECASE)]


def render_persona(text: str, values: Mapping[str, str]) -> str:
    return composer.neutralize(_PERSONA_VAR.sub(lambda m: values.get(m.group(1), ""), text))
