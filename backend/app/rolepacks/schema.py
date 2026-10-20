"""Role Pack schema (docs/DOMAIN_PACK_EMBEDDED.md §1): what good looks like for a role, as validated data.

Validation rules beyond types: unique competency ids, behaviourally anchored levels 1-4 on every competency,
role-variant weights that reference real competencies and sum to 1.0 (±0.01), practical tasks and behavioral
questions that reference existing competencies, and seed questions whose levels are declared seniority levels.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Any, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

WEIGHT_TOLERANCE = 0.01
_ID = r"^[a-z][a-z0-9_.-]{1,63}$"


class Seniority(StrEnum):
    JUNIOR = "JUNIOR"
    MID = "MID"
    SENIOR = "SENIOR"
    STAFF = "STAFF"


class Section(StrEnum):
    OPENING = "OPENING"
    CV_WALKTHROUGH = "CV_WALKTHROUGH"
    TECHNICAL = "TECHNICAL"
    PRACTICAL = "PRACTICAL"
    BEHAVIORAL = "BEHAVIORAL"
    CANDIDATE_QUESTIONS = "CANDIDATE_QUESTIONS"
    CLOSE = "CLOSE"


class TaskKind(StrEnum):
    DEBUG_SNIPPET = "DEBUG_SNIPPET"
    WRITE_CODE = "WRITE_CODE"
    WHITEBOARD = "WHITEBOARD"
    DATASHEET_READING = "DATASHEET_READING"


Text = Annotated[str, Field(min_length=1, max_length=2000)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SeedQuestion(_Strict):
    text: Text
    difficulty: int = Field(ge=1, le=5)
    level: list[Seniority] = Field(default_factory=list)
    expected_signals: list[Text] = Field(default_factory=list, max_length=20)
    follow_ups: list[Text] = Field(default_factory=list, max_length=6)


class Competency(_Strict):
    id: str = Field(pattern=_ID)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    anchors: dict[int, Text]
    signals: list[Text] = Field(default_factory=list, max_length=40)
    red_flags: list[Text] = Field(default_factory=list, max_length=40)
    probe_ladder: list[Text] = Field(default_factory=list, max_length=10)
    seed_questions: list[SeedQuestion] = Field(default_factory=list, max_length=40)

    @field_validator("anchors")
    @classmethod
    def _four_levels(cls, anchors: dict[int, str]) -> dict[int, str]:
        if sorted(anchors) != [1, 2, 3, 4]:
            raise ValueError("anchors must define exactly levels 1, 2, 3 and 4")
        return anchors


class StructureStep(_Strict):
    section: Section
    minutes: int = Field(ge=0, le=120)


class RoleVariant(_Strict):
    id: str = Field(pattern=_ID)
    name: str = Field(min_length=1, max_length=120)
    weights: dict[str, float]
    default_structure: list[StructureStep] = Field(default_factory=list, max_length=12)

    @field_validator("weights")
    @classmethod
    def _weights(cls, weights: dict[str, float]) -> dict[str, float]:
        if not weights:
            raise ValueError("a role variant needs at least one weighted competency")
        if any(w <= 0 for w in weights.values()):
            raise ValueError("weights must be positive")
        total = sum(weights.values())
        if abs(total - 1.0) > WEIGHT_TOLERANCE:
            raise ValueError(f"weights must sum to 1.0 (±{WEIGHT_TOLERANCE}); they sum to {total:.3f}")
        return weights


class TaskTest(_Strict):
    stdin: str = Field(default="", max_length=10_000)
    expected_stdout: str = Field(max_length=10_000)


class PracticalTask(_Strict):
    id: str = Field(pattern=_ID)
    kind: TaskKind
    language: str = Field(default="none", pattern=r"^(c|cpp|python|rust|none)$")
    prompt: str = Field(min_length=1, max_length=5000)
    starter_code: str = Field(default="", max_length=20_000)
    known_bugs: list[Text] = Field(default_factory=list, max_length=20)
    tests: list[TaskTest] = Field(default_factory=list, max_length=20)
    rubric_competencies: list[str] = Field(default_factory=list)


class BehavioralQuestion(_Strict):
    competency_id: str
    question: Text
    star_signals: list[Text] = Field(default_factory=list, max_length=12)


class PersonaDefaults(_Strict):
    interviewer_name: str = Field(default="Alex", min_length=1, max_length=60)
    style: str = Field(default="", max_length=2000)


class RolePackSpec(_Strict):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,63}$")
    version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=4000)
    domain_tags: list[Annotated[str, Field(min_length=1, max_length=60)]] = Field(default_factory=list, max_length=40)
    seniority_levels: list[Seniority] = Field(default_factory=lambda: list(Seniority))
    competencies: list[Competency] = Field(min_length=1, max_length=40)
    roles: list[RoleVariant] = Field(min_length=1, max_length=20)
    practical_tasks: list[PracticalTask] = Field(default_factory=list, max_length=40)
    behavioral: list[BehavioralQuestion] = Field(default_factory=list, max_length=40)
    persona_defaults: PersonaDefaults = Field(default_factory=PersonaDefaults)
    kb_hints: list[Text] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def _references(self) -> Self:
        ids = [c.id for c in self.competencies]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"duplicate competency ids: {', '.join(duplicates)}")
        known = set(ids)
        role_ids = [r.id for r in self.roles]
        if len(set(role_ids)) != len(role_ids):
            raise ValueError("role variant ids must be unique")
        for role in self.roles:
            unknown = sorted(set(role.weights) - known)
            if unknown:
                raise ValueError(f"role '{role.id}' weights unknown competencies: {', '.join(unknown)}")
        for task in self.practical_tasks:
            unknown = sorted(set(task.rubric_competencies) - known)
            if unknown:
                raise ValueError(f"practical task '{task.id}' references unknown competencies: {', '.join(unknown)}")
        for item in self.behavioral:
            if item.competency_id not in known:
                raise ValueError(f"behavioral question references unknown competency '{item.competency_id}'")
        levels = set(self.seniority_levels)
        for competency in self.competencies:
            for question in competency.seed_questions:
                if not set(question.level) <= levels:
                    raise ValueError(f"a seed question in '{competency.id}' uses a level not in seniority_levels")
        return self

    def competency(self, competency_id: str) -> Competency | None:
        return next((c for c in self.competencies if c.id == competency_id), None)

    def role(self, role_id: str | None) -> RoleVariant:
        if role_id is None:
            return self.roles[0]
        found = next((r for r in self.roles if r.id == role_id), None)
        if found is None:
            raise ValueError(f"unknown role variant '{role_id}'")
        return found


class SpecProblem(BaseModel):
    path: str
    message: str


def _path(loc: tuple[Any, ...]) -> str:
    return ".".join(str(part) for part in loc) or "(root)"


def validate_spec(data: Any) -> tuple[RolePackSpec | None, list[SpecProblem]]:
    try:
        return RolePackSpec.model_validate(data), []
    except ValidationError as exc:
        return None, [
            SpecProblem(path=_path(err["loc"]), message=re.sub(r"^Value error, ", "", err["msg"]))
            for err in exc.errors()
        ]


def parse_yaml(text: str) -> tuple[RolePackSpec | None, list[SpecProblem]]:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f"line {mark.line + 1}" if mark is not None else "(yaml)"
        return None, [SpecProblem(path=where, message=f"Invalid YAML: {getattr(exc, 'problem', exc)}")]
    if not isinstance(data, dict):
        return None, [SpecProblem(path="(root)", message="The YAML must be a mapping of role pack fields.")]
    return validate_spec(data)


def to_yaml(spec: RolePackSpec) -> str:
    return yaml.safe_dump(spec.model_dump(mode="json"), sort_keys=False, allow_unicode=True, width=110)
