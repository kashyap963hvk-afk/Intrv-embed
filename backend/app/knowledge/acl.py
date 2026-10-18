"""Who may read or contribute to which knowledge spaces.

The authority is the SECURITY DEFINER function `app_space_permissions()` (personal ownership, owner role on org
spaces, direct and group grants with expiry), the same one RLS uses. Results are cached in Valkey per user for
60 s under a per-org version number; any change to grants, groups, roles or spaces bumps the version, so
revocation applies to the very next query.
"""

from __future__ import annotations

import json
import uuid

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Permission

_TTL_S = 60


def _version_key(org_id: uuid.UUID) -> str:
    return f"acl:ver:{org_id}"


async def bump_acl_version(valkey: Redis, org_id: uuid.UUID) -> None:
    await valkey.incr(_version_key(org_id))


async def acl_version(valkey: Redis, org_id: uuid.UUID) -> int:
    return int(await valkey.get(_version_key(org_id)) or 0)


async def space_permissions(
    db: AsyncSession, valkey: Redis | None, *, org_id: uuid.UUID, user_id: uuid.UUID
) -> dict[uuid.UUID, Permission]:
    """Effective permissions of the session's current user (the tenant context must be set)."""
    cache_key: str | None = None
    if valkey is not None:
        version = await acl_version(valkey, org_id)
        cache_key = f"acl:{org_id}:{version}:{user_id}"
        cached = await valkey.get(cache_key)
        if cached is not None:
            return {uuid.UUID(k): Permission(v) for k, v in json.loads(cached).items()}
    rows = (await db.execute(text("SELECT space_id, permission FROM app_space_permissions()"))).all()
    permissions = {row.space_id: Permission(row.permission) for row in rows}
    if valkey is not None and cache_key is not None:
        await valkey.set(cache_key, json.dumps({str(k): v.value for k, v in permissions.items()}), ex=_TTL_S)
    return permissions


def readable(permissions: dict[uuid.UUID, Permission]) -> set[uuid.UUID]:
    return set(permissions)


def writable(permissions: dict[uuid.UUID, Permission]) -> set[uuid.UUID]:
    return {space for space, perm in permissions.items() if perm == Permission.CONTRIBUTE}
