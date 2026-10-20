"""Role packs: shipped SYSTEM packs (read-only per org), org-published packs (owners) and members' private packs.
Every edit creates a new version, kept in `role_pack_versions`."""

from __future__ import annotations

import functools
import hashlib
import json
import secrets
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.audit.service import ClientMeta, record
from app.auth.principal import Principal
from app.core.errors import AppError, ForbiddenError, NotFoundError
from app.core.logging import get_logger
from app.db.models import PackVisibility, Role, RolePack, RolePackVersion
from app.db.tenant import TenantContext, set_tenant_context
from app.rolepacks.schema import RolePackSpec, SpecProblem, parse_yaml

log = get_logger("app.rolepacks")


class InvalidPackError(AppError):
    status_code = 422
    slug = "invalid-role-pack"
    title = "The role pack is not valid"

    def __init__(self, problems: list[SpecProblem]) -> None:
        super().__init__(
            "; ".join(f"{p.path}: {p.message}" for p in problems[:5]),
            extra={"problems": [p.model_dump() for p in problems]},
        )


@functools.cache
def system_specs(directory: str) -> dict[str, RolePackSpec]:
    specs: dict[str, RolePackSpec] = {}
    for path in sorted(Path(directory).glob("*.yaml")):
        spec, problems = parse_yaml(path.read_text(encoding="utf-8"))
        if spec is None:
            raise RuntimeError(f"shipped role pack {path.name} is invalid: {problems}")
        specs[spec.slug] = spec
    return specs


def _fingerprint(spec: RolePackSpec) -> str:
    return hashlib.sha256(json.dumps(spec.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()


_seeded: set[tuple[uuid.UUID, str]] = set()


async def ensure_system_packs(
    session_factory: async_sessionmaker[AsyncSession], org_id: uuid.UUID, directory: str
) -> None:
    """Give the org read-only copies of the shipped packs, and refresh them when the shipped file changes.

    Runs in its own session as the platform (owner role, no user): system data is written by the server, never by
    a member. Cached per process, so it costs one query per org after start-up.
    """
    specs = system_specs(directory)
    pending = [s for s in specs.values() if (org_id, _fingerprint(s)) not in _seeded]
    if not pending:
        return
    async with session_factory() as db:
        await set_tenant_context(db, TenantContext(org_id=org_id, role=Role.OWNER.value))
        for spec in pending:
            fingerprint = _fingerprint(spec)
            existing = await db.scalar(
                select(RolePack).where(RolePack.slug == spec.slug, RolePack.visibility == PackVisibility.SYSTEM)
            )
            data = spec.model_dump(mode="json")
            if existing is None:
                pack_id = uuid.uuid4()
                await db.execute(
                    insert(RolePack)
                    .values(
                        id=pack_id,
                        org_id=org_id,
                        owner_user_id=None,
                        visibility=PackVisibility.SYSTEM,
                        slug=spec.slug,
                        name=spec.name,
                        description=spec.description,
                        domain_tags=spec.domain_tags,
                        version=1,
                        spec=data,
                    )
                    .on_conflict_do_nothing()
                )
                db.add(RolePackVersion(org_id=org_id, owner_user_id=None, pack_id=pack_id, version=1, spec=data))
            elif existing.spec != data:
                existing.version += 1
                existing.spec, existing.name = data, spec.name
                existing.description, existing.domain_tags = spec.description, spec.domain_tags
                db.add(
                    RolePackVersion(
                        org_id=org_id, owner_user_id=None, pack_id=existing.id, version=existing.version, spec=data
                    )
                )
                log.info("rolepacks.system_updated", slug=spec.slug, version=existing.version)
            await db.commit()
            _seeded.add((org_id, fingerprint))


def parse_spec(spec: RolePackSpec | None, yaml_text: str | None) -> RolePackSpec:
    if spec is not None:
        return spec
    if yaml_text is None:
        raise AppError("Provide the role pack as `spec` or `yaml`.")
    parsed, problems = parse_yaml(yaml_text)
    if parsed is None:
        raise InvalidPackError(problems)
    return parsed


async def list_packs(db: AsyncSession, *, include_archived: bool = False) -> list[RolePack]:
    query = select(RolePack).order_by(RolePack.visibility, RolePack.name)
    if not include_archived:
        query = query.where(RolePack.archived.is_(False))
    return list(await db.scalars(query))  # RLS: system + org packs, plus the caller's private ones


async def get_pack(db: AsyncSession, pack_id: uuid.UUID) -> RolePack:
    pack = await db.get(RolePack, pack_id)
    if pack is None:
        raise NotFoundError("Role pack not found.")
    return pack


def spec_of(pack: RolePack) -> RolePackSpec:
    return RolePackSpec.model_validate(pack.spec)


def _require_edit(principal: Principal, pack: RolePack) -> None:
    if pack.visibility == PackVisibility.SYSTEM:
        raise ForbiddenError("Built-in role packs are read-only. Fork it to make your own version.")
    if pack.visibility == PackVisibility.ORG and principal.role != Role.OWNER:
        raise ForbiddenError("Only owners can change organization role packs. Fork it to make your own version.")
    if pack.visibility == PackVisibility.PRIVATE and pack.owner_user_id != principal.user_id:
        raise NotFoundError("Role pack not found.")


async def _audit(db: AsyncSession, principal: Principal, action: str, pack: RolePack, meta: ClientMeta) -> None:
    await record(
        db,
        org_id=principal.org_id,
        actor_id=principal.user_id,
        action=action,
        meta=meta,
        target_type="role_pack",
        target_id=pack.id,
        detail={"slug": pack.slug, "version": pack.version, "visibility": pack.visibility.value},
    )


async def create_pack(
    db: AsyncSession,
    principal: Principal,
    spec: RolePackSpec,
    *,
    visibility: PackVisibility,
    meta: ClientMeta,
    forked_from: uuid.UUID | None = None,
) -> RolePack:
    if visibility == PackVisibility.SYSTEM:
        raise ForbiddenError("Built-in role packs are shipped with the product.")
    if visibility == PackVisibility.ORG and principal.role != Role.OWNER:
        raise ForbiddenError("Only owners can publish role packs to the organization.")
    data = spec.model_dump(mode="json")
    pack = RolePack(
        id=uuid.uuid4(),
        org_id=principal.org_id,
        owner_user_id=None if visibility == PackVisibility.ORG else principal.user_id,
        visibility=visibility,
        slug=spec.slug,
        name=spec.name,
        description=spec.description,
        domain_tags=spec.domain_tags,
        version=1,
        spec=data,
        forked_from_id=forked_from,
        created_by=principal.user_id,
    )
    db.add(pack)
    await db.flush()
    db.add(
        RolePackVersion(
            org_id=pack.org_id,
            owner_user_id=pack.owner_user_id,
            pack_id=pack.id,
            version=1,
            spec=data,
            created_by=principal.user_id,
        )
    )
    await _audit(db, principal, "role_pack.created", pack, meta)
    return pack


async def fork_pack(
    db: AsyncSession, principal: Principal, pack: RolePack, name: str | None, meta: ClientMeta
) -> RolePack:
    spec = spec_of(pack)
    suffix = secrets.token_hex(2)
    copy = spec.model_copy(
        update={
            "name": (name or f"{spec.name} (copy)")[:120],
            "slug": f"{spec.slug[:56]}-{suffix}",
            "version": "1.0.0",
        }
    )
    return await create_pack(db, principal, copy, visibility=PackVisibility.PRIVATE, meta=meta, forked_from=pack.id)


async def update_pack(
    db: AsyncSession, principal: Principal, pack: RolePack, spec: RolePackSpec, meta: ClientMeta
) -> RolePack:
    _require_edit(principal, pack)
    data = spec.model_dump(mode="json")
    if data == pack.spec:
        return pack
    pack.version += 1
    pack.spec, pack.slug, pack.name = data, spec.slug, spec.name
    pack.description, pack.domain_tags = spec.description, spec.domain_tags
    db.add(
        RolePackVersion(
            org_id=pack.org_id,
            owner_user_id=pack.owner_user_id,
            pack_id=pack.id,
            version=pack.version,
            spec=data,
            created_by=principal.user_id,
        )
    )
    await db.flush()
    await _audit(db, principal, "role_pack.updated", pack, meta)
    return pack


async def publish_pack(db: AsyncSession, principal: Principal, pack: RolePack, meta: ClientMeta) -> RolePack:
    if principal.role != Role.OWNER:
        raise ForbiddenError("Only owners can publish role packs to the organization.")
    if pack.visibility != PackVisibility.PRIVATE or pack.owner_user_id != principal.user_id:
        raise AppError("Only your own private packs can be published.")
    pack.visibility, pack.owner_user_id = PackVisibility.ORG, None
    for version in await db.scalars(select(RolePackVersion).where(RolePackVersion.pack_id == pack.id)):
        version.owner_user_id = None
    await db.flush()
    await _audit(db, principal, "role_pack.published", pack, meta)
    return pack


async def archive_pack(db: AsyncSession, principal: Principal, pack: RolePack, meta: ClientMeta) -> None:
    _require_edit(principal, pack)
    pack.archived = True
    await _audit(db, principal, "role_pack.archived", pack, meta)


async def versions(db: AsyncSession, pack_id: uuid.UUID) -> list[RolePackVersion]:
    return list(
        await db.scalars(
            select(RolePackVersion).where(RolePackVersion.pack_id == pack_id).order_by(RolePackVersion.version.desc())
        )
    )
