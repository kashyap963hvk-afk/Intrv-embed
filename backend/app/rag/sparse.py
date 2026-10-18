"""BM25 sparse vectors computed locally (no model download).

Document vectors carry BM25 term-frequency saturation; Qdrant applies IDF at query time
(`Modifier.IDF`), which together gives BM25 scoring, the same split FastEmbed's `Qdrant/bm25` uses.
Identifiers (register names, hex addresses, part numbers) are kept verbatim and case-folded but never
stemmed, so `TIM2_CCR1` matches only `TIM2_CCR1`.
"""

from __future__ import annotations

import zlib
from collections import Counter
from dataclasses import dataclass

import snowballstemmer

from app.rag.text import identifiers, words

K1 = 1.2
B = 0.75
AVG_DOC_TOKENS = 256.0
_STEMMER = snowballstemmer.stemmer("english")
_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "but",
        "by",
        "for",
        "from",
        "has",
        "have",
        "he",
        "her",
        "his",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "me",
        "my",
        "no",
        "not",
        "of",
        "on",
        "or",
        "our",
        "she",
        "so",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "to",
        "too",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "you",
        "your",
        "do",
        "does",
        "did",
        "can",
        "could",
        "should",
        "would",
        "about",
        "after",
        "all",
        "also",
        "any",
        "been",
        "before",
        "being",
        "both",
        "each",
        "few",
        "more",
        "most",
        "other",
        "some",
        "such",
        "only",
        "own",
        "same",
        "very",
        "just",
        "over",
        "under",
        "again",
        "further",
        "once",
        "here",
        "how",
    ]
)


@dataclass(frozen=True, slots=True)
class SparseVector:
    indices: list[int]
    values: list[float]


def _index(term: str) -> int:
    return zlib.crc32(term.encode()) & 0x7FFFFFFF


def terms(text: str) -> list[str]:
    exact = {i.lower() for i in identifiers(text)}
    result: list[str] = []
    for word in words(text):
        lowered = word.lower()
        if lowered in exact:
            result.append(lowered)
        elif lowered not in _STOPWORDS and len(lowered) > 1:
            result.append(_STEMMER.stemWord(lowered))
    return result


def encode_document(text: str) -> SparseVector:
    counts = Counter(terms(text))
    length = sum(counts.values()) or 1
    norm = K1 * (1 - B + B * length / AVG_DOC_TOKENS)
    weights: dict[int, float] = {}
    for term, tf in counts.items():
        index = _index(term)
        weights[index] = weights.get(index, 0.0) + tf * (K1 + 1) / (tf + norm)
    return SparseVector(list(weights), list(weights.values()))


def encode_query(text: str) -> SparseVector:
    indices = sorted({_index(term) for term in terms(text)})
    return SparseVector(indices, [1.0] * len(indices))


def encode_identifiers(text: str) -> SparseVector | None:
    """Query vector containing only exact identifiers (for the exact-match boost), or None."""
    indices = sorted({_index(i.lower()) for i in identifiers(text)})
    return SparseVector(indices, [1.0] * len(indices)) if indices else None
