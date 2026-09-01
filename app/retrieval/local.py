from __future__ import annotations

import asyncio
import hashlib
import math
from collections import Counter

from app.domain.errors import IngestionConflictError
from app.domain.models import Chunk, DocumentRecord, IngestionStatus, Principal, SearchHit
from app.ports import EmbeddingProvider, SearchResults
from app.security.guards import is_authorized
from app.text import cosine, coverage, term_frequency, terms


class HashingEmbedder(EmbeddingProvider):
    """Deterministic no-network test double; not presented as a production embedding model."""

    def __init__(self, dimensions: int = 192) -> None:
        self.dimensions = dimensions

    async def embed(self, text: str) -> tuple[float, ...]:
        values = [0.0] * self.dimensions
        counts = Counter(terms(text))
        for token, frequency in counts.items():
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            values[index] += sign * (1.0 + math.log(frequency))
        norm = math.sqrt(sum(value * value for value in values)) or 1.0
        return tuple(value / norm for value in values)


class InMemorySearchIndex:
    """Atomic local index that models Azure Search fields and security semantics."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, int], DocumentRecord] = {}
        self._chunks: dict[str, Chunk] = {}
        self._lock = asyncio.Lock()

    async def upsert(
        self, record: DocumentRecord, chunks: list[Chunk]
    ) -> tuple[IngestionStatus, int]:
        async with self._lock:
            key = (record.document_id, record.version)
            existing = self._records.get(key)
            if existing:
                if existing.checksum == record.checksum:
                    return IngestionStatus.DUPLICATE, 0
                raise IngestionConflictError(
                    "the same document_id/version already exists with a different checksum"
                )

            versions = [
                item for item in self._records.values() if item.canonical_id == record.canonical_id
            ]
            if versions:
                current = max(versions, key=lambda item: (item.effective_from, item.version))
                if (record.effective_from, record.version) <= (
                    current.effective_from,
                    current.version,
                ):
                    return IngestionStatus.STALE, 0
                for old_key, old_record in list(self._records.items()):
                    if old_record.canonical_id == record.canonical_id and old_record.is_current:
                        self._records[old_key] = old_record.model_copy(update={"is_current": False})
                for chunk_id, old_chunk in list(self._chunks.items()):
                    if old_chunk.canonical_id == record.canonical_id and old_chunk.is_current:
                        self._chunks[chunk_id] = old_chunk.model_copy(update={"is_current": False})

            self._records[key] = record
            for chunk in chunks:
                self._chunks[chunk.chunk_id] = chunk
            return IngestionStatus.INGESTED, len(chunks)

    async def current_authorized_chunks(self, principal: Principal) -> list[Chunk]:
        return [
            chunk
            for chunk in self._chunks.values()
            if chunk.is_current and is_authorized(principal, chunk.allowed_groups)
        ]

    async def debug_document(self, document_id: str, principal: Principal) -> list[Chunk]:
        return [
            chunk
            for chunk in self._chunks.values()
            if chunk.document_id == document_id and is_authorized(principal, chunk.allowed_groups)
        ]

    async def ready(self) -> bool:
        return bool(self._chunks)


class LocalHybridRetriever:
    """Keyword + vector + deterministic reranking over an authorization-prefiltered set."""

    def __init__(self, index: InMemorySearchIndex, embedder: EmbeddingProvider) -> None:
        self.index = index
        self.embedder = embedder

    async def search(self, question: str, principal: Principal, top_k: int) -> SearchResults:
        # This call is deliberately first: unauthorized chunks never participate in scoring.
        candidates = await self.index.current_authorized_chunks(principal)
        query_vector = await self.embedder.embed(question)
        query_tf = term_frequency(question)
        query_terms = set(query_tf)
        scored: list[SearchHit] = []
        for chunk in candidates:
            chunk_tf = term_frequency(f"{chunk.title} {chunk.heading or ''} {chunk.content}")
            overlap = query_terms.intersection(chunk_tf)
            keyword_score = min(
                1.0,
                sum(1.0 + math.log(chunk_tf[token]) for token in overlap)
                / max(1.0, len(query_terms) * 1.5),
            )
            vector_score = cosine(query_vector, chunk.embedding)
            reranker_score = coverage(
                question, f"{chunk.title} {chunk.heading or ''} {chunk.content}"
            )
            phrase_bonus = 0.08 if question.lower().rstrip("?") in chunk.content.lower() else 0.0
            score = min(
                1.0,
                0.40 * keyword_score + 0.35 * vector_score + 0.25 * reranker_score + phrase_bonus,
            )
            scored.append(
                SearchHit(
                    chunk=chunk,
                    score=score,
                    keyword_score=keyword_score,
                    vector_score=vector_score,
                    reranker_score=reranker_score,
                )
            )
        scored.sort(
            key=lambda hit: (hit.score, hit.chunk.effective_from, hit.chunk.version), reverse=True
        )
        return SearchResults(scored[:top_k], len(candidates), "local-hybrid")

    async def ready(self) -> bool:
        return await self.index.ready()

    async def debug_document(self, document_id: str, principal: Principal) -> list[Chunk]:
        return await self.index.debug_document(document_id, principal)
