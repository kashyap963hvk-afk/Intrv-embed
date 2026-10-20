"""Structured agent outputs and settings for the live interview engine (ARCHITECTURE §13)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

AnswerQuality = Literal[
    "strong",
    "adequate",
    "shallow",
    "incorrect",
    "non_answer",
    "dont_know",
    "off_topic",
    "asks_clarification",
    "meta_request",
    "injection_attempt",
]
NextAction = Literal[
    "probe", "clarify", "hint", "challenge", "next_topic", "next_section", "answer_candidate_question", "wrap_up"
]
SectionName = Literal[
    "OPENING", "CV_WALKTHROUGH", "TECHNICAL", "PRACTICAL", "BEHAVIORAL", "CANDIDATE_QUESTIONS", "CLOSE"
]


class TurnControl(BaseModel):
    answer_quality: AnswerQuality = "adequate"
    covered_signals: list[str] = Field(default_factory=list)
    missing_signals: list[str] = Field(default_factory=list)
    claims_to_verify: list[str] = Field(default_factory=list)
    next_action: NextAction = "probe"
    probe_focus: str | None = None
    need_facts: bool = False
    acknowledgement: Literal["none", "neutral", "brief"] = "none"
    note_for_grader: str = ""


class TopicSource(BaseModel):
    type: Literal["cv_claim", "jd_gap", "rubric", "question_set"] = "rubric"
    ref: str = ""


class PlanTopic(BaseModel):
    id: str = Field(default="", max_length=64)
    title: str = Field(min_length=2, max_length=200)
    section: Literal["CV_WALKTHROUGH", "TECHNICAL", "PRACTICAL", "BEHAVIORAL"] = "TECHNICAL"
    competencies: list[str] = Field(default_factory=list)
    source: TopicSource = Field(default_factory=TopicSource)
    rationale: str = ""
    seed_question: str = Field(min_length=5, max_length=1200)
    probe_angles: list[str] = Field(default_factory=list)
    expected_signals: list[str] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)
    planned_minutes: float = Field(default=5, ge=1, le=40)


class InterviewPlan(BaseModel):
    topics: list[PlanTopic] = Field(min_length=1, max_length=30)


class Evidence(BaseModel):
    turn_seq: int
    quote: str = Field(min_length=3, max_length=1000)


class CompetencyGrade(BaseModel):
    competency: str
    anchor_level: int = Field(ge=1, le=4)
    score: float = Field(ge=0, le=10)
    evidence: list[Evidence] = Field(default_factory=list)
    misconceptions: list[str] = Field(default_factory=list)
    verified_claims: list[str] = Field(default_factory=list)
    unverified_claims: list[str] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"] = "medium"


class TopicGradeResult(BaseModel):
    grades: list[CompetencyGrade]


class KnowledgeGap(BaseModel):
    topic: str
    explanation: str
    citations: list[str] = Field(default_factory=list)


class ScorecardNarrative(BaseModel):
    """What the scorecard writer contributes. Scores, evidence and integrity are filled in by code."""

    summary: str = Field(max_length=1500)
    claims_verified: list[str] = Field(default_factory=list)
    claims_contradicted: list[str] = Field(default_factory=list)
    claims_untested: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list, max_length=5)
    concerns: list[str] = Field(default_factory=list, max_length=5)
    knowledge_gaps: list[KnowledgeGap] = Field(default_factory=list)
    next_round_focus: list[str] = Field(default_factory=list)
    recommendation: Literal["strong_no", "no", "lean_no", "lean_yes", "yes", "strong_yes"]
    recommendation_reason: str = Field(max_length=600)


class InterviewSettings(BaseModel):
    """Frozen into each session at creation (template defaults + overrides)."""

    duration_minutes: int = Field(default=45, ge=10, le=120)
    follow_up_depth: int = Field(default=3, ge=0, le=6)
    hints_allowed: bool = True
    strictness: Literal["lenient", "balanced", "strict"] = "balanced"
    language: str = Field(default="English", max_length=40)
    turn_mode: Literal["auto", "single_call", "two_call"] = "auto"
    integrity_mode: Literal["OFF", "STANDARD", "STRICT"] = "STANDARD"
    camera_monitoring: Literal["off", "optional", "required"] = "off"
    blind_grading: bool = True
    grading_runs: int = Field(default=3, ge=1, le=5)
    interviewer_name: str | None = Field(default=None, max_length=60)
    structure: list[dict[str, int | str]] | None = None  # [{section, minutes}], defaults to the role variant's
