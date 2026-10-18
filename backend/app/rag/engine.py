"""The retrieval stack for one process: embedder, reranker, vector store, retriever."""

from __future__ import annotations

import asyncio
import ipaddress
import warnings
from urllib.parse import urlsplit

import httpx
from qdrant_client import AsyncQdrantClient

from app.core.config import Settings
from app.rag.embed import DenseEmbedder, create_embedder
from app.rag.rerank import Reranker, create_reranker
from app.rag.retrieve import Retriever
from app.rag.store import VectorStore, collection_name

COLLECTION_PREFIX = "kb"


def _is_internal_host(url: str) -> bool:
    """Plain HTTP to Qdrant is expected on a private network (a Compose service name, loopback, RFC 1918)."""
    host = urlsplit(url).hostname or ""
    if "." not in host:
        return True  # single-label names only resolve inside a container network
    try:
        return ipaddress.ip_address(host).is_private
    except ValueError:
        return False


class RagEngine:
    def __init__(self, settings: Settings, http: httpx.AsyncClient) -> None:
        self.settings = settings
        self.embedder: DenseEmbedder = create_embedder(settings, http)
        self.reranker: Reranker = create_reranker(settings, http)
        with warnings.catch_warnings():
            if _is_internal_host(settings.qdrant_url):
                warnings.filterwarnings("ignore", message="Api key is used with an insecure connection")
            self.qdrant = AsyncQdrantClient(
                url=settings.qdrant_url,
                api_key=settings.qdrant_api_key.get_secret_value() if settings.qdrant_api_key else None,
                timeout=30,
                check_compatibility=False,
            )
        self._store: VectorStore | None = None
        self._lock = asyncio.Lock()

    async def store(self) -> VectorStore:
        """The vector store for the current embedder (collection created on first use)."""
        if self._store is not None:
            return self._store
        async with self._lock:
            if self._store is None:
                dim = await self.embedder.dimension()
                store = VectorStore(self.qdrant, collection_name(COLLECTION_PREFIX, self.embedder.model_id, dim))
                await store.ensure_collection(dim)
                self._store = store
        return self._store

    async def retriever(self) -> Retriever:
        return Retriever(await self.store(), self.embedder, self.reranker)

    async def close(self) -> None:
        await self.qdrant.close()
