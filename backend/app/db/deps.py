"""Per-request database session (unit of work) with after-commit hooks."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger

log = get_logger("app.db")

AfterCommit = Callable[[], Awaitable[None]]
_HOOKS_KEY = "after_commit"


def after_commit(db: AsyncSession, hook: AfterCommit) -> None:
    """Run `hook` only once the request's transaction has committed (e.g. enqueue a job that reads the
    rows just written, or invalidate a cache). Hooks are dropped on rollback."""
    db.info.setdefault(_HOOKS_KEY, []).append(hook)


async def run_after_commit_hooks(db: AsyncSession) -> None:
    hooks: list[AfterCommit] = db.info.pop(_HOOKS_KEY, [])
    for hook in hooks:
        try:
            await hook()
        except Exception:
            log.exception("after_commit_hook_failed", hook=getattr(hook, "__qualname__", repr(hook)))


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
            session.info.pop(_HOOKS_KEY, None)
            await session.rollback()
            raise
        if session.in_transaction():
            await session.commit()
        await run_after_commit_hooks(session)


DbSession = Annotated[AsyncSession, Depends(get_db, scope="function")]
