"""Startup and operator self-test: are the models, the vector store and the parsers usable right now?"""

from __future__ import annotations

from dataclasses import dataclass

from app.rag.engine import RagEngine
from app.rag.parse import docling_available, ocr_available


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    ok: bool
    detail: str


_PROBE_TEXTS = ("GPIOx_BSRR sets pins atomically", "Tomatoes are red")


async def _check_embedder(rag: RagEngine) -> Check:
    dim = await rag.embedder.dimension()
    vectors = await rag.embedder.embed(list(_PROBE_TEXTS))
    if len(vectors) != len(_PROBE_TEXTS) or any(len(v) != dim for v in vectors):
        return Check("embedder", False, f"returned vectors of the wrong shape (expected {len(_PROBE_TEXTS)} x {dim})")
    return Check("embedder", True, f"{rag.embedder.model_id}: {dim} dimensions")


async def run_selftest(rag: RagEngine) -> list[Check]:
    checks: list[Check] = []
    try:
        embedder = await _check_embedder(rag)
        checks.append(embedder)
        if not embedder.ok:
            return checks
        store = await rag.store()
        checks.append(Check("vector store", True, f"collection {store.collection} ready"))
    except Exception as exc:
        checks.append(Check("embedder / vector store", False, f"{type(exc).__name__}: {exc}"))
    if rag.reranker.enabled:
        try:
            scores = await rag.reranker.scores("what is DMA?", ["DMA moves data without the CPU.", "Tomatoes."])
            checks.append(Check("reranker", scores[0] > scores[1], f"{rag.settings.rerank_model}: {scores}"))
        except Exception as exc:
            checks.append(Check("reranker", False, f"{type(exc).__name__}: {exc}"))
    else:
        checks.append(Check("reranker", True, "disabled (RERANK_BACKEND=none)"))
    checks.append(Check("OCR", True, "available" if ocr_available() else "not installed (scanned PDFs get no text)"))
    checks.append(
        Check("Docling", True, "available" if docling_available() else "not installed (built-in parsers in use)")
    )
    return checks
