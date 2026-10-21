"""Per-member limits set by owners under Admin → Users → Policy: voice, coding tasks, editing the interviewer
persona, and a monthly interview quota. Owners are never limited."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.principal import Principal
from app.core.errors import ForbiddenError
from app.db.models import InterviewSession, Role, User, UserPolicy


async def _member_policy(db: AsyncSession, user_id: uuid.UUID, role: Role) -> UserPolicy | None:
    if role == Role.OWNER:
        return None
    return await db.get(UserPolicy, user_id)


def month_start(now: datetime) -> datetime:
    return now.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def check_new_interview(db: AsyncSession, principal: Principal, *, mode: str, settings: dict[str, Any]) -> None:
    policy = await _member_policy(db, principal.user_id, principal.role)
    if policy is None:
        return
    if mode == "VOICE" and not policy.can_use_voice:
        raise ForbiddenError("Voice interviews are not enabled for your account; use text, or ask an owner.")
    if settings.get("practical_tasks") and not policy.can_use_coding:
        raise ForbiddenError("Practical tasks are not enabled for your account; ask an owner.")
    quota = policy.monthly_interview_quota
    if quota is not None:
        used = await db.scalar(
            select(func.count())
            .select_from(InterviewSession)
            .where(
                InterviewSession.owner_user_id == principal.user_id,
                InterviewSession.created_at >= month_start(datetime.now(UTC)),
            )
        )
        if int(used or 0) >= quota:
            raise ForbiddenError(f"You have used your {quota} interviews for this month; ask an owner to raise it.")


async def check_prompt_edit(db: AsyncSession, principal: Principal) -> None:
    policy = await _member_policy(db, principal.user_id, principal.role)
    if policy is not None and not policy.can_edit_prompts:
        raise ForbiddenError("Editing the interviewer persona is not enabled for your account; ask an owner.")


async def voice_allowed(db: AsyncSession, creator_id: uuid.UUID) -> bool:
    """Whether the interview's creator may run voice interviews (checked when a candidate switches to voice)."""
    creator = await db.get(User, creator_id)
    if creator is None:
        return False
    policy = await _member_policy(db, creator.id, creator.role)
    return policy is None or policy.can_use_voice
