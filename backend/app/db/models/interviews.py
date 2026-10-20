"""Interview templates, sessions, turns, shadow grades, scorecards and integrity events."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin, str_enum


class TemplateVisibility(StrEnum):
    PRIVATE = "PRIVATE"
    ORG = "ORG"


class SessionStatus(StrEnum):
    PREPARING = "PREPARING"  # CV analysis + planning in the background
    PLANNED = "PLANNED"  # plan ready for review; candidate may join
    LIVE = "LIVE"
    COMPLETED = "COMPLETED"  # interview over; grading/scorecard may still run
    ABORTED = "ABORTED"
    FAILED = "FAILED"  # preparation failed


class Speaker(StrEnum):
    INTERVIEWER = "INTERVIEWER"
    CANDIDATE = "CANDIDATE"
    SYSTEM = "SYSTEM"


class ScorecardStatus(StrEnum):
    PENDING = "PENDING"
    READY = "READY"
    FAILED = "FAILED"


class InterviewTemplate(IdMixin, TimestampMixin, Base):
    """Reusable interview setup. `owner_user_id` NULL = shared with the org (managed by owners)."""

    __tablename__ = "interview_templates"

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    visibility: Mapped[TemplateVisibility] = mapped_column(str_enum(TemplateVisibility, "template_visibility"))
    name: Mapped[str] = mapped_column(String(120))
    role_pack_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("role_packs.id", ondelete="SET NULL"))
    role_variant: Mapped[str | None] = mapped_column(String(64))
    persona_prompt: Mapped[str] = mapped_column(Text, default="", server_default="")  # L2, user-editable
    settings: Mapped[dict[str, Any]] = mapped_column(server_default=sql_text("'{}'::jsonb"), default=dict)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class InterviewSession(IdMixin, TimestampMixin, Base):
    """One interview. Private to its creator; the candidate reaches it only through the invite token."""

    __tablename__ = "interview_sessions"

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    candidate_label: Mapped[str] = mapped_column(String(120), default="", server_default="")
    template_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("interview_templates.id", ondelete="SET NULL"))
    role_pack_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("role_packs.id", ondelete="SET NULL"))
    role_pack_version: Mapped[int | None] = mapped_column(Integer)
    role_variant: Mapped[str | None] = mapped_column(String(64))
    cv_document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"))
    jd_document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"))
    candidate_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("candidate_profiles.id", ondelete="SET NULL")
    )
    question_set_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("question_sets.id", ondelete="SET NULL"))
    mode: Mapped[str] = mapped_column(String(16), default="TEXT", server_default="TEXT")
    status: Mapped[SessionStatus] = mapped_column(str_enum(SessionStatus, "session_status"))
    progress: Mapped[str] = mapped_column(String(200), default="", server_default="")
    error: Mapped[str | None] = mapped_column(Text)
    # Frozen at creation: settings (duration, depth, hints, integrity mode…), persona text, pack snapshot.
    settings: Mapped[dict[str, Any]] = mapped_column(server_default=sql_text("'{}'::jsonb"), default=dict)
    persona: Mapped[dict[str, Any]] = mapped_column(server_default=sql_text("'{}'::jsonb"), default=dict)
    pack_snapshot: Mapped[dict[str, Any]] = mapped_column(server_default=sql_text("'{}'::jsonb"), default=dict)
    plan: Mapped[dict[str, Any]] = mapped_column(server_default=sql_text("'{}'::jsonb"), default=dict)
    acl_snapshot: Mapped[list[str]] = mapped_column(ARRAY(String(36)), default=list, server_default="{}")
    state: Mapped[dict[str, Any]] = mapped_column(server_default=sql_text("'{}'::jsonb"), default=dict)
    invite_token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    invite_expires_at: Mapped[datetime | None]
    consent: Mapped[dict[str, Any] | None] = mapped_column()
    started_at: Mapped[datetime | None]
    ended_at: Mapped[datetime | None]


class InterviewTurn(IdMixin, Base):
    __tablename__ = "interview_turns"
    __table_args__ = (UniqueConstraint("session_id", "seq"),)

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("interview_sessions.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    speaker: Mapped[Speaker] = mapped_column(str_enum(Speaker, "turn_speaker"))
    text: Mapped[str] = mapped_column(Text)
    section: Mapped[str | None] = mapped_column(String(32))
    topic_id: Mapped[str | None] = mapped_column(String(64))
    control: Mapped[dict[str, Any] | None] = mapped_column()
    latency: Mapped[dict[str, Any] | None] = mapped_column()
    meta: Mapped[dict[str, Any] | None] = mapped_column()  # e.g. typing time and paste counts for text answers
    created_at: Mapped[datetime] = mapped_column(server_default=sql_text("now()"))


class TopicGrade(IdMixin, Base):
    __tablename__ = "topic_grades"
    __table_args__ = (Index("ix_topic_grades_session_topic", "session_id", "topic_id"),)

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("interview_sessions.id", ondelete="CASCADE"))
    topic_id: Mapped[str] = mapped_column(String(64))
    competency: Mapped[str] = mapped_column(String(64))
    run_no: Mapped[int] = mapped_column(Integer, default=0)
    anchor_level: Mapped[int | None] = mapped_column(Integer)
    score: Mapped[float | None] = mapped_column(Float)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=sql_text("'[]'::jsonb"), default=list)
    detail: Mapped[dict[str, Any]] = mapped_column(server_default=sql_text("'{}'::jsonb"), default=dict)
    confidence: Mapped[str] = mapped_column(String(16), default="medium")
    model: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(server_default=sql_text("now()"))


class Scorecard(IdMixin, TimestampMixin, Base):
    __tablename__ = "scorecards"

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("interview_sessions.id", ondelete="CASCADE"), unique=True)
    status: Mapped[ScorecardStatus] = mapped_column(str_enum(ScorecardStatus, "scorecard_status"))
    data: Mapped[dict[str, Any]] = mapped_column(server_default=sql_text("'{}'::jsonb"), default=dict)
    variance_flags: Mapped[list[str]] = mapped_column(ARRAY(String(64)), default=list, server_default="{}")
    recommendation: Mapped[str | None] = mapped_column(String(16))
    error: Mapped[str | None] = mapped_column(Text)
    human_decision: Mapped[str | None] = mapped_column(String(16))
    decision_notes: Mapped[str | None] = mapped_column(Text)
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at: Mapped[datetime | None]


class IntegrityEvent(IdMixin, Base):
    """Interview integrity signals (ADR 0005). Never read by graders; shown to human reviewers as a timeline."""

    __tablename__ = "integrity_events"
    __table_args__ = (Index("ix_integrity_events_session", "session_id", "created_at"),)

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("interview_sessions.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(48))
    severity: Mapped[str] = mapped_column(String(16))  # info | low | medium | high
    source: Mapped[str] = mapped_column(String(16))  # BROWSER | SCREEN | ANSWER | AGENT | AUDIO | CAMERA
    turn_seq: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    detail: Mapped[dict[str, Any]] = mapped_column(server_default=sql_text("'{}'::jsonb"), default=dict)
    addressed: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sql_text("false"))
    created_at: Mapped[datetime] = mapped_column(server_default=sql_text("now()"))


class PracticalSubmission(IdMixin, Base):
    """Code, a diagram or an answer to a practical task, with test results and the reviewer's (verified) findings."""

    __tablename__ = "practical_submissions"
    __table_args__ = (Index("ix_practical_submissions_session", "session_id", "created_at"),)

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("interview_sessions.id", ondelete="CASCADE"))
    topic_id: Mapped[str] = mapped_column(String(64))
    task_id: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(32))
    code: Mapped[str] = mapped_column(Text, default="", server_default="")
    explanation: Mapped[str] = mapped_column(Text, default="", server_default="")
    attachment_key: Mapped[str | None] = mapped_column(String(500))  # whiteboard image in the session's S3 prefix
    results: Mapped[dict[str, Any]] = mapped_column(server_default=sql_text("'{}'::jsonb"), default=dict)
    review: Mapped[dict[str, Any] | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(server_default=sql_text("now()"))
