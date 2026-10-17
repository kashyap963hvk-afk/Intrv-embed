"""Operational tasks."""

from __future__ import annotations

import socket
from datetime import UTC, datetime
from typing import TypedDict

from celery import shared_task


class PingResult(TypedDict):
    pong: bool
    worker: str
    at: str


@shared_task(name="system.ping")
def ping() -> PingResult:
    """Round-trip check that a worker is consuming the default queue."""
    return {"pong": True, "worker": socket.gethostname(), "at": datetime.now(UTC).isoformat()}
