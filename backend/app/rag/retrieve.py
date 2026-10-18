"""Hybrid retrieval with access control enforced twice.

1. Qdrant is queried only within the caller's readable spaces (org + space filter).
2. Every hit is re-loaded from Postgres under Row-Level Security; a point that escaped the Qdrant filter is
   simply not visible there and is dropped. Text never comes from Qdrant.
Then: dedupe to parents → optional cross-encoder rerank → pack into a token budget with citations.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import ChunkKind, DocStatus, Document, DocumentChunk
from app.rag.embed import DenseEmbedder
from app.rag.rerank import Reranker, RerankError
from app.rag.sparse import encode_identifiers, encode_query
from app.rag.store import VectorStore
from app.rag.text import approx_tokens

log = get_logger("app.rag.retrieve")

_RERANK_CHARS = 8000  # ~2k tokens of each parent goes to the cross-encoder


@dataclass(frozen=True, slots=True)
class Citation:
    document_id: uuid.UUID
    title: str
    section_path: list[str]
    page_from: int | None
    page_to: int | None


@dataclass(slots=True)
class RetrievedChunk:
    parent_id: uuid.UUID
    document_id: uuid.UUID
    space_id: uuid.UUID
    doc_type: str
    text: str
    matched_text: str
    citation: Citation
    fused_rank: int
    rerank_score: float | None = None
    token_count: int = 0


@dataclass(frozen=True, slots=True)
class RetrievalRequest:
    query: str
    space_ids: Sequence[uuid.UUID]
    doc_types: Sequence[str] | None = None
    document_ids: Sequence[uuid.UUID] | None = None
    prefetch: int = 40
    fused: int = 30
    final: int = 6
    rerank: bool = True
    token_budget: int = 2500


@dataclass(slots=True)
class RetrievalResult:
    chunks: list[RetrievedChunk] = field(default_factory=list)
    dropped_by_access_check: int = 0
    reranked: bool = False


class Retriever:
    def __init__(self, store: VectorStore, embedder: DenseEmbedder, reranker: Reranker) -> None:
        self.store = store
        self.embedder = embedder
        self.reranker = reranker

    async def retrieve(self, db: AsyncSession, org_id: uuid.UUID, request: RetrievalRequest) -> RetrievalResult:
        result = RetrievalResult()
        if not request.space_ids or not request.query.strip():
            return result
        dense = (await self.embedder.embed([request.query]))[0]
        hits = await self.store.search(
            org_id=org_id,
            space_ids=request.space_ids,
            dense=dense,
            sparse=encode_query(request.query),
            identifier_sparse=encode_identifiers(request.query),
            doc_types=request.doc_types,
            document_ids=request.document_ids,
            prefetch_limit=request.prefetch,
            limit=request.fused,
        )
        if not hits:
            return result

        # Re-load through RLS; also only READY documents in the requested spaces count.
        child_ids = [h.chunk_id for h in hits]
        rows = (
            await db.execute(
                select(DocumentChunk, Document)
                .join(Document, Document.id == DocumentChunk.document_id)
                .where(
                    DocumentChunk.id.in_(child_ids),
                    DocumentChunk.kind == ChunkKind.CHILD,
                    Document.status == DocStatus.READY,
                    Document.space_id.in_(list(request.space_ids)),
                )
            )
        ).all()
        visible = {chunk.id: (chunk, document) for chunk, document in rows}
        result.dropped_by_access_check = len(hits) - len(visible)
        if result.dropped_by_access_check:
            log.warning("rag.hits_dropped_by_access_check", count=result.dropped_by_access_check, org_id=str(org_id))

        parent_ids: list[uuid.UUID] = []
        matched: dict[uuid.UUID, str] = {}
        documents: dict[uuid.UUID, Document] = {}
        for hit in hits:
            entry = visible.get(hit.chunk_id)
            if entry is None:
                continue
            chunk, document = entry
            if chunk.parent_id and chunk.parent_id not in matched:
                parent_ids.append(chunk.parent_id)
                matched[chunk.parent_id] = chunk.text
                documents[chunk.parent_id] = document
        parents = {p.id: p for p in await db.scalars(select(DocumentChunk).where(DocumentChunk.id.in_(parent_ids)))}
        candidates = [
            RetrievedChunk(
                parent_id=pid,
                document_id=documents[pid].id,
                space_id=documents[pid].space_id,
                doc_type=documents[pid].doc_type.value,
                text=parents[pid].text,
                matched_text=matched[pid],
                citation=Citation(
                    document_id=documents[pid].id,
                    title=documents[pid].title,
                    section_path=list(parents[pid].section_path or []),
                    page_from=parents[pid].page_from,
                    page_to=parents[pid].page_to,
                ),
                fused_rank=rank,
                token_count=parents[pid].token_count or approx_tokens(parents[pid].text),
            )
            for rank, pid in enumerate(parent_ids)
            if pid in parents
        ]

        if request.rerank and self.reranker.enabled and len(candidates) > 1:
            try:
                scores = await self.reranker.scores(request.query, [c.text[:_RERANK_CHARS] for c in candidates])
            except RerankError as exc:
                log.warning("rag.rerank_failed", error=str(exc))  # degrade to fused order rather than fail
            else:
                for candidate, score in zip(candidates, scores, strict=True):
                    candidate.rerank_score = score
                candidates.sort(key=lambda c: c.rerank_score or 0.0, reverse=True)
                result.reranked = True

        budget = request.token_budget
        for candidate in candidates[: request.final]:
            if result.chunks and candidate.token_count > budget:
                continue
            result.chunks.append(candidate)
            budget -= candidate.token_count
        return result
