"""Owner administration models."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.auth.schemas import PolicyOut, UserOut
from app.db.models import Role


class AdminUserOut(UserOut):
    policy: PolicyOut
    group_ids: list[uuid.UUID] = Field(default_factory=list)


class PolicyUpdate(BaseModel):
    can_edit_prompts: bool | None = None
    can_create_llm_profiles: bool | None = None
    can_use_voice: bool | None = None
    can_use_coding: bool | None = None
    monthly_interview_quota: int | None = Field(default=None, ge=0, le=100_000)
    clear_interview_quota: bool = False
    allowed_llm_profile_ids: list[uuid.UUID] | None = None
    clear_allowed_llm_profiles: bool = False


class RoleUpdate(BaseModel):
    role: Role


class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)


class GroupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str
    member_ids: list[uuid.UUID]
    created_at: datetime


class InviteIn(BaseModel):
    email: EmailStr
    role: Role = Role.MEMBER

    @field_validator("email")
    @classmethod
    def _lower(cls, value: str) -> str:
        return value.strip().lower()


class InviteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    role: Role
    expires_at: datetime
    created_at: datetime
    invited_by: uuid.UUID | None


class InviteCreated(BaseModel):
    invite: InviteOut
    link: str = Field(description="Share this link if email delivery is not configured.")
    email_sent: bool


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    actor_id: uuid.UUID | None
    action: str
    target_type: str | None
    target_id: str | None
    ip: str | None
    detail: dict[str, object]
    at: datetime


class AuditPage(BaseModel):
    items: list[AuditOut]
    next_cursor: str | None


class OrgSettingsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str


class OrgUpdate(BaseModel):
    name: str = Field(min_length=2, max_length=200)
