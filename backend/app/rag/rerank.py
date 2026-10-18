"""Cross-encoder reranking backends (TEI, in-process FastEmbed) or none."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Protocol

import httpx

from app.core.config import Settings


class RerankError(Exception):
    pass


class Reranker(Protocol):
    enabled: bool

    async def scores(self, query: str, texts: Sequence[str]) -> list[float]: ...


class NoReranker:
    enabled = False

    async def scores(self, query: str, texts: Sequence[str]) -> list[float]:
        return [float(len(texts) - i) for i in range(len(texts))]  # keep the incoming (fused) order


class TEIReranker:
    enabled = True

    def __init__(self, url: str, client: httpx.AsyncClient) -> None:
        self._url = url.rstrip("/")
        self._client = client

    async def scores(self, query: str, texts: Sequence[str]) -> list[float]:
        if not texts:
            return []
        try:
            response = await self._client.post(
                f"{self._url}/rerank", json={"query": query, "texts": list(texts), "truncate": True}, timeout=60
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise RerankError(f"reranker error: {exc}") from exc
        result = [0.0] * len(texts)
        for item in response.json():
            result[int(item["index"])] = float(item["score"])
        return result


class FastEmbedReranker:
    enabled = True

    def __init__(self, model_id: str, cache_dir: str) -> None:
        self._model_id = model_id
        self._cache_dir = cache_dir
        self._model: object | None = None
        self._lock = asyncio.Lock()

    async def scores(self, query: str, texts: Sequence[str]) -> list[float]:
        if not texts:
            return []
        async with self._lock:
            if self._model is None:
                from fastembed.rerank.cross_encoder import TextCrossEncoder  # noqa: PLC0415 - heavy, optional

                self._model = await asyncio.to_thread(
                    TextCrossEncoder, model_name=self._model_id, cache_dir=self._cache_dir
                )
        model = self._model
        try:
            return await asyncio.to_thread(lambda: [float(s) for s in model.rerank(query, list(texts))])  # type: ignore[attr-defined]
        except Exception as exc:
            raise RerankError(f"local reranking failed: {exc}") from exc


def create_reranker(settings: Settings, client: httpx.AsyncClient) -> Reranker:
    match settings.rerank_backend:
        case "tei":
            return TEIReranker(settings.tei_rerank_url or "", client)
        case "fastembed":
            return FastEmbedReranker(settings.rerank_model, settings.models_cache_dir)
        case "none":
            return NoReranker()
    raise ValueError(f"unknown rerank backend {settings.rerank_backend}")
