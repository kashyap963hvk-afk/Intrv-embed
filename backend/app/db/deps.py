"""Per-request database session (unit of work)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """One session per request; commits when the handler succeeds, rolls back on any error.

    Declared with scope="function" (see `DbSession`) so the commit happens *before* the response is
    sent: a failed commit becomes an error response instead of a false success.
    """
    factory = request.app.state.resources.session_factory
    async with factory() as session:
        try:
            yield session
        except BaseException:
            await session.rollback()
            raise
        if session.in_transaction():
            await session.commit()


DbSession = Annotated[AsyncSession, Depends(get_db, scope="function")]
