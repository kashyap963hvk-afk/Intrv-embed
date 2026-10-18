"""Knowledge spaces, documents, search and live events (/api/spaces, /api/documents, /api/search, /api/events)."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.audit.service import ClientMeta, record
from app.auth.deps import ActiveMember, Owner, SettingsDep, ValkeyDep, csrf_protect
from app.auth.principal import Principal
from app.core.errors import AppError, NotFoundError
from app.core.resources import Resources
from app.core.ssrf import UnsafeURLError, validate_url
from app.db.deps import DbSession, after_commit
from app.db.models import DocStatus, DocType, Document, Permission
from app.db.tenant import TenantContext, set_tenant_context
from app.knowledge import service
from app.knowledge.acl import acl_version, readable, space_permissions
from app.knowledge.deps import ResourcesDep
from app.knowledge.events import org_channel
from app.knowledge.schemas import (
    CitationOut,
    DocTypeChoice,
    DocumentOut,
    DownloadOut,
    PasteIn,
    RejectedFile,
    SearchHit,
    SearchIn,
    SearchOut,
    SelfTestCheck,
    SelfTestOut,
    SpaceIn,
    SpaceOut,
    UploadResult,
    UrlIn,
)
from app.knowledge.service import IncomingFile
from app.rag.parse import SUPPORTED_MIME, detect_mime
from app.rag.retrieve import RetrievalRequest
from app.rag.selftest import run_selftest

router = APIRouter(tags=["knowledge"], dependencies=[Depends(csrf_protect)])

_EVENT_PERMISSION_REFRESH_S = 30
_EVENT_HEARTBEAT_S = 15


def _meta(request: Request) -> ClientMeta:
    return ClientMeta.from_request(request)


Meta = Annotated[ClientMeta, Depends(_meta)]


# --- spaces --------------------------------------------------------------------------------------------------


@router.get("/api/spaces", response_model=list[SpaceOut])
async def list_spaces(principal: ActiveMember, db: DbSession, valkey: ValkeyDep) -> list[SpaceOut]:
    return await service.list_spaces(db, valkey, principal)


@router.post("/api/spaces", response_model=SpaceOut, status_code=status.HTTP_201_CREATED)
async def create_space(body: SpaceIn, principal: ActiveMember, db: DbSession, valkey: ValkeyDep) -> SpaceOut:
    space = await service.create_personal_space(db, valkey, principal, body.name, body.description)
    return SpaceOut(
        id=space.id,
        kind=space.kind,
        name=space.name,
        description=space.description,
        permission=Permission.CONTRIBUTE,
        document_count=0,
        is_mine=True,
    )


@router.get("/api/spaces/{space_id}/documents", response_model=list[DocumentOut])
async def list_documents(
    space_id: uuid.UUID, principal: ActiveMember, db: DbSession, valkey: ValkeyDep
) -> list[DocumentOut]:
    await service.require_space(db, valkey, principal, space_id, write=False)
    docs = await db.scalars(
        select(Document).where(Document.space_id == space_id).order_by(Document.created_at.desc()).limit(1000)
    )
    return [service.document_out(d, principal) for d in docs]


# --- adding documents ("+" button) --------------------------------------------------------------------------


@router.post("/api/spaces/{space_id}/documents", response_model=UploadResult, status_code=status.HTTP_201_CREATED)
async def upload_documents(
    space_id: uuid.UUID,
    principal: ActiveMember,
    db: DbSession,
    valkey: ValkeyDep,
    settings: SettingsDep,
    resources: ResourcesDep,
    meta: Meta,
    files: Annotated[list[UploadFile], File(description="One or more files (zip archives are unpacked)")],
    doc_type: Annotated[DocTypeChoice, Form()] = "AUTO",
) -> UploadResult:
    space = await service.require_space(db, valkey, principal, space_id, write=True)
    max_bytes = settings.max_upload_mb * 1024 * 1024
    result = UploadResult(documents=[], duplicates=[], rejected=[])
    for upload in files:
        name = (upload.filename or "upload").rsplit("/", 1)[-1][:300]
        try:
            data = await asyncio.to_thread(service.read_limited, upload.file, max_bytes)
            mime = detect_mime(data, name)
            incoming = service.expand_zip(name, data) if mime == "application/zip" else [IncomingFile(name, data, mime)]
        except service.FileTooLargeError as exc:
            result.rejected.append(RejectedFile(filename=name, reason=exc.detail or exc.title))
            continue
        except AppError as exc:
            result.rejected.append(RejectedFile(filename=name, reason=exc.detail or exc.title))
            continue
        for item in incoming:
            if item.mime not in SUPPORTED_MIME:
                result.rejected.append(
                    RejectedFile(filename=item.filename, reason=f"Unsupported file type ({item.mime}).")
                )
                continue
            doc, created = await service.create_document(
                db, resources.storage, resources.dispatcher, principal, space, item, doc_type, meta
            )
            (result.documents if created else result.duplicates).append(service.document_out(doc, principal))
    return result


@router.post("/api/spaces/{space_id}/documents/paste", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
async def paste_document(
    space_id: uuid.UUID,
    body: PasteIn,
    principal: ActiveMember,
    db: DbSession,
    valkey: ValkeyDep,
    resources: ResourcesDep,
    meta: Meta,
) -> DocumentOut:
    space = await service.require_space(db, valkey, principal, space_id, write=True)
    filename = f"{body.title[:200]}.md"
    item = IncomingFile(filename, body.text.encode(), "text/markdown")
    doc, _ = await service.create_document(
        db, resources.storage, resources.dispatcher, principal, space, item, body.doc_type, meta
    )
    doc.title = body.title
    return service.document_out(doc, principal)


@router.post("/api/spaces/{space_id}/documents/url", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
async def add_url(
    space_id: uuid.UUID,
    body: UrlIn,
    principal: ActiveMember,
    db: DbSession,
    valkey: ValkeyDep,
    settings: SettingsDep,
    resources: ResourcesDep,
    meta: Meta,
) -> DocumentOut:
    space = await service.require_space(db, valkey, principal, space_id, write=True)
    try:
        url = validate_url(
            body.url, allow_private=settings.allow_private_llm_endpoints, allowed_schemes=("https", "http")
        )
    except UnsafeURLError as exc:
        raise AppError(exc.detail) from exc
    doc = await service.create_url_document(
        db, resources.dispatcher, principal, space, url, body.title, body.doc_type, meta
    )
    return service.document_out(doc, principal)


# --- documents ---------------------------------------------------------------------------------------------------


@router.get("/api/documents/{document_id}", response_model=DocumentOut)
async def get_document(document_id: uuid.UUID, principal: ActiveMember, db: DbSession) -> DocumentOut:
    return service.document_out(await service.get_document(db, document_id), principal)


@router.get("/api/documents/{document_id}/download", response_model=DownloadOut)
async def download_document(
    document_id: uuid.UUID, principal: ActiveMember, db: DbSession, resources: ResourcesDep, meta: Meta
) -> DownloadOut:
    doc = await service.get_document(db, document_id)
    if not doc.storage_key:
        raise NotFoundError("This document has no stored file yet.")
    expires = 300
    url = await resources.storage.presign_get(doc.storage_key, filename=doc.filename or doc.title, expires_s=expires)
    await record(
        db,
        org_id=principal.org_id,
        actor_id=principal.user_id,
        action="document.downloaded",
        meta=meta,
        target_type="document",
        target_id=doc.id,
    )
    return DownloadOut(url=url, expires_in_s=expires)


@router.post("/api/documents/{document_id}/reindex", response_model=DocumentOut)
async def reindex_document(
    document_id: uuid.UUID,
    principal: ActiveMember,
    db: DbSession,
    valkey: ValkeyDep,
    resources: ResourcesDep,
) -> DocumentOut:
    doc = await service.get_document(db, document_id)
    await service.require_space(db, valkey, principal, doc.space_id, write=True)
    doc.status = DocStatus.PENDING
    doc.error = None
    org_id, user_id = principal.org_id, principal.user_id

    async def enqueue() -> None:
        await resources.dispatcher.dispatch(org_id=org_id, user_id=user_id, document_id=document_id)

    after_commit(db, enqueue)
    await db.flush()
    return service.document_out(doc, principal)


@router.delete("/api/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID,
    principal: ActiveMember,
    db: DbSession,
    valkey: ValkeyDep,
    resources: ResourcesDep,
    meta: Meta,
) -> None:
    doc = await service.get_document(db, document_id)
    await service.delete_document(db, valkey, resources.storage, await resources.rag.store(), principal, doc, meta)


# --- search ------------------------------------------------------------------------------------------------------


@router.post("/api/search", response_model=SearchOut, summary="Hybrid search over the knowledge you can read")
async def search(
    body: SearchIn, principal: ActiveMember, db: DbSession, valkey: ValkeyDep, resources: ResourcesDep
) -> SearchOut:
    perms = await space_permissions(db, valkey, org_id=principal.org_id, user_id=principal.user_id)
    spaces = readable(perms) if body.space_ids is None else readable(perms) & set(body.space_ids)
    retriever = await resources.rag.retriever()
    result = await retriever.retrieve(
        db,
        principal.org_id,
        RetrievalRequest(
            query=body.query,
            space_ids=sorted(spaces),
            doc_types=[d.value for d in body.doc_types] if body.doc_types else None,
            final=body.k,
            rerank=body.rerank,
        ),
    )
    return SearchOut(
        results=[
            SearchHit(
                text=chunk.text,
                matched_text=chunk.matched_text,
                doc_type=DocType(chunk.doc_type),
                citation=CitationOut(
                    document_id=chunk.citation.document_id,
                    title=chunk.citation.title,
                    section_path=chunk.citation.section_path,
                    page_from=chunk.citation.page_from,
                    page_to=chunk.citation.page_to,
                ),
                fused_rank=chunk.fused_rank,
                rerank_score=chunk.rerank_score,
            )
            for chunk in result.chunks
        ],
        reranked=result.reranked,
        searched_spaces=len(spaces),
    )


# --- live events (SSE) --------------------------------------------------------------------------------------------


async def _readable_now(resources: Resources, principal: Principal) -> set[uuid.UUID]:
    async with resources.session_factory() as db:
        await set_tenant_context(
            db, TenantContext(org_id=principal.org_id, user_id=principal.user_id, role=principal.role.value)
        )
        perms = await space_permissions(db, resources.valkey, org_id=principal.org_id, user_id=principal.user_id)
        return readable(perms)


@router.get("/api/events", summary="Server-sent events: live document status for spaces you can read")
async def events(request: Request, principal: ActiveMember, resources: ResourcesDep) -> StreamingResponse:
    async def stream() -> AsyncIterator[str]:
        pubsub = resources.valkey.pubsub()
        await pubsub.subscribe(org_channel(principal.org_id))
        version = await acl_version(resources.valkey, principal.org_id)
        allowed = await _readable_now(resources, principal)
        refreshed = time.monotonic()
        try:
            yield "retry: 5000\n\n"
            while not await request.is_disconnected():
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=_EVENT_HEARTBEAT_S)
                # Re-read permissions as soon as any grant, group or space changes (and periodically for expiry).
                current = await acl_version(resources.valkey, principal.org_id)
                if current != version or time.monotonic() - refreshed > _EVENT_PERMISSION_REFRESH_S:
                    version = current
                    allowed = await _readable_now(resources, principal)
                    refreshed = time.monotonic()
                if message is None:
                    yield ": keep-alive\n\n"
                    continue
                event = json.loads(message["data"])
                if uuid.UUID(str(event.get("space_id"))) in allowed:
                    yield f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
        finally:
            await pubsub.unsubscribe()
            await pubsub.aclose()  # type: ignore[no-untyped-call]

    return StreamingResponse(
        stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


# --- self test ------------------------------------------------------------------------------------------------------


@router.get("/api/system/selftest", response_model=SelfTestOut, summary="Check models, vector store and parsers")
async def selftest(_owner: Owner, resources: ResourcesDep) -> SelfTestOut:
    checks = [SelfTestCheck(name=c.name, ok=c.ok, detail=c.detail) for c in await run_selftest(resources.rag)]
    return SelfTestOut(ok=all(c.ok for c in checks), checks=checks)
