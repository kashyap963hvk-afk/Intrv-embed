"""Structured outputs of the agents (validated with Pydantic, requested as JSON schema through the gateway)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Category = Literal["technical", "cv_deep_dive", "debugging", "design", "behavioral"]


# --- CV Analyst --------------------------------------------------------------------------------------------


class Contact(BaseModel):
    email: str | None = None
    phone: str | None = None
    links: list[str] = Field(default_factory=list)


class RoleEntry(BaseModel):
    company: str = ""
    title: str = ""
    start: str | None = None
    end: str | None = None
    achievements: list[str] = Field(default_factory=list)
    tech: list[str] = Field(default_factory=list)


class Project(BaseModel):
    name: str = ""
    role: str | None = None
    tech: list[str] = Field(default_factory=list)
    claims: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)


class Skill(BaseModel):
    name: str
    evidence_refs: list[str] = Field(default_factory=list)


class Education(BaseModel):
    institution: str = ""
    degree: str = ""
    year: str | None = None


class VagueClaim(BaseModel):
    text: str
    why_vague: str


class Gap(BaseModel):
    start: str = Field(alias="from", default="")
    end: str = Field(alias="to", default="")

    model_config = {"populate_by_name": True}


class ExtractedProfile(BaseModel):
    """What the extractor returns. Name and contact are split off and encrypted before storage."""

    name: str | None = None
    contact: Contact = Field(default_factory=Contact)
    total_years: float | None = None
    roles: list[RoleEntry] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    skills: list[Skill] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    gaps: list[Gap] = Field(default_factory=list)
    vague_claims: list[VagueClaim] = Field(default_factory=list)
    seniority_estimate: Literal["JUNIOR", "MID", "SENIOR", "STAFF"] | None = None
    domain_tags: list[str] = Field(default_factory=list)
    blind_summary: str = Field(
        default="",
        description="3-5 sentence professional summary without name, gender cues, age, nationality or school names",
    )


# --- JD Matcher --------------------------------------------------------------------------------------------


class Requirement(BaseModel):
    text: str
    competency_id: str | None = None
    status: Literal["met", "partial", "missing", "unclear"]
    evidence_refs: list[str] = Field(default_factory=list)
    note: str = ""


class GapMatrix(BaseModel):
    requirements: list[Requirement] = Field(default_factory=list)


# --- Question generation -----------------------------------------------------------------------------------


class Citation(BaseModel):
    document_id: str
    title: str = ""


class GeneratedQuestion(BaseModel):
    text: str = Field(min_length=10, max_length=1200)
    competency: str
    category: Category
    difficulty: int = Field(ge=1, le=5)
    rationale: str = Field(min_length=5, max_length=800)
    evidence_refs: list[str] = Field(default_factory=list)
    expected_signals: list[str] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)
    follow_ups: list[str] = Field(default_factory=list)
    ideal_answer: str = Field(default="", max_length=1500)
    citations: list[str] = Field(default_factory=list, description="document ids from the reference snippets")


class GeneratedQuestions(BaseModel):
    questions: list[GeneratedQuestion]


class CriticVerdict(BaseModel):
    index: int = Field(ge=0)
    action: Literal["keep", "rewrite", "drop"]
    reason: str = ""
    rewritten: GeneratedQuestion | None = None


class CriticResult(BaseModel):
    verdicts: list[CriticVerdict]
