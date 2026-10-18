"""Hands sources to the sync worker (Celery `sync` queue), or runs them in-process when INGEST_INLINE is set."""

from __future__ import annotations

import uuid
from typing import Protocol

from celery import Celery

from app.connectors.sync import SyncDeps, sync_source


class SyncDispatcher(Protocol):
    async def dispatch(self, *, org_id: uuid.UUID, user_id: uuid.UUID, source_id: uuid.UUID) -> None: ...


class CelerySyncDispatcher:
    def __init__(self, celery: Celery) -> None:
        self._celery = celery

    async def dispatch(self, *, org_id: uuid.UUID, user_id: uuid.UUID, source_id: uuid.UUID) -> None:
        self._celery.send_task("sync.source", args=[str(org_id), str(user_id), str(source_id)], queue="sync")


class InlineSyncDispatcher:
    def __init__(self, deps: SyncDeps) -> None:
        self._deps = deps

    async def dispatch(self, *, org_id: uuid.UUID, user_id: uuid.UUID, source_id: uuid.UUID) -> None:
        await sync_source(self._deps, org_id=org_id, user_id=user_id, source_id=source_id)
