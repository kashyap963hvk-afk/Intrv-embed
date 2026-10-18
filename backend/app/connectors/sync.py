"""Keeps a space in step with an external selection (ARCHITECTURE §6.4).

Runs as the person who created the source, so their tenant context, space permissions and connector
credentials apply; if they lose write access the sync fails instead of writing where they no longer may.

- New file → new document; changed content (different hash) → new version, re-ingested; the old version's
  vectors are replaced only after the new ones are written, and its original file is removed once READY.
- Unchanged content with a new etag only updates the etag.
- Remote deletion (or the file leaving the selection) → the document is deleted with its vectors and files.
- Failures back off exponentially (capped at a day) and the source's creator is emailed on the first one.
"""

from __future__ import annotations

import hashlib
import io
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import httpx2
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import SYSTEM_META, record
from app.connectors.accounts import open_connector
from app.connectors.base import ConnectorError, RemoteItem, Selection, StorageConnector
from app.connectors.registry import ConnectorClients, build_connector
from app.core.config import Settings
from app.core.email import EmailSender, OutgoingEmail
from app.core.keyring import Keyring
from app.core.logging import get_logger
from app.db.deps import after_commit, run_after_commit_hooks
from app.db.models import (
    AccountStatus,
    ConnectorAccount,
    ConnectorKind,
    ConnectorSource,
    DocStatus,
    Document,
    SourceStatus,
    User,
    UserStatus,
)
from app.db.tenant import TenantContext, set_tenant_context
from app.knowledge.acl import space_permissions, writable
from app.knowledge.dispatch import IngestDispatcher
from app.knowledge.ingest import IngestDeps
from app.knowledge.service import suggest_doc_type
from app.knowledge.storage import document_prefix, object_key
from app.rag.parse import SUPPORTED_MIME, detect_mime

log = get_logger("app.connectors.sync")

MAX_FILES_PER_RUN = 500
MAX_BACKOFF = timedelta(hours=24)
LOCK_TTL_S = 3600


@dataclass
class SyncDeps:
    ingest: IngestDeps
    clients: ConnectorClients
    keyring: Keyring
    dispatcher: IngestDispatcher
    mailer: EmailSender | None = None


@dataclass
class SyncResult:
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    deleted: int = 0
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    truncated: bool = False

    def as_json(self) -> dict[str, Any]:
        return {
            "added": self.added,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "deleted": self.deleted,
            "skipped": self.skipped[:50],
            "failed": self.failed[:50],
            "truncated": self.truncated,
        }


def _now() -> datetime:
    return datetime.now(UTC)


def _lock_key(source_id: uuid.UUID) -> str:
    return f"sync:lock:{source_id}"


async def _connector(deps: SyncDeps, db: AsyncSession, source: ConnectorSource) -> StorageConnector:
    if source.kind == ConnectorKind.URL:
        return build_connector(deps.clients, ConnectorKind.URL, {}, {})
    account = await db.get(ConnectorAccount, source.account_id) if source.account_id else None
    if account is None:
        raise ConnectorError("The connected account was removed; reconnect it and create the source again.")
    return await open_connector(db, deps.keyring, deps.clients, account)


class _Writer:
    """Applies one change at a time, committing after each so progress survives a crash."""

    def __init__(self, deps: SyncDeps, db: AsyncSession, source: ConnectorSource, user_id: uuid.UUID) -> None:
        self.deps = deps
        self.db = db
        self.source = source
        self.user_id = user_id
        self.result = SyncResult()

    async def _commit(self) -> None:
        await self.db.commit()
        await run_after_commit_hooks(self.db)

    def _enqueue(self, document_id: uuid.UUID) -> None:
        dispatcher, org_id, user_id = self.deps.dispatcher, self.source.org_id, self.user_id

        async def enqueue() -> None:
            await dispatcher.dispatch(org_id=org_id, user_id=user_id, document_id=document_id)

        after_commit(self.db, enqueue)

    async def upsert(self, connector: StorageConnector, item: RemoteItem, existing: Document | None) -> None:
        settings = self.deps.ingest.settings
        fetched = await connector.fetch(item, settings.max_upload_mb * 1024 * 1024)
        mime = detect_mime(fetched.data, fetched.filename)
        if mime not in SUPPORTED_MIME:
            self.result.skipped.append(f"{item.path}: unsupported type {mime}")
            return
        sha = hashlib.sha256(fetched.data).hexdigest()
        etag = fetched.etag or item.etag
        if existing is not None and existing.sha256 == sha:
            existing.external_etag = etag
            self.result.unchanged += 1
            await self._commit()
            return
        doc = existing
        if doc is None:
            doc = Document(
                id=uuid.uuid4(),
                org_id=self.source.org_id,
                space_id=self.source.space_id,
                uploader_id=self.user_id,
                source_id=self.source.id,
                external_id=item.id,
                title=fetched.filename.rsplit(".", 1)[0][:300] or fetched.filename[:300],
                filename=fetched.filename[:300],
                source_url=item.path if item.path.startswith(("http://", "https://")) else None,
                doc_type=self.source.doc_type or suggest_doc_type(fetched.filename, "AUTO"),
                version=1,
            )
            self.db.add(doc)
            self.result.added += 1
        else:
            doc.version += 1
            self.result.updated += 1
        extension = fetched.filename.rsplit(".", 1)[-1] if "." in fetched.filename else "bin"
        doc.storage_key = object_key(doc.org_id, doc.space_id, doc.id, doc.version, extension)
        await self.deps.ingest.storage.put(doc.storage_key, io.BytesIO(fetched.data), mime)
        doc.mime, doc.size_bytes, doc.sha256, doc.external_etag = mime, len(fetched.data), sha, etag
        doc.status, doc.error = DocStatus.PENDING, None
        await self.db.flush()
        self._enqueue(doc.id)
        await self._commit()

    async def delete(self, doc: Document) -> None:
        store = await self.deps.ingest.rag.store()
        storage = self.deps.ingest.storage
        org_id, space_id, document_id = doc.org_id, doc.space_id, doc.id

        async def purge() -> None:
            await store.delete_document(org_id, document_id)
            await storage.delete_prefix(document_prefix(org_id, space_id, document_id))

        await record(
            self.db,
            org_id=org_id,
            actor_id=self.user_id,
            action="document.deleted",
            meta=SYSTEM_META,
            target_type="document",
            target_id=document_id,
            detail={"space_id": str(space_id), "source_id": str(self.source.id), "reason": "removed at source"},
        )
        await self.db.delete(doc)
        after_commit(self.db, purge)
        self.result.deleted += 1
        await self._commit()


async def _run(deps: SyncDeps, db: AsyncSession, source: ConnectorSource, user: User) -> SyncResult:
    if user.status != UserStatus.ACTIVE:
        raise ConnectorError("The source's creator is no longer an active member.")
    user_id = user.id
    perms = await space_permissions(db, None, org_id=source.org_id, user_id=user_id)
    if source.space_id not in writable(perms):
        raise ConnectorError("The source's creator can no longer add documents to this space.")
    connector = await _connector(deps, db, source)

    async def load_docs() -> dict[str, Document]:
        rows = await db.scalars(select(Document).where(Document.source_id == source.id))
        return {d.external_id: d for d in rows if d.external_id is not None}

    async def recover() -> dict[str, Document]:
        # A rollback expires every loaded object; reload before touching them again (async sessions cannot
        # lazy-load).
        await db.rollback()
        await db.refresh(source)
        return await load_docs()

    docs = await load_docs()
    known = {external_id: doc.external_etag for external_id, doc in docs.items()}
    changes = await connector.changes(Selection.from_json(source.selection), source.cursor, known)
    writer = _Writer(deps, db, source, user_id)
    for item in changes.upserts[:MAX_FILES_PER_RUN]:
        try:
            await writer.upsert(connector, item, docs.get(item.id))
        except ConnectorError as exc:
            if exc.auth:
                raise
            docs = await recover()
            writer.result.failed.append(f"{item.path}: {exc.detail}")
        except Exception as exc:  # one bad file must not stop the rest of the sync
            log.exception("sync.file_failed", source_id=str(source.id), item=item.path)
            docs = await recover()
            writer.result.failed.append(f"{item.path}: unexpected {type(exc).__name__}")
    for external_id in changes.deleted:
        if external_id in docs:
            await writer.delete(docs[external_id])
    writer.result.truncated = len(changes.upserts) > MAX_FILES_PER_RUN
    if not writer.result.truncated:
        # With a partial run the old cursor is kept, so the remaining changes are seen again next time.
        source.cursor = changes.cursor
    return writer.result


async def _notify(deps: SyncDeps, source: ConnectorSource, email: str, name: str) -> None:
    if deps.mailer is None or not deps.mailer.configured:
        return
    link = f"{deps.ingest.settings.public_app_url.rstrip('/')}/app/knowledge?space={source.space_id}"
    await deps.mailer.send(
        OutgoingEmail(
            to=email,
            subject=f"Sync failed: {source.name}",
            body=(
                f'Hi {name},\n\nKeeping "{source.name}" in sync failed:\n\n  {source.last_error}\n\n'
                f"We will retry automatically with increasing delays. Open {link} to check the source or "
                "reconnect the account.\n"
            ),
        )
    )


async def sync_source(
    deps: SyncDeps, *, org_id: uuid.UUID, user_id: uuid.UUID, source_id: uuid.UUID
) -> SyncResult | None:
    """Run one sync. Returns None when the source is gone, not visible, or already being synced."""
    valkey = deps.ingest.valkey
    if not await valkey.set(_lock_key(source_id), "1", nx=True, ex=LOCK_TTL_S):
        log.info("sync.already_running", source_id=str(source_id))
        return None
    try:
        async with deps.ingest.session_factory() as db:
            await set_tenant_context(db, TenantContext(org_id=org_id, user_id=user_id))
            user = await db.get(User, user_id)
            if user is None:
                return None
            await set_tenant_context(db, TenantContext(org_id=org_id, user_id=user_id, role=user.role.value))
            email, name = user.email, user.name
            source = await db.get(ConnectorSource, source_id)
            if source is None:
                return None
            source.status = SourceStatus.SYNCING
            await db.commit()
            try:
                result = await _run(deps, db, source, user)
            except Exception as exc:
                # Never leave a source stuck in SYNCING: unexpected errors fail the run like any other.
                if isinstance(exc, ConnectorError):
                    error = exc
                else:
                    log.exception("sync.crashed", source_id=str(source_id))
                    error = ConnectorError(f"Sync failed unexpectedly ({type(exc).__name__}); it will be retried.")
                await db.rollback()
                source = await db.get(ConnectorSource, source_id)
                if source is None:
                    return None
                source.failure_count += 1
                source.status = SourceStatus.ERROR
                source.last_error = error.detail
                backoff = min(timedelta(minutes=source.interval_minutes) * 2 ** (source.failure_count - 1), MAX_BACKOFF)
                source.next_sync_at = _now() + backoff if source.sync_enabled else None
                if error.auth and source.account_id:
                    account = await db.get(ConnectorAccount, source.account_id)
                    if account is not None:
                        account.status, account.last_error = AccountStatus.ERROR, error.detail
                await db.commit()
                log.warning("sync.failed", source_id=str(source_id), error=error.detail, failures=source.failure_count)
                if source.failure_count == 1:
                    await _notify(deps, source, email, name)
                return SyncResult(failed=[error.detail or "sync failed"])
            source.status = SourceStatus.IDLE
            source.last_error = None
            source.failure_count = 0
            source.last_sync_at = _now()
            source.next_sync_at = _now() + timedelta(minutes=source.interval_minutes) if source.sync_enabled else None
            source.last_result = result.as_json()
            await db.commit()
            log.info(
                "sync.done",
                source_id=str(source_id),
                **{k: v for k, v in result.as_json().items() if isinstance(v, int)},
            )
            return result
    finally:
        await valkey.delete(_lock_key(source_id))


def connector_clients_for(settings: Settings, web_http: httpx2.AsyncClient) -> ConnectorClients:
    """Clients for connectors outside the API process (workers)."""
    return ConnectorClients(
        settings=settings,
        provider_http=httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)),
        web_http=web_http,
    )
