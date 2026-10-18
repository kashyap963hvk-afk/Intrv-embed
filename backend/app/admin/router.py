"""Owner administration endpoints (/api/admin). Every route requires an active owner."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status

from app.admin import service
from app.admin.schemas import (
    AdminUserOut,
    AuditPage,
    GroupIn,
    GroupOut,
    InviteCreated,
    InviteIn,
    InviteOut,
    OrgSettingsOut,
    OrgUpdate,
    PolicyUpdate,
    RoleUpdate,
)
from app.audit.service import ClientMeta
from app.auth.deps import Owner, SettingsDep, csrf_protect, require_owner
from app.auth.router import MailerDep
from app.db.deps import DbSession
from app.db.models import UserStatus
from app.knowledge.admin_router import purge_personal_content
from app.knowledge.deps import ResourcesDep, bump_acl_after_mutation
from app.orgs.policy import OrgPolicy, org_policy

router = APIRouter(
    prefix="/api/admin",
    tags=["admin"],
    dependencies=[Depends(csrf_protect), Depends(require_owner), Depends(bump_acl_after_mutation)],
)


def _meta(request: Request) -> ClientMeta:
    return ClientMeta.from_request(request)


Meta = Annotated[ClientMeta, Depends(_meta)]


# --- Members ---------------------------------------------------------------------------------------


@router.get("/users", response_model=list[AdminUserOut])
async def list_users(
    db: DbSession,
    _owner: Owner,
    status_filter: Annotated[UserStatus | None, Query(alias="status")] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> list[AdminUserOut]:
    return await service.list_users(db, status=status_filter, query=q)


@router.get("/users/{user_id}", response_model=AdminUserOut)
async def get_user(user_id: uuid.UUID, db: DbSession, _owner: Owner) -> AdminUserOut:
    return await service.get_user(db, user_id)


@router.post("/users/{user_id}/approve", response_model=AdminUserOut)
async def approve_user(
    user_id: uuid.UUID, db: DbSession, owner: Owner, settings: SettingsDep, mailer: MailerDep, meta: Meta
) -> AdminUserOut:
    return await service.approve_user(db, settings, mailer, owner, user_id, meta)


@router.post("/users/{user_id}/reject", status_code=status.HTTP_204_NO_CONTENT)
async def reject_user(user_id: uuid.UUID, db: DbSession, owner: Owner, meta: Meta) -> None:
    await service.reject_user(db, owner, user_id, meta)


@router.post("/users/{user_id}/suspend", response_model=AdminUserOut)
async def suspend_user(user_id: uuid.UUID, db: DbSession, owner: Owner, meta: Meta) -> AdminUserOut:
    return await service.suspend_user(db, owner, user_id, meta)


@router.post("/users/{user_id}/reactivate", response_model=AdminUserOut)
async def reactivate_user(user_id: uuid.UUID, db: DbSession, owner: Owner, meta: Meta) -> AdminUserOut:
    return await service.reactivate_user(db, owner, user_id, meta)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: uuid.UUID, db: DbSession, owner: Owner, meta: Meta, resources: ResourcesDep) -> None:
    await service.delete_user(db, owner, user_id, meta)
    await purge_personal_content(resources, db, owner.org_id, user_id)


@router.put("/users/{user_id}/role", response_model=AdminUserOut)
async def set_role(user_id: uuid.UUID, body: RoleUpdate, db: DbSession, owner: Owner, meta: Meta) -> AdminUserOut:
    return await service.set_role(db, owner, user_id, body.role, meta)


@router.patch("/users/{user_id}/policy", response_model=AdminUserOut)
async def update_policy(
    user_id: uuid.UUID, body: PolicyUpdate, db: DbSession, owner: Owner, meta: Meta
) -> AdminUserOut:
    return await service.update_policy(db, owner, user_id, body, meta)


# --- Groups -----------------------------------------------------------------------------------------


@router.get("/groups", response_model=list[GroupOut])
async def list_groups(db: DbSession, _owner: Owner) -> list[GroupOut]:
    return await service.list_groups(db)


@router.post("/groups", response_model=GroupOut, status_code=status.HTTP_201_CREATED)
async def create_group(body: GroupIn, db: DbSession, owner: Owner, meta: Meta) -> GroupOut:
    return await service.create_group(db, owner, body, meta)


@router.put("/groups/{group_id}", response_model=GroupOut)
async def update_group(group_id: uuid.UUID, body: GroupIn, db: DbSession, owner: Owner, meta: Meta) -> GroupOut:
    return await service.update_group(db, owner, group_id, body, meta)


@router.delete("/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(group_id: uuid.UUID, db: DbSession, owner: Owner, meta: Meta) -> None:
    await service.delete_group(db, owner, group_id, meta)


@router.put("/groups/{group_id}/members/{user_id}", response_model=GroupOut)
async def add_group_member(
    group_id: uuid.UUID, user_id: uuid.UUID, db: DbSession, owner: Owner, meta: Meta
) -> GroupOut:
    return await service.add_group_member(db, owner, group_id, user_id, meta)


@router.delete("/groups/{group_id}/members/{user_id}", response_model=GroupOut)
async def remove_group_member(
    group_id: uuid.UUID, user_id: uuid.UUID, db: DbSession, owner: Owner, meta: Meta
) -> GroupOut:
    return await service.remove_group_member(db, owner, group_id, user_id, meta)


# --- Invites ---------------------------------------------------------------------------------------


@router.get("/invites", response_model=list[InviteOut])
async def list_invites(db: DbSession, _owner: Owner) -> list[InviteOut]:
    return [InviteOut.model_validate(i) for i in await service.list_invites(db)]


@router.post("/invites", response_model=InviteCreated, status_code=status.HTTP_201_CREATED)
async def create_invite(
    body: InviteIn, db: DbSession, owner: Owner, settings: SettingsDep, mailer: MailerDep, meta: Meta
) -> InviteCreated:
    created = await service.invite_user(db, settings, mailer, owner, email=body.email, role=body.role, meta=meta)
    return InviteCreated(
        invite=InviteOut.model_validate(created.invite), link=created.link, email_sent=created.email_sent
    )


@router.delete("/invites/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invite(invite_id: uuid.UUID, db: DbSession, owner: Owner, meta: Meta) -> None:
    await service.revoke_invite(db, owner, invite_id, meta)


# --- Audit & org -------------------------------------------------------------------------------------


@router.get("/audit", response_model=AuditPage)
async def list_audit(
    db: DbSession,
    _owner: Owner,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    action: Annotated[str | None, Query(max_length=80)] = None,
    actor_id: uuid.UUID | None = None,
    target_id: Annotated[str | None, Query(max_length=64)] = None,
) -> AuditPage:
    return await service.list_audit(
        db, limit=limit, cursor=cursor, action_prefix=action, actor_id=actor_id, target_id=target_id
    )


@router.get("/org", response_model=OrgSettingsOut)
async def get_org(db: DbSession, owner: Owner) -> OrgSettingsOut:
    return OrgSettingsOut.model_validate(await service.get_org(db, owner.org_id))


@router.patch("/org", response_model=OrgSettingsOut)
async def update_org(body: OrgUpdate, db: DbSession, owner: Owner, meta: Meta) -> OrgSettingsOut:
    return OrgSettingsOut.model_validate(await service.rename_org(db, owner, body.name, meta))


@router.get("/org-policy", response_model=OrgPolicy)
async def get_org_policy(db: DbSession, owner: Owner) -> OrgPolicy:
    return org_policy(await service.get_org(db, owner.org_id))


@router.put("/org-policy", response_model=OrgPolicy)
async def put_org_policy(body: OrgPolicy, db: DbSession, owner: Owner, meta: Meta) -> OrgPolicy:
    return await service.update_org_policy(db, owner, body, meta)
