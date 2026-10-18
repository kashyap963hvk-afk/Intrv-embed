"""Dense embedding backends behind one interface.

tei      HF text-embeddings-inference server (quality profile: BAAI/bge-m3, 1024-d, GPU or CPU)
fastembed in-process ONNX (lite profile: BAAI/bge-small-en-v1.5, 384-d)
openai   any OpenAI-compatible /embeddings endpoint
hash     deterministic lexical hashing (tests and offline development only; refused in production)
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
from collections.abc import Sequence
from typing import Protocol

import httpx

from app.core.config import Settings
from app.core.logging import get_logger

log = get_logger("app.rag.embed")


class EmbeddingError(Exception):
    pass


class DenseEmbedder(Protocol):
    model_id: str

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...

    async def dimension(self) -> int: ...


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]


class HashEmbedder:
    """Feature-hashed word unigrams + character trigrams. Lexical only: for tests and offline dev."""

    def __init__(self, dim: int = 256) -> None:
        self._dim = dim
        self.model_id = f"hash-{dim}"

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self._dim
        lowered = text.lower()
        features = re.findall(r"[a-z0-9_]+", lowered)
        features += [lowered[i : i + 3] for i in range(max(0, len(lowered) - 2))]
        for feature in features:
            digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "little") % self._dim
            vector[index] += 1.0 if digest[4] & 1 else -1.0
        return _normalize(vector)

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    async def dimension(self) -> int:
        return self._dim


class TEIEmbedder:
    def __init__(self, url: str, model_id: str, batch_size: int, client: httpx.AsyncClient) -> None:
        self._url = url.rstrip("/")
        self.model_id = model_id
        self._batch = batch_size
        self._client = client
        self._dim: int | None = None

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch):
            batch = list(texts[start : start + self._batch])
            try:
                response = await self._client.post(
                    f"{self._url}/embed", json={"inputs": batch, "normalize": True, "truncate": True}, timeout=120
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise EmbeddingError(f"embedding server error: {exc}") from exc
            vectors.extend(response.json())
        return vectors

    async def dimension(self) -> int:
        if self._dim is None:
            self._dim = len((await self.embed(["dimension probe"]))[0])
        return self._dim


class OpenAIEmbedder:
    def __init__(self, base_url: str, api_key: str | None, model_id: str, batch_size: int, client: httpx.AsyncClient):
        self._url = base_url.rstrip("/")
        self._key = api_key
        self.model_id = model_id
        self._batch = batch_size
        self._client = client
        self._dim: int | None = None

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        headers = {"Authorization": f"Bearer {self._key}"} if self._key else {}
        for start in range(0, len(texts), self._batch):
            batch = list(texts[start : start + self._batch])
            try:
                response = await self._client.post(
                    f"{self._url}/embeddings",
                    json={"model": self.model_id, "input": batch},
                    headers=headers,
                    timeout=120,
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise EmbeddingError(f"embedding API error: {exc}") from exc
            data = sorted(response.json()["data"], key=lambda item: item["index"])
            vectors.extend(_normalize(item["embedding"]) for item in data)
        return vectors

    async def dimension(self) -> int:
        if self._dim is None:
            self._dim = len((await self.embed(["dimension probe"]))[0])
        return self._dim


class FastEmbedEmbedder:
    """In-process ONNX model (downloaded once into MODELS_CACHE_DIR). CPU-bound work runs in a thread."""

    def __init__(self, model_id: str, cache_dir: str, batch_size: int) -> None:
        self.model_id = model_id
        self._cache_dir = cache_dir
        self._batch = batch_size
        self._model: object | None = None
        self._lock = asyncio.Lock()

    async def _load(self) -> object:
        async with self._lock:
            if self._model is None:
                from fastembed import TextEmbedding  # noqa: PLC0415 - heavy import, only when this backend is used

                self._model = await asyncio.to_thread(
                    TextEmbedding, model_name=self.model_id, cache_dir=self._cache_dir
                )
            return self._model

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        model = await self._load()

        def run() -> list[list[float]]:
            return [list(map(float, v)) for v in model.embed(list(texts), batch_size=self._batch)]  # type: ignore[attr-defined]

        try:
            return await asyncio.to_thread(run)
        except Exception as exc:
            raise EmbeddingError(f"local embedding failed: {exc}") from exc

    async def dimension(self) -> int:
        return len((await self.embed(["dimension probe"]))[0])


def create_embedder(settings: Settings, client: httpx.AsyncClient) -> DenseEmbedder:
    match settings.embed_backend:
        case "hash":
            dims = re.findall(r"\d+", settings.embed_model)
            return HashEmbedder(int(dims[0]) if dims else 256)
        case "tei":
            return TEIEmbedder(settings.tei_url or "", settings.embed_model, settings.embed_batch_size, client)
        case "openai":
            key = settings.embed_openai_api_key.get_secret_value() if settings.embed_openai_api_key else None
            return OpenAIEmbedder(
                settings.embed_openai_base_url or "", key, settings.embed_model, settings.embed_batch_size, client
            )
        case "fastembed":
            return FastEmbedEmbedder(settings.embed_model, settings.models_cache_dir, settings.embed_batch_size)
    raise ValueError(f"unknown embed backend {settings.embed_backend}")
