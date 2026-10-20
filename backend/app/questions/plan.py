"""Deterministic planning for a question set: how many questions per competency, in which categories and at
which difficulty. Weights come from the role variant, boosted where the JD shows gaps or the CV shows evidence."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from app.rolepacks.schema import RolePackSpec, RoleVariant

DEFAULT_MIX: dict[str, float] = {
    "cv_deep_dive": 0.30,
    "technical": 0.35,
    "debugging": 0.15,
    "design": 0.10,
    "behavioral": 0.10,
}
DIFFICULTY_BY_SENIORITY: dict[str, list[int]] = {
    "JUNIOR": [1, 2, 2, 3],
    "MID": [2, 3, 3, 4],
    "SENIOR": [3, 3, 4, 4, 5],
    "STAFF": [3, 4, 4, 5, 5],
}
JD_GAP_BOOST = 1.5
CV_EVIDENCE_BOOST = 1.2
MAX_COUNT = 60


@dataclass(frozen=True, slots=True)
class Slot:
    competency: str
    category: str
    difficulty: int

    def as_json(self) -> dict[str, Any]:
        return {"competency": self.competency, "category": self.category, "difficulty": self.difficulty}


def is_behavioral(spec: RolePackSpec, competency_id: str) -> bool:
    return competency_id.startswith("beh.") or any(b.competency_id == competency_id for b in spec.behavioral)


def largest_remainder(weights: dict[str, float], total: int) -> dict[str, int]:
    """Split `total` proportionally to `weights` with integer counts that add up exactly."""
    if total <= 0 or not weights:
        return dict.fromkeys(weights, 0)
    norm = sum(weights.values())
    raw = {k: total * w / norm for k, w in weights.items()}
    counts = {k: math.floor(v) for k, v in raw.items()}
    leftover = total - sum(counts.values())
    for key in sorted(raw, key=lambda k: (raw[k] - counts[k], weights[k]), reverse=True)[:leftover]:
        counts[key] += 1
    return counts


def _cv_words(profile: dict[str, Any]) -> set[str]:
    words: set[str] = set()
    for skill in profile.get("skills", []):
        words.update(str(skill.get("name", "")).lower().split())
    for project in profile.get("projects", []):
        for tech in project.get("tech", []):
            words.update(str(tech).lower().split())
    return {w.strip(".,()") for w in words if len(w) > 2}  # noqa: PLR2004


def boosted_weights(
    spec: RolePackSpec, role: RoleVariant, profile: dict[str, Any], gap: dict[str, Any] | None
) -> dict[str, float]:
    gaps = {
        r.get("competency_id")
        for r in (gap or {}).get("requirements", [])
        if r.get("status") in {"missing", "partial"} and r.get("competency_id")
    }
    cv = _cv_words(profile)
    weights: dict[str, float] = {}
    for competency_id, weight in role.weights.items():
        competency = spec.competency(competency_id)
        boost = JD_GAP_BOOST if competency_id in gaps else 1.0
        if competency and cv & set(f"{competency.name} {' '.join(competency.signals)}".lower().split()):
            boost *= CV_EVIDENCE_BOOST
        weights[competency_id] = weight * boost
    return weights


def plan_slots(
    spec: RolePackSpec,
    role: RoleVariant,
    *,
    count: int,
    seniority: str,
    difficulty: int | None,
    mix: dict[str, float] | None,
    profile: dict[str, Any],
    gap: dict[str, Any] | None,
) -> list[Slot]:
    count = max(1, min(count, MAX_COUNT))
    weights = boosted_weights(spec, role, profile, gap)
    behavioral = {k: v for k, v in weights.items() if is_behavioral(spec, k)}
    technical = {k: v for k, v in weights.items() if k not in behavioral}
    shares = dict(mix or DEFAULT_MIX)
    if not behavioral:
        shares.pop("behavioral", None)
    category_counts = largest_remainder({k: v for k, v in shares.items() if v > 0}, count)
    behavioral_total = category_counts.pop("behavioral", 0)

    # Technical categories are spread across technical competencies in proportion to weight.
    per_competency = largest_remainder(technical or weights, count - behavioral_total)
    categories = [c for c, n in sorted(category_counts.items(), key=lambda kv: -kv[1]) for _ in range(n)]
    ladder = [difficulty] if difficulty else DIFFICULTY_BY_SENIORITY.get(seniority, DIFFICULTY_BY_SENIORITY["MID"])
    slots: list[Slot] = []
    position = 0
    for competency_id, n in sorted(per_competency.items(), key=lambda kv: -weights[kv[0]]):
        for i in range(n):
            category = categories[position % len(categories)] if categories else "technical"
            slots.append(Slot(competency_id, category, ladder[(position + i) % len(ladder)]))
            position += 1
    for competency_id, n in largest_remainder(behavioral, behavioral_total).items():
        slots.extend(Slot(competency_id, "behavioral", 2) for _ in range(n))
    return slots
