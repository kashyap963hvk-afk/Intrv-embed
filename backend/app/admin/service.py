"""Owner administration: member lifecycle, policies, groups, invites, audit log.

Every function runs inside the owner's tenant context, so RLS confines it to their organization.
"""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.schemas import AdminUserOut, AuditOut, AuditPage, GroupIn, GroupOut, PolicyUpdate
from app.audit.service import ClientMeta, record
from app.auth.principal import Principal
from app.auth.schemas import PolicyOut
from app.auth.service import revoke_families
from app.core.config import Settings
from app.core.email import EmailSender, OutgoingEmail
from app.core.errors import AppError, ConflictError, NotFoundError
from app.db.models import AuditLog, Group, GroupMember, Organization, Role, User, UserInvite, UserPolicy, UserStatus
from app.orgs.policy import OrgPolicy, org_policy, with_policy
from app.orgs.service import CreatedInvite, create_invite


class InvalidTransitionError(AppError):
    status_code = 409
    slug = "invalid-transition"
    title = "That change is not allowed in the user's current state"


async def _user(db: AsyncSession, user_id: uuid.UUID) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise NotFoundError("User not found.")
    return user


async def _group_ids_by_user(db: AsyncSession, user_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[uuid.UUID]]:
    rows = await db.execute(select(GroupMember.user_id, GroupMember.group_id).where(GroupMember.user_id.in_(user_ids)))
    result: dict[uuid.UUID, list[uuid.UUID]] = {}
    for user_id, group_id in rows:
        result.setdefault(user_id, []).append(group_id)
    return result


def _admin_user(user: User, groups: list[uuid.UUID]) -> AdminUserOut:
    return AdminUserOut.model_validate(
        {
            **{f: getattr(user, f) for f in ("id", "email", "name", "role", "status", "created_at", "last_login_at")},
            "policy": PolicyOut.model_validate(user.policy),
            "group_ids": groups,
        }
    )


async def _ensure_not_last_owner(db: AsyncSession, user: User) -> None:
    if user.role != Role.OWNER or user.status != UserStatus.ACTIVE:
        return
    owners = await db.scalar(
        select(func.count()).select_from(User).where(User.role == Role.OWNER, User.status == UserStatus.ACTIVE)
    )
    if (owners or 0) <= 1:
        raise InvalidTransitionError("The organization must keep at least one active owner.")


# --- Members ------------------------------------------------------------------------------------


async def list_users(db: AsyncSession, *, status: UserStatus | None, query: str | None) -> list[AdminUserOut]:
    stmt = select(User).order_by(User.created_at.desc())
    if status is not None:
        stmt = stmt.where(User.status == status)
    if query:
        pattern = f"%{query.strip().lower()}%"
        stmt = stmt.where(func.lower(User.email).like(pattern) | func.lower(User.name).like(pattern))
    users = list((await db.scalars(stmt)).unique())
    groups = await _group_ids_by_user(db, [u.id for u in users])
    return [_admin_user(u, groups.get(u.id, [])) for u in users]


async def get_user(db: AsyncSession, user_id: uuid.UUID) -> AdminUserOut:
    user = await _user(db, user_id)
    groups = await _group_ids_by_user(db, [user.id])
    return _admin_user(user, groups.get(user.id, []))


async def approve_user(
    db: AsyncSession, settings: Settings, mailer: EmailSender, owner: Principal, user_id: uuid.UUID, meta: ClientMeta
) -> AdminUserOut:
    user = await _user(db, user_id)
    if user.status != UserStatus.PENDING:
        raise InvalidTransitionError("Only pending users can be approved.")
    user.status = UserStatus.ACTIVE
    await record(
        db,
        org_id=owner.org_id,
        actor_id=owner.user_id,
        action="user.approved",
        meta=meta,
        target_type="user",
        target_id=user.id,
    )
    await db.flush()
    await mailer.send(
        OutgoingEmail(
            to=user.email,
            subject="Your Intrv-embed account was approved",
            body=f"Hi {user.name},\n\nYour account was approved. Sign in at {settings.public_app_url}/login\n",
        )
    )
    return await get_user(db, user.id)


async def reject_user(db: AsyncSession, owner: Principal, user_id: uuid.UUID, meta: ClientMeta) -> None:
    user = await _user(db, user_id)
    if user.status != UserStatus.PENDING:
        raise InvalidTransitionError("Only pending users can be rejected; suspend or delete active users instead.")
    await record(
        db,
        org_id=owner.org_id,
        actor_id=owner.user_id,
        action="user.rejected",
        meta=meta,
        target_type="user",
        target_id=user.id,
    )
    await db.delete(user)


async def suspend_user(db: AsyncSession, owner: Principal, user_id: uuid.UUID, meta: ClientMeta) -> AdminUserOut:
    user = await _user(db, user_id)
    if user.id == owner.user_id:
        raise InvalidTransitionError("You cannot suspend yourself.")
    if user.status != UserStatus.ACTIVE:
        raise InvalidTransitionError("Only active users can be suspended.")
    await _ensure_not_last_owner(db, user)
    user.status = UserStatus.SUSPENDED
    await revoke_families(db, user_id=user.id)
    await record(
        db,
        org_id=owner.org_id,
        actor_id=owner.user_id,
        action="user.suspended",
        meta=meta,
        target_type="user",
        target_id=user.id,
    )
    await db.flush()
    return await get_user(db, user.id)


async def reactivate_user(db: AsyncSession, owner: Principal, user_id: uuid.UUID, meta: ClientMeta) -> AdminUserOut:
    user = await _user(db, user_id)
    if user.status != UserStatus.SUSPENDED:
        raise InvalidTransitionError("Only suspended users can be reactivated.")
    user.status = UserStatus.ACTIVE
    await record(
        db,
        org_id=owner.org_id,
        actor_id=owner.user_id,
        action="user.reactivated",
        meta=meta,
        target_type="user",
        target_id=user.id,
    )
    await db.flush()
    return await get_user(db, user.id)


async def delete_user(db: AsyncSession, owner: Principal, user_id: uuid.UUID, meta: ClientMeta) -> None:
    user = await _user(db, user_id)
    if user.id == owner.user_id:
        raise InvalidTransitionError("You cannot delete yourself.")
    await _ensure_not_last_owner(db, user)
    await record(
        db,
        org_id=owner.org_id,
        actor_id=owner.user_id,
        action="user.deleted",
        meta=meta,
        target_type="user",
        target_id=user.id,
        detail={"role": user.role.value, "status": user.status.value},
    )
    await db.delete(user)


async def set_role(
    db: AsyncSession, owner: Principal, user_id: uuid.UUID, role: Role, meta: ClientMeta
) -> AdminUserOut:
    user = await _user(db, user_id)
    if user.role == role:
        return await get_user(db, user.id)
    if user.role == Role.OWNER:
        await _ensure_not_last_owner(db, user)
    previous = user.role
    user.role = role
    await record(
        db,
        org_id=owner.org_id,
        actor_id=owner.user_id,
        action="user.role_changed",
        meta=meta,
        target_type="user",
        target_id=user.id,
        detail={"from": previous.value, "to": role.value},
    )
    await db.flush()
    return await get_user(db, user.id)


async def update_policy(
    db: AsyncSession, owner: Principal, user_id: uuid.UUID, body: PolicyUpdate, meta: ClientMeta
) -> AdminUserOut:
    user = await _user(db, user_id)
    policy = user.policy or UserPolicy(user_id=user.id, org_id=user.org_id)
    changes: dict[str, object] = {}
    for field in ("can_edit_prompts", "can_create_llm_profiles", "can_use_voice", "can_use_coding"):
        value = getattr(body, field)
        if value is not None and getattr(policy, field) != value:
            setattr(policy, field, value)
            changes[field] = value
    if body.clear_interview_quota:
        policy.monthly_interview_quota = None
        changes["monthly_interview_quota"] = None
    elif body.monthly_interview_quota is not None:
        policy.monthly_interview_quota = body.monthly_interview_quota
        changes["monthly_interview_quota"] = body.monthly_interview_quota
    if body.clear_allowed_llm_profiles:
        policy.allowed_llm_profile_ids = None
        changes["allowed_llm_profile_ids"] = None
    elif body.allowed_llm_profile_ids is not None:
        policy.allowed_llm_profile_ids = body.allowed_llm_profile_ids
        changes["allowed_llm_profile_ids"] = [str(i) for i in body.allowed_llm_profile_ids]
    db.add(policy)
    if changes:
        await record(
            db,
            org_id=owner.org_id,
            actor_id=owner.user_id,
            action="user.policy_changed",
            meta=meta,
            target_type="user",
            target_id=user.id,
            detail=changes,
        )
    await db.flush()
    await db.refresh(user)
    return await get_user(db, user.id)


# --- Groups ---------------------------------------------------------------------------------------


async def _group_out(db: AsyncSession, group: Group) -> GroupOut:
    members = await db.scalars(select(GroupMember.user_id).where(GroupMember.group_id == group.id))
    return GroupOut(
        id=group.id,
        name=group.name,
        description=group.description,
        member_ids=list(members),
        created_at=group.created_at,
    )


async def list_groups(db: AsyncSession) -> list[GroupOut]:
    groups = list(await db.scalars(select(Group).order_by(Group.name)))
    return [await _group_out(db, g) for g in groups]


async def create_group(db: AsyncSession, owner: Principal, body: GroupIn, meta: ClientMeta) -> GroupOut:
    group = Group(org_id=owner.org_id, name=body.name.strip(), description=body.description)
    db.add(group)
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError as exc:
        raise ConflictError("A group with this name already exists.") from exc
    await record(
        db,
        org_id=owner.org_id,
        actor_id=owner.user_id,
        action="group.created",
        meta=meta,
        target_type="group",
        target_id=group.id,
    )
    return await _group_out(db, group)


async def _group(db: AsyncSession, group_id: uuid.UUID) -> Group:
    group = await db.get(Group, group_id)
    if group is None:
        raise NotFoundError("Group not found.")
    return group


async def update_group(
    db: AsyncSession, owner: Principal, group_id: uuid.UUID, body: GroupIn, meta: ClientMeta
) -> GroupOut:
    group = await _group(db, group_id)
    group.name = body.name.strip()
    group.description = body.description
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError as exc:
        raise ConflictError("A group with this name already exists.") from exc
    await record(
        db,
        org_id=owner.org_id,
        actor_id=owner.user_id,
        action="group.updated",
        meta=meta,
        target_type="group",
        target_id=group.id,
    )
    return await _group_out(db, group)


async def delete_group(db: AsyncSession, owner: Principal, group_id: uuid.UUID, meta: ClientMeta) -> None:
    group = await _group(db, group_id)
    await record(
        db,
        org_id=owner.org_id,
        actor_id=owner.user_id,
        action="group.deleted",
        meta=meta,
        target_type="group",
        target_id=group.id,
    )
    await db.delete(group)


async def add_group_member(
    db: AsyncSession, owner: Principal, group_id: uuid.UUID, user_id: uuid.UUID, meta: ClientMeta
) -> GroupOut:
    group = await _group(db, group_id)
    await _user(db, user_id)  # RLS: the user must belong to this org
    if await db.get(GroupMember, (group.id, user_id)) is None:
        db.add(GroupMember(group_id=group.id, user_id=user_id, org_id=owner.org_id))
        await record(
            db,
            org_id=owner.org_id,
            actor_id=owner.user_id,
            action="group.member_added",
            meta=meta,
            target_type="group",
            target_id=group.id,
            detail={"user_id": str(user_id)},
        )
        await db.flush()
    return await _group_out(db, group)


async def remove_group_member(
    db: AsyncSession, owner: Principal, group_id: uuid.UUID, user_id: uuid.UUID, meta: ClientMeta
) -> GroupOut:
    group = await _group(db, group_id)
    result = await db.execute(
        delete(GroupMember).where(GroupMember.group_id == group.id, GroupMember.user_id == user_id)
    )
    if result.rowcount:  # type: ignore[attr-defined]
        await record(
            db,
            org_id=owner.org_id,
            actor_id=owner.user_id,
            action="group.member_removed",
            meta=meta,
            target_type="group",
            target_id=group.id,
            detail={"user_id": str(user_id)},
        )
    return await _group_out(db, group)


# --- Invites ---------------------------------------------------------------------------------------


async def invite_user(
    db: AsyncSession,
    settings: Settings,
    mailer: EmailSender,
    owner: Principal,
    *,
    email: str,
    role: Role,
    meta: ClientMeta,
) -> CreatedInvite:
    existing = await db.scalar(select(User).where(User.email == email))
    if existing is not None and existing.status != UserStatus.PENDING:
        raise ConflictError("A member with this email already exists.")
    org = await db.get(Organization, owner.org_id)
    if org is None:
        raise NotFoundError("Organization not found.")
    return await create_invite(
        db, settings, mailer, org=org, email=email, role=role, invited_by=owner.user_id, meta=meta
    )


async def list_invites(db: AsyncSession) -> list[UserInvite]:
    now = datetime.now(UTC)
    stmt = (
        select(UserInvite)
        .where(UserInvite.accepted_at.is_(None), UserInvite.revoked_at.is_(None), UserInvite.expires_at > now)
        .order_by(UserInvite.created_at.desc())
    )
    return list(await db.scalars(stmt))


async def revoke_invite(db: AsyncSession, owner: Principal, invite_id: uuid.UUID, meta: ClientMeta) -> None:
    invite = await db.get(UserInvite, invite_id)
    if invite is None or invite.revoked_at is not None or invite.accepted_at is not None:
        raise NotFoundError("Invitation not found or no longer pending.")
    invite.revoked_at = datetime.now(UTC)
    await record(
        db,
        org_id=owner.org_id,
        actor_id=owner.user_id,
        action="invite.revoked",
        meta=meta,
        target_type="invite",
        target_id=invite.id,
    )


# --- Audit -------------------------------------------------------------------------------------------


def _encode_cursor(at: datetime, row_id: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(f"{at.isoformat()}|{row_id}".encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        at_raw, id_raw = base64.urlsafe_b64decode(cursor.encode()).decode().split("|", 1)
        return datetime.fromisoformat(at_raw), uuid.UUID(id_raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise AppError("Invalid cursor.") from exc


async def list_audit(
    db: AsyncSession,
    *,
    limit: int,
    cursor: str | None,
    action_prefix: str | None,
    actor_id: uuid.UUID | None,
    target_id: str | None,
) -> AuditPage:
    stmt = select(AuditLog).order_by(AuditLog.at.desc(), AuditLog.id.desc()).limit(limit + 1)
    if action_prefix:
        stmt = stmt.where(AuditLog.action.startswith(action_prefix, autoescape=True))
    if actor_id:
        stmt = stmt.where(AuditLog.actor_id == actor_id)
    if target_id:
        stmt = stmt.where(AuditLog.target_id == target_id)
    if cursor:
        at, row_id = _decode_cursor(cursor)
        stmt = stmt.where(tuple_(AuditLog.at, AuditLog.id) < tuple_(at, row_id))
    rows = list(await db.scalars(stmt))
    next_cursor = _encode_cursor(rows[limit - 1].at, rows[limit - 1].id) if len(rows) > limit else None
    return AuditPage(items=[AuditOut.model_validate(r) for r in rows[:limit]], next_cursor=next_cursor)


# --- Organization ----------------------------------------------------------------------------------------


async def get_org(db: AsyncSession, org_id: uuid.UUID) -> Organization:
    org = await db.get(Organization, org_id)
    if org is None:
        raise NotFoundError("Organization not found.")
    return org


async def rename_org(db: AsyncSession, owner: Principal, name: str, meta: ClientMeta) -> Organization:
    org = await get_org(db, owner.org_id)
    previous = org.name
    org.name = name.strip()
    await record(
        db,
        org_id=owner.org_id,
        actor_id=owner.user_id,
        action="org.renamed",
        meta=meta,
        target_type="org",
        target_id=org.id,
        detail={"from": previous, "to": org.name},
    )
    await db.flush()
    return org


async def update_org_policy(db: AsyncSession, owner: Principal, policy: OrgPolicy, meta: ClientMeta) -> OrgPolicy:
    org = await get_org(db, owner.org_id)
    previous = org_policy(org)
    org.settings = with_policy(org, policy)
    await record(
        db,
        org_id=owner.org_id,
        actor_id=owner.user_id,
        action="org.policy_changed",
        meta=meta,
        target_type="org",
        target_id=org.id,
        detail={"from": previous.model_dump(mode="json"), "to": policy.model_dump(mode="json")},
    )
    await db.flush()
    return policy
