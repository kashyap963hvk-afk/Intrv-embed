"""Owner endpoints for organization knowledge spaces and their access grants (/api/admin/spaces)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import delete, func, select

from app.audit.service import ClientMeta, record
from app.auth.deps import Owner, csrf_protect, require_owner
from app.core.errors import AppError, NotFoundError
from app.db.deps import DbSession, after_commit
from app.db.models import Document, Group, KnowledgeSpace, PrincipalType, SpaceGrant, SpaceKind, User
from app.knowledge.deps import ResourcesDep, bump_acl_after_mutation
from app.knowledge.schemas import GrantIn, GrantOut, OrgSpaceOut, SpaceIn
from app.knowledge.storage import space_prefix

router = APIRouter(
    prefix="/api/admin/spaces",
    tags=["admin"],
    dependencies=[Depends(csrf_protect), Depends(require_owner), Depends(bump_acl_after_mutation)],
)


def _meta(request: Request) -> ClientMeta:
    return ClientMeta.from_request(request)


Meta = Annotated[ClientMeta, Depends(_meta)]


async def _org_space(db: DbSession, space_id: uuid.UUID) -> KnowledgeSpace:
    space = await db.get(KnowledgeSpace, space_id)
    if space is None or space.kind != SpaceKind.ORG:
        raise NotFoundError("Organization space not found.")
    return space


async def _out(db: DbSession, spaces: list[KnowledgeSpace]) -> list[OrgSpaceOut]:
    ids = [s.id for s in spaces]
    grants = list(await db.scalars(select(SpaceGrant).where(SpaceGrant.space_id.in_(ids))))
    counts = dict(
        (
            await db.execute(
                select(Document.space_id, func.count()).where(Document.space_id.in_(ids)).group_by(Document.space_id)
            )
        ).all()
    )
    return [
        OrgSpaceOut(
            id=s.id,
            name=s.name,
            description=s.description,
            document_count=int(counts.get(s.id, 0)),
            grants=[GrantOut.model_validate(g) for g in grants if g.space_id == s.id],
        )
        for s in spaces
    ]


@router.get("", response_model=list[OrgSpaceOut])
async def list_org_spaces(db: DbSession, _owner: Owner) -> list[OrgSpaceOut]:
    spaces = list(
        await db.scalars(
            select(KnowledgeSpace).where(KnowledgeSpace.kind == SpaceKind.ORG).order_by(KnowledgeSpace.name)
        )
    )
    return await _out(db, spaces)


@router.post("", response_model=OrgSpaceOut, status_code=status.HTTP_201_CREATED)
async def create_org_space(body: SpaceIn, db: DbSession, owner: Owner, meta: Meta) -> OrgSpaceOut:
    space = KnowledgeSpace(
        org_id=owner.org_id, kind=SpaceKind.ORG, name=body.name.strip(), description=body.description
    )
    db.add(space)
    await db.flush()
    await record(
        db,
        org_id=owner.org_id,
        actor_id=owner.user_id,
        action="space.created",
        meta=meta,
        target_type="space",
        target_id=space.id,
    )
    return (await _out(db, [space]))[0]


@router.patch("/{space_id}", response_model=OrgSpaceOut)
async def update_org_space(space_id: uuid.UUID, body: SpaceIn, db: DbSession, owner: Owner, meta: Meta) -> OrgSpaceOut:
    space = await _org_space(db, space_id)
    space.name = body.name.strip()
    space.description = body.description
    await record(
        db,
        org_id=owner.org_id,
        actor_id=owner.user_id,
        action="space.updated",
        meta=meta,
        target_type="space",
        target_id=space.id,
    )
    await db.flush()
    return (await _out(db, [space]))[0]


@router.delete("/{space_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_org_space(
    space_id: uuid.UUID, db: DbSession, owner: Owner, meta: Meta, resources: ResourcesDep
) -> None:
    space = await _org_space(db, space_id)
    store = await resources.rag.store()
    await store.delete_space(owner.org_id, space.id)
    await resources.storage.delete_prefix(space_prefix(owner.org_id, space.id))
    await record(
        db,
        org_id=owner.org_id,
        actor_id=owner.user_id,
        action="space.deleted",
        meta=meta,
        target_type="space",
        target_id=space.id,
        detail={"name": space.name},
    )
    await db.delete(space)


@router.put("/{space_id}/grants", response_model=OrgSpaceOut, summary="Replace who can read or contribute")
async def set_grants(space_id: uuid.UUID, body: list[GrantIn], db: DbSession, owner: Owner, meta: Meta) -> OrgSpaceOut:
    space = await _org_space(db, space_id)
    seen: set[tuple[PrincipalType, uuid.UUID]] = set()
    for grant in body:
        key = (grant.principal_type, grant.principal_id)
        if key in seen:
            raise AppError("Each user or group may appear only once.")
        seen.add(key)
        model = User if grant.principal_type == PrincipalType.USER else Group
        if await db.get(model, grant.principal_id) is None:  # RLS: must be in this organization
            raise NotFoundError(f"{grant.principal_type.value.lower()} {grant.principal_id} not found.")
    await db.execute(delete(SpaceGrant).where(SpaceGrant.space_id == space.id))
    for grant in body:
        db.add(
            SpaceGrant(
                org_id=owner.org_id,
                space_id=space.id,
                principal_type=grant.principal_type,
                principal_id=grant.principal_id,
                permission=grant.permission,
                granted_by=owner.user_id,
                expires_at=grant.expires_at,
            )
        )
    await record(
        db,
        org_id=owner.org_id,
        actor_id=owner.user_id,
        action="space.grants_changed",
        meta=meta,
        target_type="space",
        target_id=space.id,
        detail={"grants": [g.model_dump(mode="json") for g in body]},
    )
    await db.flush()
    return (await _out(db, [space]))[0]


async def purge_personal_content(resources: object, db: DbSession, org_id: uuid.UUID, user_id: uuid.UUID) -> None:
    """Before a member is deleted, remove the vectors and files of their private spaces (which owners cannot
    read through RLS). Space ids are collected as that member; deletion runs after the commit succeeds."""
    from app.core.resources import Resources  # noqa: PLC0415
    from app.db.tenant import TenantContext, set_tenant_context  # noqa: PLC0415

    if not isinstance(resources, Resources):
        raise TypeError("resources")
    async with resources.session_factory() as member_db:
        await set_tenant_context(member_db, TenantContext(org_id=org_id, user_id=user_id, role="MEMBER"))
        space_ids = list(
            await member_db.scalars(
                select(KnowledgeSpace.id).where(
                    KnowledgeSpace.kind == SpaceKind.PERSONAL, KnowledgeSpace.owner_user_id == user_id
                )
            )
        )

    async def purge() -> None:
        store = await resources.rag.store()
        for space_id in space_ids:
            await store.delete_space(org_id, space_id)
            await resources.storage.delete_prefix(space_prefix(org_id, space_id))

    after_commit(db, purge)
