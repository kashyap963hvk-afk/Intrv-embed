"""Role packs (with version history), candidate profiles extracted from CVs, and generated question sets."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Index, Integer, LargeBinary, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin, str_enum


class PackVisibility(StrEnum):
    SYSTEM = "SYSTEM"  # shipped with the product; read-only, fork to change
    ORG = "ORG"  # published by an owner for everyone in the org
    PRIVATE = "PRIVATE"  # a member's own pack


class RolePack(IdMixin, TimestampMixin, Base):
    """`owner_user_id` NULL = visible to the whole org (SYSTEM/ORG); otherwise private to that member."""

    __tablename__ = "role_packs"
    __table_args__ = (
        Index("ix_role_packs_org_slug", "org_id", "slug"),
        Index(
            "uq_role_packs_system_slug",
            "org_id",
            "slug",
            unique=True,
            postgresql_where=text("visibility = 'SYSTEM'"),
        ),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    visibility: Mapped[PackVisibility] = mapped_column(str_enum(PackVisibility, "pack_visibility"))
    slug: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    domain_tags: Mapped[list[str]] = mapped_column(ARRAY(String(60)), default=list, server_default="{}")
    version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    spec: Mapped[dict[str, Any]] = mapped_column()
    forked_from_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("role_packs.id", ondelete="SET NULL"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))


class RolePackVersion(IdMixin, Base):
    __tablename__ = "role_pack_versions"
    __table_args__ = (UniqueConstraint("pack_id", "version"),)

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    pack_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("role_packs.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    spec: Mapped[dict[str, Any]] = mapped_column()
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))


class CandidateProfile(IdMixin, TimestampMixin, Base):
    """Structured CV facts. Contact details live only in `pii_encrypted`; `blind_summary` is name- and
    demographics-free for blind scoring."""

    __tablename__ = "candidate_profiles"

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    document_version: Mapped[int] = mapped_column(Integer, default=1)
    structured: Mapped[dict[str, Any]] = mapped_column()
    pii_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)
    blind_summary: Mapped[str] = mapped_column(Text, default="", server_default="")
    domain_tags: Mapped[list[str]] = mapped_column(ARRAY(String(60)), default=list, server_default="{}")
    model: Mapped[str | None] = mapped_column(String(200))


class SetStatus(StrEnum):
    GENERATING = "GENERATING"
    READY = "READY"
    FAILED = "FAILED"


class QuestionSet(IdMixin, TimestampMixin, Base):
    __tablename__ = "question_sets"

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    cv_document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"))
    jd_document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"))
    candidate_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("candidate_profiles.id", ondelete="SET NULL")
    )
    role_pack_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("role_packs.id", ondelete="SET NULL"))
    role_pack_version: Mapped[int | None] = mapped_column(Integer)
    role_variant: Mapped[str | None] = mapped_column(String(64))
    # {"count", "seniority", "difficulty", "mix", "space_ids"}: what was asked for
    params: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"), default=dict)
    # gap matrix, competency plan and critic summary, kept for transparency
    analysis: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"), default=dict)
    status: Mapped[SetStatus] = mapped_column(str_enum(SetStatus, "question_set_status"), default=SetStatus.GENERATING)
    progress: Mapped[str] = mapped_column(String(200), default="", server_default="")
    error: Mapped[str | None] = mapped_column(Text)


class QuestionItem(IdMixin, TimestampMixin, Base):
    __tablename__ = "question_items"
    __table_args__ = (Index("ix_question_items_set_ord", "set_id", "ord"),)

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    set_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("question_sets.id", ondelete="CASCADE"))
    ord: Mapped[int] = mapped_column(Integer)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    data: Mapped[dict[str, Any]] = mapped_column()
