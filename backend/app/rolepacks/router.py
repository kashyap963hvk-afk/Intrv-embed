"""Role packs API: list (shipped + org + mine), read as spec or YAML, validate live, create, edit (versioned),
fork, publish to the org, archive, and the Role Builder agent."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app.audit.service import ClientMeta
from app.auth.deps import ActiveMember, SettingsDep, csrf_protect, per_user_limit
from app.auth.principal import Principal
from app.core.errors import AppError
from app.db.deps import DbSession
from app.db.models import PackVisibility, Role, RolePack
from app.knowledge.deps import ResourcesDep
from app.rolepacks import builder, service
from app.rolepacks.schema import RolePackSpec, SpecProblem, parse_yaml, to_yaml, validate_spec

router = APIRouter(prefix="/api/role-packs", tags=["role packs"], dependencies=[Depends(csrf_protect)])


def _meta(request: Request) -> ClientMeta:
    return ClientMeta.from_request(request)


Meta = Annotated[ClientMeta, Depends(_meta)]


class VariantOut(BaseModel):
    id: str
    name: str


class RolePackOut(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    description: str
    domain_tags: list[str]
    visibility: PackVisibility
    version: int
    is_mine: bool
    editable: bool
    archived: bool
    forked_from_id: uuid.UUID | None
    variants: list[VariantOut]
    competency_count: int
    updated_at: datetime


class RolePackDetail(RolePackOut):
    spec: dict[str, Any]
    yaml: str


class PackIn(BaseModel):
    spec: dict[str, Any] | None = None
    yaml: str | None = Field(default=None, max_length=500_000)


class CreatePackIn(PackIn):
    visibility: Literal["PRIVATE", "ORG"] = "PRIVATE"


class ForkIn(BaseModel):
    name: str | None = Field(default=None, max_length=120)


class ValidationOut(BaseModel):
    valid: bool
    problems: list[SpecProblem]
    spec: dict[str, Any] | None


class VersionOut(BaseModel):
    version: int
    created_at: datetime
    created_by: uuid.UUID | None


class BuildIn(BaseModel):
    description: str = Field(min_length=10, max_length=4000)
    space_ids: list[uuid.UUID] | None = None
    base_pack_id: uuid.UUID | None = None


class BuildOut(BaseModel):
    spec: dict[str, Any]
    valid: bool
    problems: list[SpecProblem]
    yaml: str | None
    diff: builder.PackDiff | None


def _editable(principal: Principal, pack: RolePack) -> bool:
    if pack.visibility == PackVisibility.SYSTEM:
        return False
    if pack.visibility == PackVisibility.ORG:
        return principal.role == Role.OWNER
    return pack.owner_user_id == principal.user_id


def _out(principal: Principal, pack: RolePack) -> RolePackOut:
    spec = pack.spec
    return RolePackOut(
        id=pack.id,
        slug=pack.slug,
        name=pack.name,
        description=pack.description,
        domain_tags=list(pack.domain_tags or []),
        visibility=pack.visibility,
        version=pack.version,
        is_mine=pack.owner_user_id == principal.user_id,
        editable=_editable(principal, pack),
        archived=pack.archived,
        forked_from_id=pack.forked_from_id,
        variants=[VariantOut(id=r["id"], name=r["name"]) for r in spec.get("roles", [])],
        competency_count=len(spec.get("competencies", [])),
        updated_at=pack.updated_at,
    )


def _detail(principal: Principal, pack: RolePack) -> RolePackDetail:
    return RolePackDetail(**_out(principal, pack).model_dump(), spec=pack.spec, yaml=to_yaml(service.spec_of(pack)))


def _spec(body: PackIn) -> RolePackSpec:
    if body.spec is not None:
        spec, problems = validate_spec(body.spec)
        if spec is None:
            raise service.InvalidPackError(problems)
        return spec
    return service.parse_spec(None, body.yaml)


async def _ensure_system(resources: ResourcesDep, settings: SettingsDep, principal: Principal) -> None:
    await service.ensure_system_packs(resources.session_factory, principal.org_id, settings.role_packs_dir)


@router.get("", response_model=list[RolePackOut])
async def list_packs(
    principal: ActiveMember,
    db: DbSession,
    resources: ResourcesDep,
    settings: SettingsDep,
    include_archived: bool = False,
) -> list[RolePackOut]:
    await _ensure_system(resources, settings, principal)
    return [_out(principal, p) for p in await service.list_packs(db, include_archived=include_archived)]


@router.post("/validate", response_model=ValidationOut, summary="Validate a pack without saving (live editor)")
async def validate(body: PackIn, _member: ActiveMember) -> ValidationOut:
    if body.spec is not None:
        spec, problems = validate_spec(body.spec)
    elif body.yaml is not None:
        spec, problems = parse_yaml(body.yaml)
    else:
        raise AppError("Provide `spec` or `yaml`.")
    return ValidationOut(valid=spec is not None, problems=problems, spec=spec.model_dump(mode="json") if spec else None)


@router.post("", response_model=RolePackDetail, status_code=status.HTTP_201_CREATED)
async def create_pack(body: CreatePackIn, principal: ActiveMember, db: DbSession, meta: Meta) -> RolePackDetail:
    pack = await service.create_pack(db, principal, _spec(body), visibility=PackVisibility(body.visibility), meta=meta)
    return _detail(principal, pack)


@router.post(
    "/build",
    response_model=BuildOut,
    summary="Role Builder: draft a pack from a plain-language description",
    dependencies=[per_user_limit("role-builder", limit=20)],
)
async def build(body: BuildIn, principal: ActiveMember, db: DbSession, resources: ResourcesDep) -> BuildOut:
    draft = await builder.build_draft(
        resources.agent_deps,
        db,
        org_id=principal.org_id,
        user_id=principal.user_id,
        description=body.description,
        space_ids=body.space_ids,
    )
    spec = RolePackSpec.model_validate(draft.spec) if draft.valid else None
    diff = None
    if spec is not None and body.base_pack_id is not None:
        diff = builder.diff(service.spec_of(await service.get_pack(db, body.base_pack_id)), spec)
    return BuildOut(
        spec=draft.spec, valid=draft.valid, problems=draft.problems, yaml=to_yaml(spec) if spec else None, diff=diff
    )


@router.get("/{pack_id}", response_model=RolePackDetail)
async def get_pack(pack_id: uuid.UUID, principal: ActiveMember, db: DbSession) -> RolePackDetail:
    return _detail(principal, await service.get_pack(db, pack_id))


@router.get("/{pack_id}/export", response_class=PlainTextResponse, summary="Download the pack as YAML")
async def export_pack(pack_id: uuid.UUID, _member: ActiveMember, db: DbSession) -> PlainTextResponse:
    pack = await service.get_pack(db, pack_id)
    return PlainTextResponse(
        to_yaml(service.spec_of(pack)),
        media_type="application/yaml",
        headers={"Content-Disposition": f'attachment; filename="{pack.slug}-v{pack.version}.yaml"'},
    )


@router.get("/{pack_id}/versions", response_model=list[VersionOut])
async def list_versions(pack_id: uuid.UUID, _member: ActiveMember, db: DbSession) -> list[VersionOut]:
    await service.get_pack(db, pack_id)
    return [
        VersionOut(version=v.version, created_at=v.created_at, created_by=v.created_by)
        for v in await service.versions(db, pack_id)
    ]


@router.put("/{pack_id}", response_model=RolePackDetail)
async def update_pack(
    pack_id: uuid.UUID, body: PackIn, principal: ActiveMember, db: DbSession, meta: Meta
) -> RolePackDetail:
    pack = await service.update_pack(db, principal, await service.get_pack(db, pack_id), _spec(body), meta)
    return _detail(principal, pack)


@router.post("/{pack_id}/fork", response_model=RolePackDetail, status_code=status.HTTP_201_CREATED)
async def fork_pack(
    pack_id: uuid.UUID, body: ForkIn, principal: ActiveMember, db: DbSession, meta: Meta
) -> RolePackDetail:
    pack = await service.fork_pack(db, principal, await service.get_pack(db, pack_id), body.name, meta)
    return _detail(principal, pack)


@router.post("/{pack_id}/publish", response_model=RolePackDetail)
async def publish_pack(pack_id: uuid.UUID, principal: ActiveMember, db: DbSession, meta: Meta) -> RolePackDetail:
    pack = await service.publish_pack(db, principal, await service.get_pack(db, pack_id), meta)
    return _detail(principal, pack)


@router.delete("/{pack_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_pack(pack_id: uuid.UUID, principal: ActiveMember, db: DbSession, meta: Meta) -> None:
    await service.archive_pack(db, principal, await service.get_pack(db, pack_id), meta)
