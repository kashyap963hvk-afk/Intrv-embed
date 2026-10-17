"""The authenticated caller."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.db.models import Role, UserStatus


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: uuid.UUID
    org_id: uuid.UUID
    role: Role
    status: UserStatus
    email: str
    name: str
    family_id: uuid.UUID

    @property
    def is_owner(self) -> bool:
        return self.role == Role.OWNER and self.status == UserStatus.ACTIVE

    @property
    def is_active(self) -> bool:
        return self.status == UserStatus.ACTIVE
