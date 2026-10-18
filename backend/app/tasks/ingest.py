"""Celery task wrapper for document ingestion."""

from __future__ import annotations

import uuid

from celery import shared_task

from app.core.logging import get_logger

log = get_logger("app.tasks.ingest")


@shared_task(name="ingest.document", acks_late=True, max_retries=0, soft_time_limit=1800, time_limit=1900)
def ingest_document_task(org_id: str, user_id: str, document_id: str) -> str | None:
    from app.knowledge.ingest import ingest_document  # noqa: PLC0415 - keep worker import graph light
    from workers.runtime import ingest_deps, run  # noqa: PLC0415

    status = run(
        ingest_document(
            ingest_deps(), org_id=uuid.UUID(org_id), user_id=uuid.UUID(user_id), document_id=uuid.UUID(document_id)
        )
    )
    return status.value if status else None
