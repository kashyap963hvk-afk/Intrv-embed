"""Per-process async runtime for Celery tasks.

Celery workers are synchronous; our data layer is async. Each worker process keeps one event loop and one set
of clients (DB pool, Valkey, S3, embedder with any loaded model) for its lifetime, instead of rebuilding them
for every task.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

import httpx

from app.agents.runtime import AgentDeps
from app.connectors.sync import SyncDeps, connector_clients_for
from app.core.config import get_settings
from app.core.email import EmailSender
from app.core.keyring import Keyring
from app.core.resources import create_s3_client
from app.core.ssrf import create_http_client
from app.db.session import create_engine, create_session_factory
from app.knowledge.dispatch import CeleryDispatcher
from app.knowledge.ingest import IngestDeps
from app.knowledge.storage import ObjectStorage
from app.rag.engine import RagEngine

_loop: asyncio.AbstractEventLoop | None = None
_deps: IngestDeps | None = None
_sync: SyncDeps | None = None
_agents: AgentDeps | None = None


def _event_loop() -> asyncio.AbstractEventLoop:
    global _loop  # noqa: PLW0603 - one loop per worker process, created lazily after fork
    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
    return _loop


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    return _event_loop().run_until_complete(coro)


def ingest_deps() -> IngestDeps:
    global _deps  # noqa: PLW0603 - process-wide clients, created inside the worker's loop
    if _deps is None:
        settings = get_settings()
        engine = create_engine(settings)
        from redis.asyncio import Redis  # noqa: PLC0415

        _deps = IngestDeps(
            settings=settings,
            session_factory=create_session_factory(engine),
            valkey=Redis.from_url(settings.valkey_url),
            storage=ObjectStorage(create_s3_client(settings), settings.s3_bucket),
            rag=RagEngine(settings, httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=5.0))),
            public_http=create_http_client(allow_private=settings.allow_private_llm_endpoints),
        )
    return _deps


def sync_deps() -> SyncDeps:
    global _sync  # noqa: PLW0603 - process-wide, like ingest_deps()
    if _sync is None:
        from celery import current_app  # noqa: PLC0415

        ingest = ingest_deps()
        settings = ingest.settings
        _sync = SyncDeps(
            ingest=ingest,
            clients=connector_clients_for(settings, ingest.public_http),
            keyring=Keyring(settings.master_key),
            dispatcher=CeleryDispatcher(current_app),
            mailer=EmailSender(settings),
        )
    return _sync


def agent_deps() -> AgentDeps:
    global _agents  # noqa: PLW0603 - process-wide, like ingest_deps()
    if _agents is None:
        from app.llm.factory import make_provider_factory  # noqa: PLC0415
        from app.llm.gateway import LLMGateway  # noqa: PLC0415
        from app.llm.limits import ProfileLimiter  # noqa: PLC0415

        ingest = ingest_deps()
        settings = ingest.settings
        factory = make_provider_factory(
            settings, create_http_client(allow_private=False), create_http_client(allow_private=True)
        )
        _agents = AgentDeps(
            settings=settings,
            keyring=Keyring(settings.master_key),
            gateway=LLMGateway(factory, ProfileLimiter(ingest.valkey)),
            rag=ingest.rag,
            valkey=ingest.valkey,
        )
    return _agents
