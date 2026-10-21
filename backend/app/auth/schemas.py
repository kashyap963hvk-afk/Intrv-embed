"""Request/response models for authentication."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core.security import PASSWORD_MAX_LENGTH
from app.db.models import Role, UserStatus

SLUG_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,62}[a-z0-9])$")


def _normalize_email(value: str) -> str:
    return value.strip().lower()


def _normalize_slug(value: str) -> str:
    slug = value.strip().lower()
    if not SLUG_PATTERN.match(slug):
        raise ValueError("must be 3-64 characters: lowercase letters, digits and hyphens")
    return slug


class SignupRequest(BaseModel):
    org_slug: str
    email: EmailStr
    name: str = Field(min_length=1, max_length=200)
    password: str = Field(max_length=PASSWORD_MAX_LENGTH)

    _email = field_validator("email")(_normalize_email)
    _slug = field_validator("org_slug")(_normalize_slug)


class RegisterOrgRequest(BaseModel):
    org_name: str = Field(min_length=2, max_length=200)
    org_slug: str
    email: EmailStr
    name: str = Field(min_length=1, max_length=200)
    password: str = Field(max_length=PASSWORD_MAX_LENGTH)

    _email = field_validator("email")(_normalize_email)
    _slug = field_validator("org_slug")(_normalize_slug)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)
    org_slug: str | None = None

    _email = field_validator("email")(_normalize_email)


class AcceptInviteRequest(BaseModel):
    token: str = Field(min_length=16, max_length=128)
    name: str = Field(min_length=1, max_length=200)
    password: str = Field(max_length=PASSWORD_MAX_LENGTH)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)
    new_password: str = Field(max_length=PASSWORD_MAX_LENGTH)


class CsrfResponse(BaseModel):
    csrf_token: str


class SignupAccepted(BaseModel):
    status: Literal["pending_approval"] = "pending_approval"
    message: str


class OrgOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str


class PolicyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    can_edit_prompts: bool
    can_create_llm_profiles: bool
    can_use_voice: bool
    can_use_coding: bool
    monthly_interview_quota: int | None
    allowed_llm_profile_ids: list[uuid.UUID] | None


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    name: str
    role: Role
    status: UserStatus
    created_at: datetime
    last_login_at: datetime | None


class MeResponse(BaseModel):
    user: UserOut
    org: OrgOut
    policy: PolicyOut


class InvitePreview(BaseModel):
    org_name: str
    email: str
    role: Role
    usable: bool
    reason: str | None = None
    sign_in: Literal["password", "google"] = Field(
        default="password", description="'google': accept with Sign in with Google (no password)"
    )
