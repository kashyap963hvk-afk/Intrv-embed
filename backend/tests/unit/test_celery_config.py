from __future__ import annotations

from tests.conftest import make_settings
from workers.config import QUEUES, create_celery


def test_queues_and_routes() -> None:
    app = create_celery(make_settings())
    assert {q.name for q in app.conf.task_queues} == set(QUEUES)
    assert app.conf.task_default_queue == "default"
    router = app.amqp.router
    assert router.route({}, "ingest.document")["queue"].name == "ingest"
    assert router.route({}, "grade.topic")["queue"].name == "grade"
    assert router.route({}, "sync.source")["queue"].name == "sync"
    assert router.route({}, "system.ping")["queue"].name == "default"


def test_reliability_settings() -> None:
    conf = create_celery(make_settings()).conf
    assert conf.task_acks_late is True
    assert conf.worker_prefetch_multiplier == 1
    assert conf.accept_content == ["json"]
