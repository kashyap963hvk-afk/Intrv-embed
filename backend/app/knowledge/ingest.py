"""Ingestion pipeline: original file → parse → risk scan → chunk → embed (dense + BM25) → Qdrant + Postgres.

Runs inside the worker *as the uploading user* (their tenant context and space permissions apply). Reindexing
swaps versions: new points are written first, then the document's other points are deleted.
"""

from __future__ import annotations

import asyncio
import io
import uuid
from dataclasses import dataclass

import httpx2
from redis.asyncio import Redis
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.ssrf import UnsafeURLError
from app.db.models import ChunkKind, DocStatus, Document, DocumentChunk, User
from app.db.tenant import TenantContext, set_tenant_context
from app.knowledge.events import publish_document_status
from app.knowledge.storage import ObjectStorage, object_key
from app.rag.chunk import chunk_document, embedding_text
from app.rag.engine import RagEngine
from app.rag.parse import ParseError, detect_mime, parse_document
from app.rag.sparse import encode_document
from app.rag.store import ChunkPoint

log = get_logger("app.ingest")

MAX_URL_BYTES = 25 * 1024 * 1024


@dataclass
class IngestDeps:
    settings: Settings
    session_factory: async_sessionmaker[AsyncSession]
    valkey: Redis
    storage: ObjectStorage
    rag: RagEngine
    public_http: httpx2.AsyncClient


class IngestError(Exception):
    """A user-facing reason the document could not be ingested."""


async def _set_status(
    deps: IngestDeps, db: AsyncSession, doc: Document, status: DocStatus, error: str | None = None
) -> None:
    doc.status = status
    doc.error = error
    await db.commit()
    await publish_document_status(
        deps.valkey, org_id=doc.org_id, space_id=doc.space_id, document_id=doc.id, status=status.value, error=error
    )


async def _fetch_url(deps: IngestDeps, url: str) -> tuple[bytes, str | None]:
    try:
        async with deps.public_http.stream("GET", url, follow_redirects=False, timeout=30) as response:
            if response.status_code in {301, 302, 303, 307, 308}:
                raise IngestError("The URL redirects; paste the final address instead.")
            if response.status_code >= 400:  # noqa: PLR2004
                raise IngestError(f"The URL returned HTTP {response.status_code}.")
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_URL_BYTES:
                    raise IngestError("The page is larger than 25 MB.")
                chunks.append(chunk)
            return b"".join(chunks), response.headers.get("content-type")
    except UnsafeURLError as exc:
        raise IngestError(exc.detail or "This URL is not allowed.") from exc
    except httpx2.HTTPError as exc:
        raise IngestError(f"Could not download the URL ({type(exc).__name__}).") from exc


async def _load_bytes(deps: IngestDeps, db: AsyncSession, doc: Document) -> bytes:
    if doc.storage_key:
        return await deps.storage.get(doc.storage_key)
    if not doc.source_url:
        raise IngestError("The document has no content.")
    data, content_type = await _fetch_url(deps, doc.source_url)
    filename = doc.filename or "page.html"
    doc.mime = detect_mime(data, filename) if not content_type or "html" not in content_type else "text/html"
    doc.size_bytes = len(data)
    extension = filename.rsplit(".", 1)[-1] if "." in filename else ("html" if doc.mime == "text/html" else "bin")
    doc.storage_key = object_key(doc.org_id, doc.space_id, doc.id, doc.version, extension)
    await deps.storage.put(doc.storage_key, io.BytesIO(data), doc.mime)
    await db.flush()
    return data


async def _index(deps: IngestDeps, db: AsyncSession, doc: Document, data: bytes) -> None:
    settings = deps.settings
    try:
        parsed = await asyncio.wait_for(
            asyncio.to_thread(
                parse_document,
                data,
                doc.mime or detect_mime(data, doc.filename),
                doc.filename or doc.title,
                backend=settings.parser_backend,
                max_pages=settings.max_pages,
            ),
            timeout=settings.ingest_timeout_s,
        )
    except TimeoutError as exc:
        raise IngestError(f"Parsing took longer than {settings.ingest_timeout_s}s.") from exc
    except ParseError as exc:
        raise IngestError(str(exc)) from exc

    doc.page_count = len(parsed.pages)
    doc.risk_flags = parsed.risk_flags()
    doc.parse_meta = {**parsed.meta, "ocr_pages": parsed.ocr_pages}
    if parsed.title and doc.title == doc.filename:
        doc.title = parsed.title[:300]
    parents = chunk_document(parsed, doc.doc_type)
    if not parents:
        raise IngestError("The document contains no text to index.")
    await _set_status(deps, db, doc, DocStatus.EMBEDDING)

    rows: list[DocumentChunk] = []
    children: list[tuple[DocumentChunk, str]] = []
    for p_ord, parent in enumerate(parents):
        parent_row = DocumentChunk(
            id=uuid.uuid4(),
            org_id=doc.org_id,
            space_id=doc.space_id,
            document_id=doc.id,
            kind=ChunkKind.PARENT,
            ord=p_ord,
            text=parent.text,
            section_path=parent.section_path,
            page_from=parent.page_from,
            page_to=parent.page_to,
            token_count=parent.token_count,
        )
        rows.append(parent_row)
        for c_ord, child in enumerate(parent.children):
            child_row = DocumentChunk(
                id=uuid.uuid4(),
                org_id=doc.org_id,
                space_id=doc.space_id,
                document_id=doc.id,
                parent_id=parent_row.id,
                kind=ChunkKind.CHILD,
                ord=c_ord,
                text=child.text,
                section_path=parent.section_path,
                page_from=child.page_from,
                page_to=child.page_to,
                token_count=child.token_count,
            )
            rows.append(child_row)
            children.append((child_row, embedding_text(doc.title, parent.section_path, child.text)))

    vectors = await deps.rag.embedder.embed([text for _, text in children])
    store = await deps.rag.store()
    points = [
        ChunkPoint(
            chunk_id=row.id,
            parent_id=row.parent_id or row.id,
            org_id=doc.org_id,
            space_id=doc.space_id,
            document_id=doc.id,
            doc_type=doc.doc_type.value,
            dense=vector,
            sparse=encode_document(text),
        )
        for (row, text), vector in zip(children, vectors, strict=True)
    ]
    await store.upsert(points)
    # Swap: drop the previous version's rows and points only after the new ones exist.
    await db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == doc.id))
    db.add_all(rows)
    doc.chunk_count = len(children)
    await db.flush()
    await store.delete_document(doc.org_id, doc.id, keep=[row.id for row, _ in children])


async def ingest_document(
    deps: IngestDeps, *, org_id: uuid.UUID, user_id: uuid.UUID, document_id: uuid.UUID
) -> DocStatus | None:
    """Process one document. Returns the final status, or None when the document is gone or not accessible."""
    async with deps.session_factory() as db:
        await set_tenant_context(db, TenantContext(org_id=org_id, user_id=user_id))
        user = await db.get(User, user_id)
        if user is None:
            log.warning("ingest.uploader_missing", document_id=str(document_id))
            return None
        await set_tenant_context(db, TenantContext(org_id=org_id, user_id=user_id, role=user.role.value))
        doc = await db.get(Document, document_id)
        if doc is None or doc.status == DocStatus.DELETING:
            log.info("ingest.document_unavailable", document_id=str(document_id))
            return None
        try:
            await _set_status(deps, db, doc, DocStatus.PARSING)
            data = await _load_bytes(deps, db, doc)
            await _index(deps, db, doc, data)
            await _set_status(deps, db, doc, DocStatus.READY)
            log.info("ingest.ready", document_id=str(doc.id), chunks=doc.chunk_count, pages=doc.page_count)
        except IngestError as exc:
            await db.rollback()
            doc = await db.get(Document, document_id)
            if doc is None:
                return None
            await _set_status(deps, db, doc, DocStatus.FAILED, str(exc))
        except Exception:
            log.exception("ingest.failed", document_id=str(document_id))
            await db.rollback()
            doc = await db.get(Document, document_id)
            if doc is None:
                return None
            await _set_status(deps, db, doc, DocStatus.FAILED, "Processing failed unexpectedly; try reindexing.")
        return doc.status
