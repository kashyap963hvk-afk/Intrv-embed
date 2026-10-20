"""Privacy API (owners): find a candidate's data, export it (subject access) and erase it (right to erasure)."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel, EmailStr, Field

from app.audit.service import ClientMeta, record
from app.auth.deps import Owner, csrf_protect
from app.auth.principal import Principal
from app.core.errors import AppError
from app.db.deps import DbSession
from app.knowledge.deps import ResourcesDep
from app.privacy import service

router = APIRouter(prefix="/api/privacy", tags=["privacy"], dependencies=[Depends(csrf_protect)])


class SubjectIn(BaseModel):
    email: EmailStr | None = Field(default=None, description="The candidate's email as written on their CV")
    session_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)
    profile_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)
    document_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)


class EraseIn(SubjectIn):
    confirm: bool = Field(default=False, description="Must be true: erasure cannot be undone")


class SubjectItem(BaseModel):
    kind: str
    id: str
    owner_user_id: str
    label: str | None


class EraseOut(BaseModel):
    erased: dict[str, int]
    items: list[SubjectItem]


async def _deps(resources: Any) -> service.PrivacyDeps:
    return service.PrivacyDeps(
        agent=resources.agent_deps,
        session_factory=resources.session_factory,
        storage=resources.storage,
        store=await resources.rag.store(),
    )


async def _find(body: SubjectIn, principal: Principal, db: DbSession, deps: service.PrivacyDeps) -> list[service.Item]:
    if not (body.email or body.session_ids or body.profile_ids or body.document_ids):
        raise AppError("Give the candidate's email or the ids of their interviews, profiles or CV documents.")
    index = await service.candidate_index(db)
    return await service.find_subject(
        deps,
        principal.org_id,
        index,
        email=body.email,
        session_ids=body.session_ids,
        profile_ids=body.profile_ids,
        document_ids=body.document_ids,
    )


def _selector(body: SubjectIn) -> dict[str, Any]:
    """What was asked for, for the audit log (the email only as a hash)."""
    return {
        "email_sha256": hashlib.sha256(body.email.lower().encode()).hexdigest() if body.email else None,
        "sessions": len(body.session_ids),
        "profiles": len(body.profile_ids),
        "documents": len(body.document_ids),
    }


@router.post("/search", response_model=list[SubjectItem], summary="Preview everything held about a candidate")
async def search(body: SubjectIn, principal: Owner, db: DbSession, resources: ResourcesDep) -> list[SubjectItem]:
    items = await _find(body, principal, db, await _deps(resources))
    return [SubjectItem.model_validate(i.out()) for i in items]


@router.post("/export", summary="Subject access export (JSON)")
async def export(
    body: SubjectIn, request: Request, principal: Owner, db: DbSession, resources: ResourcesDep
) -> Response:
    deps = await _deps(resources)
    items = await _find(body, principal, db, deps)
    bundle = await service.export(deps, principal.org_id, items)
    await record(
        db,
        org_id=principal.org_id,
        actor_id=principal.user_id,
        action="privacy.exported",
        meta=ClientMeta.from_request(request),
        target_type="subject",
        detail={**_selector(body), "items": len(items)},
    )
    return Response(
        json.dumps(bundle, indent=1, default=str),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="candidate-data.json"'},
    )


@router.post("/erase", response_model=EraseOut, summary="Erase a candidate's data everywhere (cannot be undone)")
async def erase(body: EraseIn, request: Request, principal: Owner, db: DbSession, resources: ResourcesDep) -> EraseOut:
    if not body.confirm:
        raise AppError("Set confirm to true: erasure cannot be undone.")
    deps = await _deps(resources)
    items = await _find(body, principal, db, deps)
    await db.commit()  # each item is erased in its owner's own transaction
    erased = await service.erase(deps, principal.org_id, items)
    await record(
        db,
        org_id=principal.org_id,
        actor_id=principal.user_id,
        action="privacy.erased",
        meta=ClientMeta.from_request(request),
        target_type="subject",
        detail={**_selector(body), "erased": erased},
    )
    return EraseOut(erased=erased, items=[SubjectItem.model_validate(i.out()) for i in items])
