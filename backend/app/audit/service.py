"""Audit trail: every security-relevant action is recorded in the same transaction as the change."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog


@dataclass(frozen=True, slots=True)
class ClientMeta:
    ip: str | None
    user_agent: str | None

    @classmethod
    def from_request(cls, request: Request) -> ClientMeta:
        agent = request.headers.get("user-agent")
        return cls(ip=request.client.host if request.client else None, user_agent=agent[:400] if agent else None)


SYSTEM_META = ClientMeta(ip=None, user_agent="system")


async def record(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    action: str,
    meta: ClientMeta,
    target_type: str | None = None,
    target_id: uuid.UUID | str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    db.add(
        AuditLog(
            org_id=org_id,
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            ip=meta.ip,
            user_agent=meta.user_agent,
            detail=detail or {},
        )
    )
