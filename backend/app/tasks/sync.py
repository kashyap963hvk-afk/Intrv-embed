"""Celery tasks for connector sync: one source, and the minutely scan for sources that are due."""

from __future__ import annotations

import uuid

from celery import current_app, shared_task
from sqlalchemy import text

from app.core.logging import get_logger

log = get_logger("app.tasks.sync")

DUE_BATCH = 50


@shared_task(name="sync.source", acks_late=True, max_retries=0, soft_time_limit=3300, time_limit=3500)
def sync_source_task(org_id: str, user_id: str, source_id: str) -> dict[str, object] | None:
    from app.connectors.sync import sync_source  # noqa: PLC0415 - keep the worker import graph light
    from workers.runtime import run, sync_deps  # noqa: PLC0415

    result = run(
        sync_source(sync_deps(), org_id=uuid.UUID(org_id), user_id=uuid.UUID(user_id), source_id=uuid.UUID(source_id))
    )
    return result.as_json() if result else None


@shared_task(name="sync.due", acks_late=False, max_retries=0, soft_time_limit=50, time_limit=55)
def sync_due_task() -> int:
    """Enqueue every source whose next sync time has passed (beat runs this every minute)."""
    from workers.runtime import ingest_deps, run  # noqa: PLC0415

    async def due() -> list[tuple[uuid.UUID, uuid.UUID, uuid.UUID]]:
        # The SECURITY DEFINER function returns ids only; each sync then runs inside its own tenant context.
        async with ingest_deps().session_factory() as db:
            rows = await db.execute(
                text("SELECT id, org_id, created_by FROM app_due_connector_sources(:n)"), {"n": DUE_BATCH}
            )
            return [(row.id, row.org_id, row.created_by) for row in rows]

    sources = run(due())
    for source_id, org_id, user_id in sources:
        current_app.send_task("sync.source", args=[str(org_id), str(user_id), str(source_id)], queue="sync")
    if sources:
        log.info("sync.enqueued", count=len(sources))
    return len(sources)
