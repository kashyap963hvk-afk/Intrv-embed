"""Database-aware LLM access: resolves the routed profile chain for a call, enforces organization and user
policy, decrypts keys, and records usage. All queries run inside the caller's tenant context (RLS)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator, Sequence
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.core.keyring import Keyring
from app.db.models import (
    LLMProfile,
    LLMRoleRoute,
    Organization,
    ProfileStatus,
    ProviderKind,
    QualityTier,
    Role,
    UsageEvent,
    User,
    UserPolicy,
)
from app.llm.gateway import CallTrace, LLMGateway
from app.llm.presets import get_preset
from app.llm.types import CallCtx, Completion, Message, ResolvedProfile, UsageRecord
from app.orgs.policy import OrgPolicy, org_policy

API_KEY_PURPOSE = "llm_profile.api_key:{profile_id}"
_TIER_ORDER = {QualityTier.EXCELLENT: 0, QualityTier.GOOD: 1, QualityTier.USABLE: 2, QualityTier.BASIC: 3, None: 4}
_STATUS_ORDER = {ProfileStatus.OK: 0, ProfileStatus.DEGRADED: 1, ProfileStatus.UNTESTED: 2, ProfileStatus.ERROR: 3}


class PolicyViolationError(AppError):
    status_code = 403
    slug = "llm-policy"
    title = "No model you are allowed to use can handle this request"


def api_key_purpose(profile_id: uuid.UUID) -> str:
    return API_KEY_PURPOSE.format(profile_id=profile_id)


def _policy_problems(
    profile: LLMProfile,
    *,
    ctx: CallCtx,
    org: OrgPolicy,
    user_policy: UserPolicy | None,
    user_role: Role | None,
    settings: Settings,
) -> Iterator[str]:
    if profile.kind == ProviderKind.FAKE:
        if settings.is_prod:
            yield "the scripted test provider is disabled in production"
    elif profile.kind not in org.allowed_provider_kinds:
        yield "your organization does not allow this type of provider"
    if org.allowed_preset_keys is not None and profile.preset_key and profile.preset_key not in org.allowed_preset_keys:
        yield "your organization does not allow this provider"
    if profile.owner_user_id is not None and not org.allow_member_llm_profiles and user_role != Role.OWNER:
        yield "your organization only allows shared models"
    allowed_ids = user_policy.allowed_llm_profile_ids if user_policy else None
    if allowed_ids is not None and profile.id not in allowed_ids:
        yield "this model is not in your allowed list"
    if ctx.contains_pii and org.block_training_providers_for_pii and profile.trains_on_data is not False:
        yield "this provider may use data for training (or its policy is unknown), so it cannot receive candidate data"


def policy_problem(
    profile: LLMProfile,
    *,
    ctx: CallCtx,
    org: OrgPolicy,
    user_policy: UserPolicy | None,
    user_role: Role | None,
    settings: Settings,
) -> str | None:
    """Why `profile` may not serve `ctx`, or None when it may."""
    problems = _policy_problems(
        profile, ctx=ctx, org=org, user_policy=user_policy, user_role=user_role, settings=settings
    )
    return next(problems, None)


class LLMService:
    def __init__(self, db: AsyncSession, *, settings: Settings, keyring: Keyring, gateway: LLMGateway) -> None:
        self.db = db
        self.settings = settings
        self.keyring = keyring
        self.gateway = gateway

    # --- resolution ------------------------------------------------------------------------------

    async def resolve_profile(self, profile: LLMProfile) -> ResolvedProfile:
        api_key = (
            await self.keyring.decrypt(
                self.db, profile.org_id, profile.encrypted_api_key, purpose=api_key_purpose(profile.id)
            )
            if profile.encrypted_api_key
            else None
        )
        preset = get_preset(profile.preset_key)
        return ResolvedProfile(
            id=profile.id,
            name=profile.name,
            kind=profile.kind,
            model=profile.model,
            base_url=profile.base_url,
            api_key=api_key,
            preset_key=profile.preset_key,
            params=dict(profile.params or {}),
            capabilities=dict(profile.capabilities or {}),
            trains_on_data=profile.trains_on_data,
            rpm_limit=profile.rpm_limit or (preset.default_rpm if preset else None),
            rpd_limit=profile.rpd_limit or (preset.default_rpd if preset else None),
            quality_tier=profile.quality_tier,
        )

    async def _route_ids(self, ctx: CallCtx) -> list[uuid.UUID] | None:
        routes = list(
            await self.db.scalars(select(LLMRoleRoute).where(LLMRoleRoute.agent_role == ctx.role))
        )  # RLS: the org default plus (only) the caller's own route
        own = next((r for r in routes if r.user_id is not None and r.user_id == ctx.user_id), None)
        default = next((r for r in routes if r.user_id is None), None)
        chosen = own or default
        return list(chosen.profile_ids) if chosen and chosen.profile_ids else None

    async def candidate_profiles(self, ctx: CallCtx) -> list[LLMProfile]:
        visible = {p.id: p for p in await self.db.scalars(select(LLMProfile))}
        routed = await self._route_ids(ctx)
        if routed is not None:
            return [visible[i] for i in routed if i in visible]
        return sorted(
            (p for p in visible.values() if p.status != ProfileStatus.ERROR),
            key=lambda p: (_STATUS_ORDER[p.status], _TIER_ORDER[p.quality_tier], p.owner_user_id is None, p.name),
        )

    async def resolve_chain(self, ctx: CallCtx) -> list[ResolvedProfile]:
        org = await self.db.get(Organization, ctx.org_id)
        if org is None:
            raise PolicyViolationError("Organization not found.")
        policy = org_policy(org)
        user_policy = await self.db.get(UserPolicy, ctx.user_id) if ctx.user_id else None
        user_role = await self._user_role(ctx.user_id)
        chain: list[ResolvedProfile] = []
        blocked: list[str] = []
        for profile in await self.candidate_profiles(ctx):
            problem = policy_problem(
                profile, ctx=ctx, org=policy, user_policy=user_policy, user_role=user_role, settings=self.settings
            )
            if problem:
                blocked.append(f"{profile.name}: {problem}")
                continue
            chain.append(await self.resolve_profile(profile))
        if not chain:
            detail = (
                "No usable model for this task. " + "; ".join(blocked)
                if blocked
                else "No model is configured yet. Add one under AI models."
            )
            raise PolicyViolationError(detail, extra={"blocked": blocked})
        return chain

    async def _user_role(self, user_id: uuid.UUID | None) -> Role | None:
        if user_id is None:
            return None
        user = await self.db.get(User, user_id)
        return user.role if user else None

    # --- usage --------------------------------------------------------------------------------------

    async def record_usage(self, record: UsageRecord) -> None:
        self.db.add(
            UsageEvent(
                org_id=record.ctx.org_id,
                user_id=record.ctx.user_id,
                profile_id=record.profile.id,
                session_id=record.ctx.session_id,
                agent_role=record.ctx.role.value,
                provider=record.profile.provider_label,
                model=record.profile.model,
                tokens_in=record.usage.tokens_in,
                tokens_out=record.usage.tokens_out,
                latency_ms=record.latency_ms,
                ttft_ms=record.ttft_ms,
                ok=record.ok,
                error_kind=record.error_kind,
            )
        )

    # --- calls ----------------------------------------------------------------------------------------

    async def stream(
        self, ctx: CallCtx, messages: Sequence[Message], *, trace: CallTrace | None = None, **options: Any
    ) -> AsyncIterator[str]:
        chain = await self.resolve_chain(ctx)
        async for delta in self.gateway.stream(
            chain, messages, ctx, on_usage=self.record_usage, trace=trace, **options
        ):
            yield delta

    async def complete(self, ctx: CallCtx, messages: Sequence[Message], **options: Any) -> Completion:
        chain = await self.resolve_chain(ctx)
        return await self.gateway.complete(chain, messages, ctx, on_usage=self.record_usage, **options)

    async def complete_json[T: BaseModel](
        self, ctx: CallCtx, messages: Sequence[Message], schema: type[T], **options: Any
    ) -> T:
        chain = await self.resolve_chain(ctx)
        return await self.gateway.complete_json(chain, messages, schema, ctx, on_usage=self.record_usage, **options)
