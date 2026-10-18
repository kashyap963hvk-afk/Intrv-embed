"""API models for knowledge spaces, grants, documents and search."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import DocStatus, DocType, Permission, PrincipalType, SpaceKind

DocTypeChoice = DocType | Literal["AUTO"]


class SpaceOut(BaseModel):
    id: uuid.UUID
    kind: SpaceKind
    name: str
    description: str
    permission: Permission
    document_count: int
    is_mine: bool


class SpaceIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)


class GrantIn(BaseModel):
    principal_type: PrincipalType
    principal_id: uuid.UUID
    permission: Permission = Permission.READ
    expires_at: datetime | None = None


class GrantOut(GrantIn):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    granted_by: uuid.UUID | None
    created_at: datetime


class OrgSpaceOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str
    document_count: int
    grants: list[GrantOut]


class DocumentOut(BaseModel):
    id: uuid.UUID
    space_id: uuid.UUID
    title: str
    filename: str | None
    source_url: str | None
    mime: str | None
    size_bytes: int
    doc_type: DocType
    status: DocStatus
    error: str | None
    page_count: int | None
    chunk_count: int
    risk_flags: dict[str, Any]
    uploaded_by_me: bool
    created_at: datetime
    updated_at: datetime


class RejectedFile(BaseModel):
    filename: str
    reason: str


class UploadResult(BaseModel):
    documents: list[DocumentOut]
    duplicates: list[DocumentOut]
    rejected: list[RejectedFile]


class PasteIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    text: str = Field(min_length=1, max_length=2_000_000)
    doc_type: DocTypeChoice = "AUTO"


class UrlIn(BaseModel):
    url: str = Field(min_length=8, max_length=2000)
    doc_type: DocTypeChoice = "AUTO"
    title: str | None = Field(default=None, max_length=300)


class DownloadOut(BaseModel):
    url: str
    expires_in_s: int


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    space_ids: list[uuid.UUID] | None = None
    doc_types: list[DocType] | None = None
    k: int = Field(default=6, ge=1, le=20)
    rerank: bool = True


class CitationOut(BaseModel):
    document_id: uuid.UUID
    title: str
    section_path: list[str]
    page_from: int | None
    page_to: int | None


class SearchHit(BaseModel):
    text: str
    matched_text: str
    doc_type: DocType
    citation: CitationOut
    fused_rank: int
    rerank_score: float | None


class SearchOut(BaseModel):
    results: list[SearchHit]
    reranked: bool
    searched_spaces: int


class SelfTestCheck(BaseModel):
    name: str
    ok: bool
    detail: str


class SelfTestOut(BaseModel):
    ok: bool
    checks: list[SelfTestCheck]
