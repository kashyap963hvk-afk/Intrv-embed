"""API models for LLM profiles, presets, probing and routing."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.db.models import AgentRole, ProfileStatus, ProviderKind, QualityTier
from app.llm.presets import PresetSupports

ProfileScope = Literal["personal", "org"]


class PresetOut(BaseModel):
    key: str
    display_name: str
    kind: ProviderKind
    base_url: str
    requires_api_key: bool
    docs_url: str
    signup_url: str | None
    free_tier: bool
    trains_on_data: bool | None
    notes: str | None
    supports: PresetSupports
    model_hints: list[str]


class GenerationParams(BaseModel):
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1, le=64_000)


class ProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    scope: ProfileScope = "personal"
    kind: ProviderKind = ProviderKind.OPENAI_COMPATIBLE
    preset_key: str | None = Field(default=None, max_length=64)
    base_url: str | None = Field(default=None, max_length=500)
    api_key: str | None = Field(default=None, max_length=1000)
    model: str = Field(min_length=1, max_length=200)
    params: GenerationParams = Field(default_factory=GenerationParams)
    rpm_limit: int | None = Field(default=None, ge=1, le=100_000)
    rpd_limit: int | None = Field(default=None, ge=1, le=10_000_000)
    trains_on_data: bool | None = Field(
        default=None, description="Owners only: override the preset (e.g. a paid tier with a no-training agreement)."
    )
    script: list[dict[str, Any]] | None = Field(default=None, description="FAKE provider only (dev/test).")
    device_id: uuid.UUID | None = Field(default=None, description="BRIDGE only: your paired PC (see /api/bridge).")
    model_path: str | None = Field(
        default=None, max_length=1000, description="GGUF_MANAGED only (owners, self-host): absolute .gguf path."
    )


class ProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    base_url: str | None = Field(default=None, max_length=500)
    api_key: str | None = Field(default=None, max_length=1000)
    model: str | None = Field(default=None, min_length=1, max_length=200)
    params: GenerationParams | None = None
    rpm_limit: int | None = Field(default=None, ge=1, le=100_000)
    rpd_limit: int | None = Field(default=None, ge=1, le=10_000_000)
    trains_on_data: bool | None = None


class ProfileOut(BaseModel):
    id: uuid.UUID
    name: str
    scope: ProfileScope
    owned_by_me: bool
    kind: ProviderKind
    preset_key: str | None
    base_url: str | None
    has_api_key: bool
    api_key_last4: str | None
    model: str
    params: dict[str, Any]
    capabilities: dict[str, Any]
    quality_tier: QualityTier | None
    trains_on_data: bool | None
    rpm_limit: int | None
    rpd_limit: int | None
    status: ProfileStatus
    last_probe_at: datetime | None
    last_error: str | None
    created_at: datetime


class DiscoverRequest(BaseModel):
    kind: ProviderKind = ProviderKind.OPENAI_COMPATIBLE
    preset_key: str | None = None
    base_url: str | None = Field(default=None, max_length=500)
    api_key: str | None = Field(default=None, max_length=1000)


class ModelList(BaseModel):
    models: list[str]


class TryRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    system: str | None = Field(default=None, max_length=4000)


class TryResponse(BaseModel):
    text: str
    ttft_ms: int | None
    tokens_in: int
    tokens_out: int


class RoutesOut(BaseModel):
    org: dict[AgentRole, list[uuid.UUID]]
    mine: dict[AgentRole, list[uuid.UUID]]


class RoutesUpdate(BaseModel):
    scope: ProfileScope
    routes: dict[AgentRole, list[uuid.UUID]] = Field(
        description="Ordered fallback chain per agent role; an empty list removes the route."
    )
