"""Hands documents to the ingestion worker (Celery in deployments, inline in tests and single-process dev)."""

from __future__ import annotations

import uuid
from typing import Protocol

from celery import Celery

from app.knowledge.ingest import IngestDeps, ingest_document


class IngestDispatcher(Protocol):
    async def dispatch(self, *, org_id: uuid.UUID, user_id: uuid.UUID, document_id: uuid.UUID) -> None: ...


class CeleryDispatcher:
    def __init__(self, celery: Celery) -> None:
        self._celery = celery

    async def dispatch(self, *, org_id: uuid.UUID, user_id: uuid.UUID, document_id: uuid.UUID) -> None:
        self._celery.send_task("ingest.document", args=[str(org_id), str(user_id), str(document_id)], queue="ingest")


class InlineDispatcher:
    """Runs ingestion immediately in the current process (tests, demos without a worker)."""

    def __init__(self, deps: IngestDeps) -> None:
        self._deps = deps

    async def dispatch(self, *, org_id: uuid.UUID, user_id: uuid.UUID, document_id: uuid.UUID) -> None:
        await ingest_document(self._deps, org_id=org_id, user_id=user_id, document_id=document_id)
