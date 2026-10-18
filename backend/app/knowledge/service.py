"""Knowledge spaces and documents: creation, listing, deletion. RLS confines every query; the explicit
permission checks here produce clear errors (403 vs 404) and guard writes."""

from __future__ import annotations

import hashlib
import io
import re
import uuid
import zipfile
from dataclasses import dataclass
from typing import BinaryIO

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import ClientMeta, record
from app.auth.principal import Principal
from app.core.errors import AppError, ConflictError, ForbiddenError, NotFoundError
from app.db.deps import after_commit
from app.db.models import DocStatus, DocType, Document, KnowledgeSpace, Permission, SpaceKind
from app.knowledge.acl import bump_acl_version, readable, space_permissions, writable
from app.knowledge.dispatch import IngestDispatcher
from app.knowledge.schemas import DocTypeChoice, DocumentOut, SpaceOut
from app.knowledge.storage import ObjectStorage, document_prefix, object_key
from app.rag.parse import SUPPORTED_MIME, detect_mime
from app.rag.store import VectorStore

MAX_PERSONAL_SPACES = 10
MAX_ZIP_MEMBERS = 200
MAX_ZIP_TOTAL_BYTES = 500 * 1024 * 1024
MAX_ZIP_RATIO = 100
_OFFICE_ZIP_MIMES = {m for m in SUPPORTED_MIME if "openxmlformats" in m}


class FileTooLargeError(AppError):
    status_code = 413
    slug = "file-too-large"
    title = "File too large"


@dataclass
class IncomingFile:
    filename: str
    data: bytes
    mime: str


def suggest_doc_type(filename: str, choice: DocTypeChoice) -> DocType:
    if choice != "AUTO":
        return DocType(choice)
    name = filename.lower()
    rules = (
        (r"\b(cv|resume|résumé|curriculum)\b|_cv|cv_|-cv|cv-", DocType.CV),
        (r"\b(jd|job[ _-]?description|job[ _-]?posting|opening)\b", DocType.JD),
        (r"rubric|scorecard|competenc", DocType.RUBRIC),
        (r"question|interview[ _-]?bank", DocType.QUESTION_BANK),
        (r"datasheet|reference[ _-]?manual|\brm\d{3,4}\b|errata|\bds\d{3,5}\b|user[ _-]?manual", DocType.DATASHEET),
        (r"\.(c|h|cpp|hpp|py|rs|ino)$", DocType.CODE),
    )
    for pattern, doc_type in rules:
        if re.search(pattern, name):
            return doc_type
    return DocType.KNOWLEDGE


def document_out(doc: Document, principal: Principal) -> DocumentOut:
    return DocumentOut(
        id=doc.id,
        space_id=doc.space_id,
        title=doc.title,
        filename=doc.filename,
        source_url=doc.source_url,
        mime=doc.mime,
        size_bytes=doc.size_bytes,
        doc_type=doc.doc_type,
        status=doc.status,
        error=doc.error,
        page_count=doc.page_count,
        chunk_count=doc.chunk_count,
        risk_flags=doc.risk_flags or {},
        uploaded_by_me=doc.uploader_id == principal.user_id,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


async def permissions(db: AsyncSession, valkey: Redis, principal: Principal) -> dict[uuid.UUID, Permission]:
    return await space_permissions(db, valkey, org_id=principal.org_id, user_id=principal.user_id)


async def require_space(
    db: AsyncSession, valkey: Redis, principal: Principal, space_id: uuid.UUID, *, write: bool
) -> KnowledgeSpace:
    perms = await permissions(db, valkey, principal)
    if space_id not in readable(perms):
        raise NotFoundError("Knowledge space not found.")
    if write and space_id not in writable(perms):
        raise ForbiddenError("You can read this space but not add to it. Ask an owner for contribute access.")
    space = await db.get(KnowledgeSpace, space_id)
    if space is None:
        raise NotFoundError("Knowledge space not found.")
    return space


def _invalidate_acl_after_commit(db: AsyncSession, valkey: Redis, org_id: uuid.UUID) -> None:
    async def bump() -> None:
        await bump_acl_version(valkey, org_id)

    after_commit(db, bump)


async def ensure_personal_space(db: AsyncSession, valkey: Redis, principal: Principal) -> None:
    exists = await db.scalar(
        select(KnowledgeSpace.id).where(
            KnowledgeSpace.kind == SpaceKind.PERSONAL, KnowledgeSpace.owner_user_id == principal.user_id
        )
    )
    if exists is None:
        db.add(
            KnowledgeSpace(
                org_id=principal.org_id,
                kind=SpaceKind.PERSONAL,
                owner_user_id=principal.user_id,
                name="My documents",
                description="Private to you: CVs, notes and anything you do not want to share.",
            )
        )
        await db.flush()
        _invalidate_acl_after_commit(db, valkey, principal.org_id)


async def list_spaces(db: AsyncSession, valkey: Redis, principal: Principal) -> list[SpaceOut]:
    await ensure_personal_space(db, valkey, principal)
    perms = await space_permissions(db, None, org_id=principal.org_id, user_id=principal.user_id)
    spaces = list(await db.scalars(select(KnowledgeSpace).where(KnowledgeSpace.id.in_(list(perms)))))
    counts = dict(
        (
            await db.execute(
                select(Document.space_id, func.count())
                .where(Document.space_id.in_(list(perms)))
                .group_by(Document.space_id)
            )
        ).all()
    )
    spaces.sort(key=lambda s: (s.kind != SpaceKind.PERSONAL, s.name.lower()))
    return [
        SpaceOut(
            id=s.id,
            kind=s.kind,
            name=s.name,
            description=s.description,
            permission=perms[s.id],
            document_count=int(counts.get(s.id, 0)),
            is_mine=s.owner_user_id == principal.user_id,
        )
        for s in spaces
    ]


async def create_personal_space(
    db: AsyncSession, valkey: Redis, principal: Principal, name: str, description: str
) -> KnowledgeSpace:
    owned = await db.scalar(
        select(func.count()).select_from(KnowledgeSpace).where(KnowledgeSpace.owner_user_id == principal.user_id)
    )
    if (owned or 0) >= MAX_PERSONAL_SPACES:
        raise ConflictError(f"You can have at most {MAX_PERSONAL_SPACES} personal spaces.")
    space = KnowledgeSpace(
        org_id=principal.org_id,
        kind=SpaceKind.PERSONAL,
        owner_user_id=principal.user_id,
        name=name.strip(),
        description=description,
    )
    db.add(space)
    await db.flush()
    _invalidate_acl_after_commit(db, valkey, principal.org_id)
    return space


# --- documents ---------------------------------------------------------------------------------------------


def read_limited(stream: BinaryIO, max_bytes: int) -> bytes:
    buffer = io.BytesIO()
    while chunk := stream.read(1024 * 1024):
        buffer.write(chunk)
        if buffer.tell() > max_bytes:
            raise FileTooLargeError(f"Files may be at most {max_bytes // (1024 * 1024)} MB.")
    return buffer.getvalue()


def expand_zip(filename: str, data: bytes) -> list[IncomingFile]:
    """Unpack a .zip upload into its files, refusing zip bombs and nested archives."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise AppError(f"{filename}: not a valid zip file.") from exc
    members = [m for m in archive.infolist() if not m.is_dir() and not m.filename.startswith(("__MACOSX/", "."))]
    if len(members) > MAX_ZIP_MEMBERS:
        raise AppError(f"{filename}: more than {MAX_ZIP_MEMBERS} files.")
    total = sum(m.file_size for m in members)
    if total > MAX_ZIP_TOTAL_BYTES or total > max(1, len(data)) * MAX_ZIP_RATIO:
        raise AppError(f"{filename}: the archive expands too much to be processed safely.")
    files: list[IncomingFile] = []
    for member in members:
        name = member.filename.rsplit("/", 1)[-1]
        content = archive.read(member)
        mime = detect_mime(content, name)
        if mime == "application/zip":
            continue  # no nested archives
        files.append(IncomingFile(name, content, mime))
    return files


async def create_document(
    db: AsyncSession,
    storage: ObjectStorage,
    dispatcher: IngestDispatcher,
    principal: Principal,
    space: KnowledgeSpace,
    incoming: IncomingFile,
    doc_type: DocTypeChoice,
    meta: ClientMeta,
) -> tuple[Document, bool]:
    """Store the original and queue ingestion. Returns (document, created) where False means duplicate."""
    digest = hashlib.sha256(incoming.data).hexdigest()
    duplicate = await db.scalar(
        select(Document).where(
            Document.space_id == space.id, Document.sha256 == digest, Document.status != DocStatus.FAILED
        )
    )
    if duplicate is not None:
        return duplicate, False
    document_id = uuid.uuid4()
    extension = incoming.filename.rsplit(".", 1)[-1] if "." in incoming.filename else "bin"
    key = object_key(principal.org_id, space.id, document_id, 1, extension)
    await storage.put(key, io.BytesIO(incoming.data), incoming.mime)
    title = incoming.filename.rsplit(".", 1)[0] if "." in incoming.filename else incoming.filename
    doc = Document(
        id=document_id,
        org_id=principal.org_id,
        space_id=space.id,
        uploader_id=principal.user_id,
        title=title[:300] or "Untitled",
        filename=incoming.filename[:300],
        mime=incoming.mime,
        size_bytes=len(incoming.data),
        sha256=digest,
        doc_type=suggest_doc_type(incoming.filename, doc_type),
        storage_key=key,
    )
    db.add(doc)
    await db.flush()
    await record(
        db,
        org_id=principal.org_id,
        actor_id=principal.user_id,
        action="document.uploaded",
        meta=meta,
        target_type="document",
        target_id=doc.id,
        detail={"space_id": str(space.id), "doc_type": doc.doc_type.value, "size": doc.size_bytes},
    )

    async def enqueue() -> None:
        await dispatcher.dispatch(org_id=principal.org_id, user_id=principal.user_id, document_id=document_id)

    after_commit(db, enqueue)
    return doc, True


async def create_url_document(
    db: AsyncSession,
    dispatcher: IngestDispatcher,
    principal: Principal,
    space: KnowledgeSpace,
    url: str,
    title: str | None,
    doc_type: DocTypeChoice,
    meta: ClientMeta,
) -> Document:
    doc = Document(
        id=uuid.uuid4(),
        org_id=principal.org_id,
        space_id=space.id,
        uploader_id=principal.user_id,
        title=(title or url)[:300],
        filename=None,
        source_url=url,
        doc_type=suggest_doc_type(url, doc_type),
    )
    db.add(doc)
    await db.flush()
    await record(
        db,
        org_id=principal.org_id,
        actor_id=principal.user_id,
        action="document.url_added",
        meta=meta,
        target_type="document",
        target_id=doc.id,
        detail={"space_id": str(space.id)},
    )
    document_id = doc.id

    async def enqueue() -> None:
        await dispatcher.dispatch(org_id=principal.org_id, user_id=principal.user_id, document_id=document_id)

    after_commit(db, enqueue)
    return doc


async def get_document(db: AsyncSession, document_id: uuid.UUID) -> Document:
    doc = await db.get(Document, document_id)  # RLS: only documents in readable spaces exist for the caller
    if doc is None:
        raise NotFoundError("Document not found.")
    return doc


async def delete_document(
    db: AsyncSession,
    valkey: Redis,
    storage: ObjectStorage,
    store: VectorStore,
    principal: Principal,
    doc: Document,
    meta: ClientMeta,
) -> None:
    await require_space(db, valkey, principal, doc.space_id, write=True)
    doc.status = DocStatus.DELETING  # excluded from retrieval immediately
    await db.flush()
    org_id, space_id, document_id = doc.org_id, doc.space_id, doc.id

    async def purge() -> None:
        # Vectors and files go only once the row is gone; retrieval re-checks rows, so nothing leaks meanwhile.
        await store.delete_document(org_id, document_id)
        await storage.delete_prefix(document_prefix(org_id, space_id, document_id))

    after_commit(db, purge)
    await record(
        db,
        org_id=principal.org_id,
        actor_id=principal.user_id,
        action="document.deleted",
        meta=meta,
        target_type="document",
        target_id=doc.id,
        detail={"space_id": str(doc.space_id)},
    )
    await db.delete(doc)
