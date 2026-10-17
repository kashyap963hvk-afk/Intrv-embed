"""FastAPI dependencies: principal resolution (with RLS context), role guards and CSRF protection."""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import Depends, Request
from redis.asyncio import Redis
from sqlalchemy import exists, select

from app.auth.cookies import ACCESS_COOKIE, CSRF_COOKIE, CSRF_HEADER
from app.auth.principal import Principal
from app.core.config import Settings
from app.core.errors import ForbiddenError, UnauthorizedError
from app.core.security import decode_access_token, tokens_match
from app.db.deps import DbSession
from app.db.models import RefreshToken, User, UserStatus
from app.db.tenant import TenantContext, set_tenant_context

_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_valkey(request: Request) -> Redis:
    valkey: Redis = request.app.state.resources.valkey
    return valkey


SettingsDep = Annotated[Settings, Depends(get_settings)]
ValkeyDep = Annotated[Redis, Depends(get_valkey)]


async def get_principal(request: Request, db: DbSession, settings: SettingsDep) -> Principal:
    """Authenticate the access cookie, bind the tenant context, and load the user fresh from the DB."""
    token = request.cookies.get(ACCESS_COOKIE)
    claims = decode_access_token(settings, token) if token else None
    if claims is None:
        raise UnauthorizedError()

    await set_tenant_context(db, TenantContext(org_id=claims.org_id, user_id=claims.user_id))
    user = await db.get(User, claims.user_id)
    if user is None or user.org_id != claims.org_id or user.status == UserStatus.SUSPENDED:
        raise UnauthorizedError()
    session_alive = await db.scalar(
        select(
            exists().where(
                RefreshToken.family_id == claims.family_id,
                RefreshToken.user_id == user.id,
                RefreshToken.revoked_at.is_(None),
            )
        )
    )
    if not session_alive:
        raise UnauthorizedError("Session has ended; please sign in again.")

    await set_tenant_context(db, TenantContext(org_id=user.org_id, user_id=user.id, role=user.role.value))
    structlog.contextvars.bind_contextvars(org_id=str(user.org_id), user_id=str(user.id))
    return Principal(
        user_id=user.id,
        org_id=user.org_id,
        role=user.role,
        status=user.status,
        email=user.email,
        name=user.name,
        family_id=claims.family_id,
    )


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


async def require_active_member(principal: CurrentPrincipal) -> Principal:
    if not principal.is_active:
        raise ForbiddenError("Your account is waiting for approval by an organization owner.")
    return principal


async def require_owner(principal: CurrentPrincipal) -> Principal:
    if not principal.is_owner:
        raise ForbiddenError("Only organization owners can do this.")
    return principal


ActiveMember = Annotated[Principal, Depends(require_active_member)]
Owner = Annotated[Principal, Depends(require_owner)]


async def csrf_protect(request: Request) -> None:
    """Double-submit CSRF check for state-changing requests."""
    if request.method not in _UNSAFE_METHODS:
        return
    cookie = request.cookies.get(CSRF_COOKIE)
    header = request.headers.get(CSRF_HEADER)
    if not cookie or not header or not tokens_match(cookie, header):
        raise ForbiddenError("Missing or invalid CSRF token. Fetch one from GET /api/auth/csrf.")
