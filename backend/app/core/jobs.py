"""Background jobs by name: Celery in deployments, in-process when INGEST_INLINE is set (tests, demos)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Protocol

from celery import Celery

JobHandler = Callable[[Mapping[str, str]], Awaitable[None]]


class JobDispatcher(Protocol):
    async def submit(self, name: str, args: Mapping[str, str]) -> None: ...


class CeleryJobs:
    def __init__(self, celery: Celery) -> None:
        self._celery = celery

    async def submit(self, name: str, args: Mapping[str, str]) -> None:
        self._celery.send_task(name, kwargs=dict(args))


class InlineJobs:
    def __init__(self, handlers: Mapping[str, JobHandler]) -> None:
        self._handlers = handlers

    async def submit(self, name: str, args: Mapping[str, str]) -> None:
        await self._handlers[name](args)
