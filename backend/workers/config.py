"""Celery application factory (importable without environment variables)."""

from __future__ import annotations

from celery import Celery
from kombu import Queue

from app.core.config import Settings

QUEUES = ("ingest", "grade", "sync", "default")

TASK_ROUTES = {
    "ingest.*": {"queue": "ingest"},
    "grade.*": {"queue": "grade"},
    "sync.*": {"queue": "sync"},
}


def create_celery(settings: Settings) -> Celery:
    app = Celery("intrv", broker=settings.valkey_url, backend=settings.valkey_url)
    app.conf.update(
        task_queues=[Queue(name) for name in QUEUES],
        task_default_queue="default",
        task_routes=TASK_ROUTES,
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        task_time_limit=3600,
        task_soft_time_limit=3300,
        result_expires=3600,
        broker_connection_retry_on_startup=True,
        timezone="UTC",
        enable_utc=True,
        imports=(
            "app.tasks.system",
            "app.tasks.ingest",
            "app.tasks.sync",
            "app.tasks.questions",
            "app.tasks.interviews",
        ),
        beat_schedule={
            "connector-sync-due": {"task": "sync.due", "schedule": 60.0, "options": {"queue": "sync"}},
            "heal-stuck-work": {"task": "system.heal", "schedule": 300.0, "options": {"queue": "default"}},
        },
    )
    return app
