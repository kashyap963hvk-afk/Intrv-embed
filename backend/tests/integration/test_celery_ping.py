from __future__ import annotations

import pytest
from celery.contrib.testing.worker import start_worker

from tests.conftest import make_settings
from workers.config import QUEUES, create_celery

pytestmark = pytest.mark.integration


def test_ping_task_round_trips_through_valkey(valkey: str) -> None:
    app = create_celery(make_settings(valkey_url=valkey))
    app.loader.import_default_modules()

    with start_worker(app, pool="solo", perform_ping_check=False, queues=list(QUEUES)):
        result = app.send_task("system.ping").get(timeout=20)

    assert result["pong"] is True
    assert result["worker"]
    assert result["at"].endswith("+00:00")
