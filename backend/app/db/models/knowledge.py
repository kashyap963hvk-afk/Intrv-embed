"""Knowledge spaces, access grants, documents and chunks."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin, str_enum


class SpaceKind(StrEnum):
    ORG = "ORG"
    PERSONAL = "PERSONAL"


class PrincipalType(StrEnum):
    USER = "USER"
    GROUP = "GROUP"


class Permission(StrEnum):
    READ = "READ"
    CONTRIBUTE = "CONTRIBUTE"


class DocType(StrEnum):
    CV = "CV"
    JD = "JD"
    RUBRIC = "RUBRIC"
    QUESTION_BANK = "QUESTION_BANK"
    KNOWLEDGE = "KNOWLEDGE"
    DATASHEET = "DATASHEET"
    CODE = "CODE"
    OTHER = "OTHER"


class DocStatus(StrEnum):
    PENDING = "PENDING"
    PARSING = "PARSING"
    EMBEDDING = "EMBEDDING"
    READY = "READY"
    FAILED = "FAILED"
    DELETING = "DELETING"


class ChunkKind(StrEnum):
    PARENT = "PARENT"
    CHILD = "CHILD"


class KnowledgeSpace(IdMixin, TimestampMixin, Base):
    __tablename__ = "knowledge_spaces"

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    kind: Mapped[SpaceKind] = mapped_column(str_enum(SpaceKind, "space_kind"))
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")


class SpaceGrant(IdMixin, Base):
    __tablename__ = "space_grants"
    __table_args__ = (UniqueConstraint("space_id", "principal_type", "principal_id"),)

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    space_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_spaces.id", ondelete="CASCADE"), index=True)
    principal_type: Mapped[PrincipalType] = mapped_column(str_enum(PrincipalType, "principal_type"))
    principal_id: Mapped[uuid.UUID] = mapped_column(index=True)
    permission: Mapped[Permission] = mapped_column(str_enum(Permission, "space_permission"))
    granted_by: Mapped[uuid.UUID | None]
    expires_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))


class Document(IdMixin, TimestampMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (Index("ix_documents_space_created", "space_id", "created_at"),)

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    space_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_spaces.id", ondelete="CASCADE"))
    uploader_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    source_url: Mapped[str | None] = mapped_column(String(2000))
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("connector_sources.id", ondelete="SET NULL"), index=True
    )
    external_id: Mapped[str | None] = mapped_column(String(500))
    external_etag: Mapped[str | None] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(300))
    filename: Mapped[str | None] = mapped_column(String(300))
    mime: Mapped[str | None] = mapped_column(String(120))
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    doc_type: Mapped[DocType] = mapped_column(str_enum(DocType, "doc_type"))
    status: Mapped[DocStatus] = mapped_column(str_enum(DocStatus, "doc_status"), default=DocStatus.PENDING)
    error: Mapped[str | None] = mapped_column(Text)
    storage_key: Mapped[str | None] = mapped_column(String(500))
    page_count: Mapped[int | None] = mapped_column(Integer)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    risk_flags: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"), default=dict)
    parse_meta: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"), default=dict)


class DocumentChunk(IdMixin, Base):
    __tablename__ = "document_chunks"
    __table_args__ = (Index("ix_document_chunks_document", "document_id", "kind", "ord"),)

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    space_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_spaces.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("document_chunks.id", ondelete="CASCADE"))
    kind: Mapped[ChunkKind] = mapped_column(str_enum(ChunkKind, "chunk_kind"))
    ord: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    section_path: Mapped[list[str]] = mapped_column(ARRAY(String(300)), default=list)
    page_from: Mapped[int | None] = mapped_column(Integer)
    page_to: Mapped[int | None] = mapped_column(Integer)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
