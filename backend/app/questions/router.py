"""Question sets API: generate from a CV (+ optional JD) and a role pack, then edit, pin, reorder, regenerate
single questions and export (Markdown, PDF, DOCX)."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.agents.schemas import Category
from app.auth.deps import ActiveMember, csrf_protect
from app.core.errors import AppError
from app.db.deps import DbSession, after_commit
from app.db.models import Document, QuestionItem, QuestionSet, RolePack, SetStatus
from app.knowledge.deps import ResourcesDep
from app.questions import export, service
from app.questions.plan import MAX_COUNT
from app.rolepacks.schema import RolePackSpec

router = APIRouter(prefix="/api/question-sets", tags=["questions"], dependencies=[Depends(csrf_protect)])

Seniority = Literal["JUNIOR", "MID", "SENIOR", "STAFF"]


class CreateSetIn(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    cv_document_id: uuid.UUID
    jd_document_id: uuid.UUID | None = None
    role_pack_id: uuid.UUID
    role_variant: str | None = Field(default=None, max_length=64)
    count: int = Field(default=20, ge=3, le=MAX_COUNT)
    seniority: Seniority | None = None
    difficulty: int | None = Field(default=None, ge=1, le=5, description="Fixed difficulty; default follows seniority")
    mix: dict[Category, float] | None = Field(default=None, description="Category shares, e.g. {'technical': 0.5}")
    space_ids: list[uuid.UUID] | None = Field(default=None, description="Ground answers in these spaces only")


class ItemOut(BaseModel):
    id: uuid.UUID
    ord: int
    pinned: bool
    data: dict[str, Any]


class SetOut(BaseModel):
    id: uuid.UUID
    title: str
    status: SetStatus
    progress: str
    error: str | None
    cv_document_id: uuid.UUID | None
    jd_document_id: uuid.UUID | None
    role_pack_id: uuid.UUID | None
    role_pack_version: int | None
    role_variant: str | None
    params: dict[str, Any]
    item_count: int
    created_at: datetime
    updated_at: datetime


class SetDetail(SetOut):
    items: list[ItemOut]
    analysis: dict[str, Any]
    competency_names: dict[str, str]
    citation_titles: dict[str, str]


class SetUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class ItemUpdate(BaseModel):
    pinned: bool | None = None
    text: str | None = Field(default=None, min_length=10, max_length=1200)
    follow_ups: list[str] | None = Field(default=None, max_length=8)
    expected_signals: list[str] | None = Field(default=None, max_length=20)
    red_flags: list[str] | None = Field(default=None, max_length=20)
    ideal_answer: str | None = Field(default=None, max_length=1500)
    difficulty: int | None = Field(default=None, ge=1, le=5)


class ReorderIn(BaseModel):
    item_ids: list[uuid.UUID] = Field(min_length=1, max_length=MAX_COUNT * 2)


class RegenerateIn(BaseModel):
    hint: str | None = Field(default=None, max_length=500)


def _summary(qset: QuestionSet, count: int) -> SetOut:
    return SetOut(
        id=qset.id,
        title=qset.title,
        status=qset.status,
        progress=qset.progress,
        error=qset.error,
        cv_document_id=qset.cv_document_id,
        jd_document_id=qset.jd_document_id,
        role_pack_id=qset.role_pack_id,
        role_pack_version=qset.role_pack_version,
        role_variant=qset.role_variant,
        params=qset.params,
        item_count=count,
        created_at=qset.created_at,
        updated_at=qset.updated_at,
    )


async def _detail(db: DbSession, qset: QuestionSet) -> SetDetail:
    items = await service.items_of(db, qset.id)
    pack = await db.get(RolePack, qset.role_pack_id) if qset.role_pack_id else None
    names = {c["id"]: c["name"] for c in (pack.spec.get("competencies", []) if pack else [])}
    cited = {uuid.UUID(c) for i in items for c in i.data.get("citations", []) if _is_uuid(c)}
    titles = (
        dict((await db.execute(select(Document.id, Document.title).where(Document.id.in_(cited)))).all())
        if cited
        else {}
    )
    return SetDetail(
        **_summary(qset, len(items)).model_dump(),
        items=[ItemOut(id=i.id, ord=i.ord, pinned=i.pinned, data=i.data) for i in items],
        analysis=qset.analysis or {},
        competency_names=names,
        citation_titles={str(k): v for k, v in titles.items()},
    )


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


@router.get("", response_model=list[SetOut])
async def list_sets(principal: ActiveMember, db: DbSession) -> list[SetOut]:
    sets = list(
        await db.scalars(
            select(QuestionSet)
            .where(QuestionSet.owner_user_id == principal.user_id)
            .order_by(QuestionSet.created_at.desc())
            .limit(200)
        )
    )
    counts = await service.item_counts(db, [s.id for s in sets])
    return [_summary(s, counts.get(s.id, 0)) for s in sets]


@router.post("", response_model=SetDetail, status_code=status.HTTP_202_ACCEPTED, summary="Generate a question set")
async def create_set(body: CreateSetIn, principal: ActiveMember, db: DbSession, resources: ResourcesDep) -> SetDetail:
    params = body.model_dump(mode="json", include={"count", "seniority", "difficulty", "mix", "space_ids"})
    qset = await service.create_set(
        db,
        resources.jobs,
        principal,
        title=body.title,
        cv_document_id=body.cv_document_id,
        jd_document_id=body.jd_document_id,
        role_pack_id=body.role_pack_id,
        role_variant=body.role_variant,
        params=params,
    )
    return await _detail(db, qset)


@router.get("/{set_id}", response_model=SetDetail)
async def get_set(set_id: uuid.UUID, _member: ActiveMember, db: DbSession) -> SetDetail:
    return await _detail(db, await service.get_set(db, set_id))


@router.patch("/{set_id}", response_model=SetDetail)
async def rename_set(set_id: uuid.UUID, body: SetUpdate, _member: ActiveMember, db: DbSession) -> SetDetail:
    qset = await service.get_set(db, set_id)
    qset.title = body.title.strip()
    await db.flush()
    return await _detail(db, qset)


@router.post("/{set_id}/retry", response_model=SetDetail, status_code=status.HTTP_202_ACCEPTED)
async def retry_set(set_id: uuid.UUID, principal: ActiveMember, db: DbSession, resources: ResourcesDep) -> SetDetail:
    qset = await service.get_set(db, set_id)
    if qset.status == SetStatus.GENERATING:
        raise AppError("This set is already being generated.")
    qset.status, qset.error, qset.progress = SetStatus.GENERATING, None, "Queued"
    args = {"org_id": str(principal.org_id), "user_id": str(principal.user_id), "set_id": str(qset.id)}

    async def enqueue() -> None:
        await resources.jobs.submit(service.JOB, args)

    after_commit(db, enqueue)
    await db.flush()
    return await _detail(db, qset)


@router.delete("/{set_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_set(set_id: uuid.UUID, _member: ActiveMember, db: DbSession) -> None:
    await db.delete(await service.get_set(db, set_id))


@router.post("/{set_id}/items/reorder", response_model=SetDetail)
async def reorder_items(set_id: uuid.UUID, body: ReorderIn, _member: ActiveMember, db: DbSession) -> SetDetail:
    qset = await service.get_set(db, set_id)
    await service.reorder(db, qset, body.item_ids)
    await db.flush()
    return await _detail(db, qset)


@router.patch("/{set_id}/items/{item_id}", response_model=ItemOut)
async def update_item(
    set_id: uuid.UUID, item_id: uuid.UUID, body: ItemUpdate, _member: ActiveMember, db: DbSession
) -> ItemOut:
    item = await service.get_item(db, await service.get_set(db, set_id), item_id)
    changes = body.model_dump(exclude_unset=True, exclude={"pinned"})
    if changes:
        item.data = {**item.data, **changes, "edited": True}
    if body.pinned is not None:
        item.pinned = body.pinned
    await db.flush()
    return ItemOut(id=item.id, ord=item.ord, pinned=item.pinned, data=item.data)


@router.delete("/{set_id}/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_item(set_id: uuid.UUID, item_id: uuid.UUID, _member: ActiveMember, db: DbSession) -> None:
    item: QuestionItem = await service.get_item(db, await service.get_set(db, set_id), item_id)
    await db.delete(item)


@router.post("/{set_id}/items/{item_id}/regenerate", response_model=ItemOut)
async def regenerate_item(
    set_id: uuid.UUID,
    item_id: uuid.UUID,
    body: RegenerateIn,
    principal: ActiveMember,
    db: DbSession,
    resources: ResourcesDep,
) -> ItemOut:
    qset = await service.get_set(db, set_id)
    item = await service.regenerate_item(
        resources.agent_deps, db, principal, qset, await service.get_item(db, qset, item_id), body.hint
    )
    return ItemOut(id=item.id, ord=item.ord, pinned=item.pinned, data=item.data)


_FORMATS = {
    "md": ("text/markdown; charset=utf-8", "md"),
    "pdf": ("application/pdf", "pdf"),
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"),
}


@router.get("/{set_id}/export", summary="Download the set (interviewer copy with model answers)")
async def export_set(
    set_id: uuid.UUID,
    _member: ActiveMember,
    db: DbSession,
    format: Annotated[Literal["md", "pdf", "docx"], Query()] = "md",
) -> Response:
    detail = await _detail(db, await service.get_set(db, set_id))
    if detail.status != SetStatus.READY:
        raise AppError("The set is not ready yet.")
    pack = await db.get(RolePack, detail.role_pack_id) if detail.role_pack_id else None
    variant = RolePackSpec.model_validate(pack.spec).role(detail.role_variant).name if pack else ""
    doc = export.ExportSet(
        title=detail.title,
        subtitle=" · ".join(p for p in (pack.name if pack else "", variant, f"{len(detail.items)} questions") if p),
        items=[i.data for i in detail.items],
        competency_names=detail.competency_names,
        citation_titles=detail.citation_titles,
    )
    media, extension = _FORMATS[format]
    body: bytes = (
        export.to_markdown(doc).encode()
        if format == "md"
        else export.to_pdf(doc)
        if format == "pdf"
        else export.to_docx(doc)
    )
    filename = re.sub(r"[^A-Za-z0-9._-]+", "-", detail.title).strip("-")[:80] or "questions"
    return Response(
        body, media_type=media, headers={"Content-Disposition": f'attachment; filename="{filename}.{extension}"'}
    )
