"""Row-Level Security tenant context.

Postgres policies read `app.org_id` / `app.user_id` / `app.role` (see migrations). We set them with
`set_config(..., is_local => true)`, so they live only for the current transaction. An `after_begin`
listener re-applies the context at the start of *every* transaction on a session, so a commit in the
middle of a request can never silently drop it. Without a context, policies match nothing (fail closed).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import event, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, SessionTransaction

_TENANT_KEY = "tenant_context"
_SET_CONTEXT = text(
    "SELECT set_config('app.org_id', :org_id, true), "
    "set_config('app.user_id', :user_id, true), "
    "set_config('app.role', :role, true)"
)


@dataclass(frozen=True, slots=True)
class TenantContext:
    org_id: uuid.UUID
    user_id: uuid.UUID | None = None
    role: str = ""


def _params(ctx: TenantContext) -> dict[str, str]:
    return {"org_id": str(ctx.org_id), "user_id": str(ctx.user_id or ""), "role": ctx.role}


@event.listens_for(Session, "after_begin")
def _apply_on_begin(session: Session, _transaction: SessionTransaction, connection: Connection) -> None:
    ctx = session.info.get(_TENANT_KEY)
    if isinstance(ctx, TenantContext):
        connection.execute(_SET_CONTEXT, _params(ctx))


async def set_tenant_context(session: AsyncSession, ctx: TenantContext) -> None:
    """Bind `ctx` to the session; applies immediately if a transaction is already open."""
    session.info[_TENANT_KEY] = ctx
    if session.in_transaction():
        await session.execute(_SET_CONTEXT, _params(ctx))


def get_tenant_context(session: AsyncSession) -> TenantContext | None:
    ctx = session.info.get(_TENANT_KEY)
    return ctx if isinstance(ctx, TenantContext) else None
