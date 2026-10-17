"""Celery entrypoint for background work (ingestion, grading, connector sync).

Worker:  celery -A workers.celery_app:celery_app worker -Q ingest,grade,sync,default
Beat:    celery -A workers.celery_app:celery_app beat
"""

from __future__ import annotations

from typing import Any

from celery.signals import setup_logging

from app.core.config import get_settings
from app.core.logging import configure_logging
from workers.config import create_celery


@setup_logging.connect
def _configure_worker_logging(**_: Any) -> None:
    settings = get_settings()
    configure_logging(level=settings.log_level, json=settings.log_json)


celery_app = create_celery(get_settings())
