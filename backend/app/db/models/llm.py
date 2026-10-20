"""Per-org encryption keys and the LLM gateway's profiles, routes and usage records."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Index, Integer, LargeBinary, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin, str_enum


class OrgKey(Base):
    """The org's data-encryption key, wrapped by the master key (see app/core/crypto.py)."""

    __tablename__ = "org_keys"

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True)
    wrapped_dek: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    rotated_at: Mapped[datetime | None]


class ProviderKind(StrEnum):
    OPENAI_COMPATIBLE = "OPENAI_COMPATIBLE"
    SERVER_LOCAL = "SERVER_LOCAL"
    BRIDGE = "BRIDGE"
    GGUF_MANAGED = "GGUF_MANAGED"
    FAKE = "FAKE"


class ProfileStatus(StrEnum):
    UNTESTED = "UNTESTED"
    OK = "OK"
    DEGRADED = "DEGRADED"
    ERROR = "ERROR"


class QualityTier(StrEnum):
    BASIC = "BASIC"
    USABLE = "USABLE"
    GOOD = "GOOD"
    EXCELLENT = "EXCELLENT"


class AgentRole(StrEnum):
    INTERVIEWER = "interviewer"
    CONTROLLER = "controller"
    GRADER = "grader"
    PLANNER = "planner"
    QUESTION_GEN = "question_gen"
    EXTRACTOR = "extractor"
    VISION = "vision"
    ROLE_BUILDER = "role_builder"


class LLMProfile(IdMixin, TimestampMixin, Base):
    """A model the gateway can call. `owner_user_id` NULL means org-shared (managed by owners)."""

    __tablename__ = "llm_profiles"

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[ProviderKind] = mapped_column(str_enum(ProviderKind, "provider_kind"))
    preset_key: Mapped[str | None] = mapped_column(String(64))
    base_url: Mapped[str | None] = mapped_column(String(500))
    encrypted_api_key: Mapped[bytes | None] = mapped_column(LargeBinary)
    api_key_last4: Mapped[str | None] = mapped_column(String(8))
    model: Mapped[str] = mapped_column(String(200))
    params: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"), default=dict)
    capabilities: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"), default=dict)
    quality_tier: Mapped[QualityTier | None] = mapped_column(str_enum(QualityTier, "quality_tier"))
    trains_on_data: Mapped[bool | None] = mapped_column(Boolean)
    rpm_limit: Mapped[int | None] = mapped_column(Integer)
    rpd_limit: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[ProfileStatus] = mapped_column(
        str_enum(ProfileStatus, "profile_status"), default=ProfileStatus.UNTESTED
    )
    last_probe_at: Mapped[datetime | None]
    last_error: Mapped[str | None] = mapped_column(Text)


class LLMRoleRoute(IdMixin, TimestampMixin, Base):
    """Ordered fallback chain of profiles for one agent role; user routes override the org default."""

    __tablename__ = "llm_role_routes"
    __table_args__ = (UniqueConstraint("org_id", "user_id", "agent_role", postgresql_nulls_not_distinct=True),)

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    agent_role: Mapped[AgentRole] = mapped_column(str_enum(AgentRole, "agent_role"))
    profile_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)))


class UsageEvent(IdMixin, Base):
    __tablename__ = "usage_events"
    __table_args__ = (Index("ix_usage_events_org_at", "org_id", "at"),)

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID | None]
    profile_id: Mapped[uuid.UUID | None]
    session_id: Mapped[uuid.UUID | None]
    agent_role: Mapped[str] = mapped_column(String(32))
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(200))
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    ttft_ms: Mapped[int | None] = mapped_column(Integer)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error_kind: Mapped[str | None] = mapped_column(String(40))
    at: Mapped[datetime] = mapped_column(server_default=text("now()"))


class BridgeDevice(IdMixin, TimestampMixin, Base):
    """A member's PC running the Local Model Bridge (ARCHITECTURE §10). Private to that member.

    The device authenticates its tunnel with a token stored only as a SHA-256 hash; `models` is what the device
    last announced (name, size, quantisation, context length), refreshed on every connect.
    """

    __tablename__ = "bridge_devices"

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    platform: Mapped[str] = mapped_column(String(60), default="", server_default="")
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    models: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"), default=list)
    capabilities: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"), default=dict)
    last_seen_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]
