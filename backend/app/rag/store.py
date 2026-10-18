"""Qdrant vector store: one collection per embedding model+dimension, hybrid dense+BM25 search with RRF.

Points carry only identifiers (org, space, document, chunk, parent, doc type), never document text: text lives
in Postgres under RLS, so a Qdrant mistake can leak at most IDs, and erasure only has to delete points by id.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from qdrant_client import AsyncQdrantClient, models

from app.rag.sparse import SparseVector

_INDEXED_FIELDS = ("space_id", "document_id", "doc_type")


@dataclass(frozen=True, slots=True)
class ChunkPoint:
    chunk_id: uuid.UUID
    parent_id: uuid.UUID
    org_id: uuid.UUID
    space_id: uuid.UUID
    document_id: uuid.UUID
    doc_type: str
    dense: list[float]
    sparse: SparseVector


@dataclass(frozen=True, slots=True)
class Hit:
    chunk_id: uuid.UUID
    parent_id: uuid.UUID
    document_id: uuid.UUID
    score: float


def collection_name(prefix: str, model_id: str, dim: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", model_id.lower()).strip("-")
    return f"{prefix}__{slug}__{dim}"


def _sparse(vector: SparseVector) -> models.SparseVector:
    return models.SparseVector(indices=vector.indices, values=vector.values)


class VectorStore:
    def __init__(self, client: AsyncQdrantClient, collection: str) -> None:
        self.client = client
        self.collection = collection

    async def ensure_collection(self, dim: int) -> None:
        if await self.client.collection_exists(self.collection):
            info = await self.client.get_collection(self.collection)
            vectors = info.config.params.vectors
            existing = vectors["dense"].size if isinstance(vectors, dict) else None
            if existing != dim:
                raise RuntimeError(
                    f"Qdrant collection {self.collection} has dimension {existing}, embedder produces {dim}"
                )
            return
        await self.client.create_collection(
            self.collection,
            vectors_config={"dense": models.VectorParams(size=dim, distance=models.Distance.COSINE)},
            sparse_vectors_config={"sparse": models.SparseVectorParams(modifier=models.Modifier.IDF)},
        )
        await self.client.create_payload_index(
            self.collection,
            "org_id",
            field_schema=models.KeywordIndexParams(type=models.KeywordIndexType.KEYWORD, is_tenant=True),
        )
        for field in _INDEXED_FIELDS:
            await self.client.create_payload_index(
                self.collection, field, field_schema=models.PayloadSchemaType.KEYWORD
            )

    async def upsert(self, points: Sequence[ChunkPoint], batch_size: int = 64) -> None:
        for start in range(0, len(points), batch_size):
            batch = points[start : start + batch_size]
            await self.client.upsert(
                self.collection,
                points=[
                    models.PointStruct(
                        id=str(p.chunk_id),
                        vector={"dense": p.dense, "sparse": _sparse(p.sparse)},
                        payload={
                            "org_id": str(p.org_id),
                            "space_id": str(p.space_id),
                            "document_id": str(p.document_id),
                            "parent_id": str(p.parent_id),
                            "doc_type": p.doc_type,
                        },
                    )
                    for p in batch
                ],
                wait=True,
            )

    async def delete_document(
        self, org_id: uuid.UUID, document_id: uuid.UUID, *, keep: Sequence[uuid.UUID] = ()
    ) -> None:
        """Delete a document's points, except `keep` (the new version during a reindex swap)."""
        flt = models.Filter(
            must=[
                models.FieldCondition(key="org_id", match=models.MatchValue(value=str(org_id))),
                models.FieldCondition(key="document_id", match=models.MatchValue(value=str(document_id))),
            ],
            must_not=[models.HasIdCondition(has_id=[str(k) for k in keep])] if keep else None,
        )
        await self.client.delete(self.collection, points_selector=models.FilterSelector(filter=flt), wait=True)

    async def delete_space(self, org_id: uuid.UUID, space_id: uuid.UUID) -> None:
        flt = models.Filter(
            must=[
                models.FieldCondition(key="org_id", match=models.MatchValue(value=str(org_id))),
                models.FieldCondition(key="space_id", match=models.MatchValue(value=str(space_id))),
            ]
        )
        await self.client.delete(self.collection, points_selector=models.FilterSelector(filter=flt), wait=True)

    async def search(
        self,
        *,
        org_id: uuid.UUID,
        space_ids: Sequence[uuid.UUID],
        dense: list[float],
        sparse: SparseVector,
        identifier_sparse: SparseVector | None,
        doc_types: Sequence[str] | None = None,
        document_ids: Sequence[uuid.UUID] | None = None,
        prefetch_limit: int = 40,
        limit: int = 30,
    ) -> list[Hit]:
        if not space_ids:
            return []
        must: list[models.Condition] = [
            models.FieldCondition(key="org_id", match=models.MatchValue(value=str(org_id))),
            models.FieldCondition(key="space_id", match=models.MatchAny(any=[str(s) for s in space_ids])),
        ]
        if doc_types:
            must.append(models.FieldCondition(key="doc_type", match=models.MatchAny(any=list(doc_types))))
        if document_ids:
            must.append(
                models.FieldCondition(key="document_id", match=models.MatchAny(any=[str(d) for d in document_ids]))
            )
        flt = models.Filter(must=must)
        prefetch = [
            models.Prefetch(query=dense, using="dense", filter=flt, limit=prefetch_limit),
            models.Prefetch(query=_sparse(sparse), using="sparse", filter=flt, limit=prefetch_limit),
        ]
        if identifier_sparse is not None:
            # Exact identifiers (TIM2_CCR1, 0x4000...) get their own ranked list, i.e. extra weight in the fusion.
            prefetch.append(
                models.Prefetch(query=_sparse(identifier_sparse), using="sparse", filter=flt, limit=prefetch_limit)
            )
        result = await self.client.query_points(
            self.collection,
            prefetch=prefetch,
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=limit,
            with_payload=True,
        )
        hits: list[Hit] = []
        for point in result.points:
            payload = point.payload or {}
            if payload.get("org_id") != str(org_id):  # belt and braces: never trust a mis-scoped hit
                continue
            hits.append(
                Hit(
                    chunk_id=uuid.UUID(str(point.id)),
                    parent_id=uuid.UUID(str(payload["parent_id"])),
                    document_id=uuid.UUID(str(payload["document_id"])),
                    score=float(point.score),
                )
            )
        return hits
