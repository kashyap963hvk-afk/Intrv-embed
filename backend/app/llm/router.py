"""AI model management (/api/llm): presets, profiles, probing, model discovery, role routing."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import ClientMeta, record
from app.auth.deps import ActiveMember, SettingsDep, ValkeyDep, csrf_protect
from app.auth.principal import Principal
from app.core.config import Settings
from app.core.errors import AppError, ForbiddenError, NotFoundError
from app.core.ratelimit import enforce_rate_limit
from app.core.resources import Resources
from app.core.ssrf import UnsafeURLError, validate_url
from app.db.deps import DbSession
from app.db.models import (
    AgentRole,
    LLMProfile,
    LLMRoleRoute,
    Organization,
    ProfileStatus,
    ProviderKind,
    Role,
    UserPolicy,
)
from app.llm.gateway import CallTrace
from app.llm.presets import get_preset, load_presets
from app.llm.probe import Prober
from app.llm.schemas import (
    DiscoverRequest,
    ModelList,
    PresetOut,
    ProfileCreate,
    ProfileOut,
    ProfileUpdate,
    RoutesOut,
    RoutesUpdate,
    TryRequest,
    TryResponse,
)
from app.llm.service import LLMService, api_key_purpose
from app.llm.types import CallCtx, LLMUnavailableError, ProviderError, ResolvedProfile
from app.orgs.policy import OrgPolicy, org_policy

router = APIRouter(prefix="/api/llm", tags=["llm"], dependencies=[Depends(csrf_protect)])

PROBE_TIMEOUT_S = 180
_MIN_KEY_LENGTH_FOR_HINT = 8  # never reveal the tail of very short keys


class InvalidProfileError(AppError):
    status_code = 422
    slug = "invalid-llm-profile"
    title = "This model configuration is not valid"


def _resources(request: Request) -> Resources:
    resources: Resources = request.app.state.resources
    return resources


ResourcesDep = Annotated[Resources, Depends(_resources)]


def _service(db: DbSession, settings: SettingsDep, resources: ResourcesDep) -> LLMService:
    return LLMService(db, settings=settings, keyring=resources.keyring, gateway=resources.gateway)


ServiceDep = Annotated[LLMService, Depends(_service)]


def _out(profile: LLMProfile, principal: Principal) -> ProfileOut:
    return ProfileOut(
        id=profile.id,
        name=profile.name,
        scope="org" if profile.owner_user_id is None else "personal",
        owned_by_me=profile.owner_user_id == principal.user_id,
        kind=profile.kind,
        preset_key=profile.preset_key,
        base_url=profile.base_url,
        has_api_key=profile.encrypted_api_key is not None,
        api_key_last4=profile.api_key_last4,
        model=profile.model,
        params=profile.params or {},
        capabilities=profile.capabilities or {},
        quality_tier=profile.quality_tier,
        trains_on_data=profile.trains_on_data,
        rpm_limit=profile.rpm_limit,
        rpd_limit=profile.rpd_limit,
        status=profile.status,
        last_probe_at=profile.last_probe_at,
        last_error=profile.last_error,
        created_at=profile.created_at,
    )


async def _policy(db: AsyncSession, principal: Principal) -> OrgPolicy:
    org = await db.get(Organization, principal.org_id)
    if org is None:
        raise NotFoundError("Organization not found.")
    return org_policy(org)


def _check_kind(kind: ProviderKind, settings: Settings, policy: OrgPolicy) -> None:
    if kind == ProviderKind.FAKE:
        if settings.is_prod:
            raise InvalidProfileError("The scripted test provider is only available in development.")
        return
    if kind in (ProviderKind.BRIDGE, ProviderKind.GGUF_MANAGED):
        raise InvalidProfileError("This provider type is not supported by this server version.")
    if kind == ProviderKind.SERVER_LOCAL and not settings.allow_private_llm_endpoints:
        raise InvalidProfileError(
            "Self-hosted endpoints are disabled on this server. In cloud deployments, run local models through "
            "the Local Model Bridge; in self-host mode set ALLOW_PRIVATE_LLM_ENDPOINTS=true."
        )
    if kind not in policy.allowed_provider_kinds:
        raise ForbiddenError("Your organization does not allow this type of provider.")


def _check_url(kind: ProviderKind, base_url: str | None, settings: Settings) -> str | None:
    if kind == ProviderKind.FAKE:
        return None
    if not base_url:
        raise InvalidProfileError("A base URL is required.")
    try:
        return validate_url(
            base_url,
            allow_private=settings.allow_private_llm_endpoints,
            allowed_schemes=("https",) if kind == ProviderKind.OPENAI_COMPATIBLE else ("https", "http"),
        )
    except UnsafeURLError as exc:
        raise InvalidProfileError(exc.detail) from exc


async def _can_create(db: AsyncSession, principal: Principal, scope: str, policy: OrgPolicy) -> None:
    if scope == "org":
        if not principal.is_owner:
            raise ForbiddenError("Only owners can add organization-wide models.")
        return
    if principal.role == Role.OWNER:
        return
    user_policy = await db.get(UserPolicy, principal.user_id)
    if not policy.allow_member_llm_profiles or (user_policy and not user_policy.can_create_llm_profiles):
        raise ForbiddenError("Your organization does not allow you to add your own models.")


async def _store_key(db: AsyncSession, resources: Resources, profile: LLMProfile, raw_key: str) -> None:
    key = raw_key.strip()
    profile.encrypted_api_key = await resources.keyring.encrypt(
        db, profile.org_id, key, purpose=api_key_purpose(profile.id)
    )
    profile.api_key_last4 = key[-4:] if len(key) >= _MIN_KEY_LENGTH_FOR_HINT else None


async def _get(db: AsyncSession, profile_id: uuid.UUID) -> LLMProfile:
    profile = await db.get(LLMProfile, profile_id)  # RLS: only own and org-shared profiles are visible
    if profile is None:
        raise NotFoundError("Model not found.")
    return profile


def _require_write(profile: LLMProfile, principal: Principal) -> None:
    if profile.owner_user_id is None and not principal.is_owner:
        raise ForbiddenError("Only owners can change organization-wide models.")
    if profile.owner_user_id is not None and profile.owner_user_id != principal.user_id:
        raise ForbiddenError("This model belongs to someone else.")


def _ctx(principal: Principal, role: AgentRole = AgentRole.PLANNER) -> CallCtx:
    return CallCtx(org_id=principal.org_id, user_id=principal.user_id, role=role)


# --- presets & profiles ----------------------------------------------------------------------------


@router.get("/presets", response_model=list[PresetOut])
async def presets(_principal: ActiveMember) -> list[PresetOut]:
    return [PresetOut.model_validate(p.model_dump()) for p in load_presets().values()]


@router.get("/profiles", response_model=list[ProfileOut])
async def list_profiles(principal: ActiveMember, db: DbSession) -> list[ProfileOut]:
    profiles = await db.scalars(select(LLMProfile).order_by(LLMProfile.owner_user_id.is_(None).desc(), LLMProfile.name))
    return [_out(p, principal) for p in profiles]


@router.post("/profiles", response_model=ProfileOut, status_code=status.HTTP_201_CREATED)
async def create_profile(
    body: ProfileCreate,
    request: Request,
    principal: ActiveMember,
    db: DbSession,
    settings: SettingsDep,
    resources: ResourcesDep,
) -> ProfileOut:
    policy = await _policy(db, principal)
    await _can_create(db, principal, body.scope, policy)
    preset = get_preset(body.preset_key)
    if body.preset_key and preset is None:
        raise InvalidProfileError("Unknown provider preset.")
    kind = preset.kind if preset else body.kind
    _check_kind(kind, settings, policy)
    if preset and policy.allowed_preset_keys is not None and preset.key not in policy.allowed_preset_keys:
        raise ForbiddenError("Your organization does not allow this provider.")
    base_url = _check_url(kind, body.base_url or (preset.base_url if preset else None), settings)
    if preset and preset.requires_api_key and not body.api_key:
        raise InvalidProfileError(f"{preset.display_name} needs an API key.")
    if body.trains_on_data is not None and not principal.is_owner:
        raise ForbiddenError("Only owners can change a provider's data-training classification.")

    trains = body.trains_on_data if body.trains_on_data is not None else (preset.trains_on_data if preset else None)
    if kind == ProviderKind.FAKE:
        trains = False
    params = body.params.model_dump(exclude_none=True)
    if body.script is not None:
        if kind != ProviderKind.FAKE:
            raise InvalidProfileError("Scripts are only for the test provider.")
        params["script"] = body.script
    profile = LLMProfile(
        id=uuid.uuid4(),
        org_id=principal.org_id,
        owner_user_id=None if body.scope == "org" else principal.user_id,
        name=body.name.strip(),
        kind=kind,
        preset_key=preset.key if preset else None,
        base_url=base_url,
        model=body.model.strip(),
        params=params,
        trains_on_data=trains,
        rpm_limit=body.rpm_limit,
        rpd_limit=body.rpd_limit,
        status=ProfileStatus.UNTESTED,
    )
    if body.api_key:
        await _store_key(db, resources, profile, body.api_key)
    db.add(profile)
    await db.flush()
    await record(
        db,
        org_id=principal.org_id,
        actor_id=principal.user_id,
        action="llm.profile_created",
        meta=ClientMeta.from_request(request),
        target_type="llm_profile",
        target_id=profile.id,
        detail={"scope": body.scope, "kind": kind.value, "preset": profile.preset_key, "model": profile.model},
    )
    await db.refresh(profile)
    return _out(profile, principal)


@router.patch("/profiles/{profile_id}", response_model=ProfileOut)
async def update_profile(
    profile_id: uuid.UUID,
    body: ProfileUpdate,
    request: Request,
    principal: ActiveMember,
    db: DbSession,
    settings: SettingsDep,
    resources: ResourcesDep,
) -> ProfileOut:
    profile = await _get(db, profile_id)
    _require_write(profile, principal)
    changed: list[str] = []
    if body.name is not None:
        profile.name = body.name.strip()
        changed.append("name")
    if body.base_url is not None:
        profile.base_url = _check_url(profile.kind, body.base_url, settings)
        changed.append("base_url")
    if body.model is not None:
        profile.model = body.model.strip()
        changed.append("model")
    if body.params is not None:
        profile.params = {**(profile.params or {}), **body.params.model_dump(exclude_none=True)}
        changed.append("params")
    if body.rpm_limit is not None:
        profile.rpm_limit = body.rpm_limit
        changed.append("rpm_limit")
    if body.rpd_limit is not None:
        profile.rpd_limit = body.rpd_limit
        changed.append("rpd_limit")
    if body.trains_on_data is not None:
        if not principal.is_owner:
            raise ForbiddenError("Only owners can change a provider's data-training classification.")
        profile.trains_on_data = body.trains_on_data
        changed.append("trains_on_data")
    if body.api_key is not None:
        await _store_key(db, resources, profile, body.api_key)
        changed.append("api_key")
    if {"base_url", "model", "api_key"} & set(changed):
        profile.status = ProfileStatus.UNTESTED
    await record(
        db,
        org_id=principal.org_id,
        actor_id=principal.user_id,
        action="llm.profile_updated",
        meta=ClientMeta.from_request(request),
        target_type="llm_profile",
        target_id=profile.id,
        detail={"fields": changed},
    )
    await db.flush()
    await db.refresh(profile)
    return _out(profile, principal)


@router.delete("/profiles/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_profile(profile_id: uuid.UUID, request: Request, principal: ActiveMember, db: DbSession) -> None:
    profile = await _get(db, profile_id)
    _require_write(profile, principal)
    await record(
        db,
        org_id=principal.org_id,
        actor_id=principal.user_id,
        action="llm.profile_deleted",
        meta=ClientMeta.from_request(request),
        target_type="llm_profile",
        target_id=profile.id,
    )
    await db.delete(profile)


# --- probing, discovery, try-it -------------------------------------------------------------------


@router.post("/profiles/{profile_id}/probe", response_model=ProfileOut, summary="Test connection and capabilities")
async def probe_profile(
    profile_id: uuid.UUID,
    request: Request,
    principal: ActiveMember,
    db: DbSession,
    service: ServiceDep,
    resources: ResourcesDep,
) -> ProfileOut:
    profile = await _get(db, profile_id)
    resolved = await service.resolve_profile(profile)
    prober = Prober(resources.provider_factory, _ctx(principal))
    try:
        report = await asyncio.wait_for(prober.run(resolved), timeout=PROBE_TIMEOUT_S)
    except TimeoutError:
        profile.status = ProfileStatus.ERROR
        profile.last_error = f"The test did not finish within {PROBE_TIMEOUT_S}s."
    else:
        profile.capabilities = {
            **report.capabilities,
            "checks": report.checks,
            "quality_score": report.quality_score,
            "warnings": report.warnings,
        }
        profile.quality_tier = report.quality_tier
        profile.status = ProfileStatus.OK if report.ok else ProfileStatus.ERROR
        profile.last_error = report.error
    profile.last_probe_at = datetime.now(UTC)
    await record(
        db,
        org_id=principal.org_id,
        actor_id=principal.user_id,
        action="llm.profile_probed",
        meta=ClientMeta.from_request(request),
        target_type="llm_profile",
        target_id=profile.id,
        detail={"status": profile.status.value, "tier": profile.quality_tier.value if profile.quality_tier else None},
    )
    await db.flush()
    await db.refresh(profile)
    return _out(profile, principal)


async def _list_models(resources: Resources, resolved: ResolvedProfile) -> ModelList:
    try:
        provider = resources.provider_factory(resolved)
        return ModelList(models=await provider.list_models())
    except ProviderError as exc:
        raise InvalidProfileError(f"Could not list models: {exc.message}") from exc


@router.get("/profiles/{profile_id}/models", response_model=ModelList)
async def profile_models(
    profile_id: uuid.UUID, _principal: ActiveMember, db: DbSession, service: ServiceDep, resources: ResourcesDep
) -> ModelList:
    return await _list_models(resources, await service.resolve_profile(await _get(db, profile_id)))


@router.post("/models/discover", response_model=ModelList, summary="List a provider's models before saving")
async def discover_models(
    body: DiscoverRequest, principal: ActiveMember, db: DbSession, settings: SettingsDep, resources: ResourcesDep
) -> ModelList:
    preset = get_preset(body.preset_key)
    kind = preset.kind if preset else body.kind
    _check_kind(kind, settings, await _policy(db, principal))
    base_url = _check_url(kind, body.base_url or (preset.base_url if preset else None), settings)
    probe_profile = ResolvedProfile(
        id=uuid.uuid4(), name="discovery", kind=kind, model="", base_url=base_url, api_key=body.api_key
    )
    return await _list_models(resources, probe_profile)


@router.post("/profiles/{profile_id}/try", response_model=TryResponse, summary="Send one message to a model")
async def try_profile(
    profile_id: uuid.UUID,
    body: TryRequest,
    principal: ActiveMember,
    db: DbSession,
    service: ServiceDep,
    valkey: ValkeyDep,
) -> TryResponse:
    await enforce_rate_limit(valkey, f"llm-try:{principal.user_id}", limit=30, window_s=3600)
    resolved = await service.resolve_profile(await _get(db, profile_id))
    messages = [
        *([{"role": "system", "content": body.system}] if body.system else []),
        {"role": "user", "content": body.message},
    ]
    trace = CallTrace()
    try:
        parts = [
            delta
            async for delta in service.gateway.stream(
                [resolved], messages, _ctx(principal), on_usage=service.record_usage, trace=trace, max_tokens=800
            )
        ]
    except LLMUnavailableError as exc:
        raise InvalidProfileError("; ".join(exc.reasons)) from exc
    return TryResponse(
        text="".join(parts), ttft_ms=trace.ttft_ms, tokens_in=trace.usage.tokens_in, tokens_out=trace.usage.tokens_out
    )


# --- routing -------------------------------------------------------------------------------------------


@router.get("/routes", response_model=RoutesOut)
async def get_routes(principal: ActiveMember, db: DbSession) -> RoutesOut:
    routes = list(await db.scalars(select(LLMRoleRoute)))
    return RoutesOut(
        org={r.agent_role: list(r.profile_ids) for r in routes if r.user_id is None},
        mine={r.agent_role: list(r.profile_ids) for r in routes if r.user_id == principal.user_id},
    )


@router.put("/routes", response_model=RoutesOut)
async def put_routes(body: RoutesUpdate, request: Request, principal: ActiveMember, db: DbSession) -> RoutesOut:
    if body.scope == "org" and not principal.is_owner:
        raise ForbiddenError("Only owners can set the organization's default routing.")
    visible = {p.id: p for p in await db.scalars(select(LLMProfile))}
    user_id = None if body.scope == "org" else principal.user_id
    for role, ids in body.routes.items():
        if len(ids) != len(set(ids)):
            raise InvalidProfileError(f"Duplicate model in the {role.value} chain.")
        for profile_id in ids:
            profile = visible.get(profile_id)
            if profile is None:
                raise InvalidProfileError(f"Unknown model in the {role.value} chain.")
            if body.scope == "org" and profile.owner_user_id is not None:
                raise InvalidProfileError("Organization routing can only use organization-wide models.")
        await db.execute(
            delete(LLMRoleRoute).where(
                LLMRoleRoute.agent_role == role,
                LLMRoleRoute.user_id.is_(None) if user_id is None else LLMRoleRoute.user_id == user_id,
            )
        )
        if ids:
            db.add(LLMRoleRoute(org_id=principal.org_id, user_id=user_id, agent_role=role, profile_ids=ids))
    await record(
        db,
        org_id=principal.org_id,
        actor_id=principal.user_id,
        action="llm.routes_changed",
        meta=ClientMeta.from_request(request),
        target_type="llm_routes",
        target_id=body.scope,
        detail={role.value: [str(i) for i in ids] for role, ids in body.routes.items()},
    )
    await db.flush()
    return await get_routes(principal, db)
