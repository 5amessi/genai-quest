from __future__ import annotations

from typing import Protocol

from app.domain.models import (
    AnswerDraft,
    Chunk,
    DocumentRecord,
    IngestionStatus,
    ModelRoute,
    Principal,
    SearchHit,
)


class EmbeddingProvider(Protocol):
    async def embed(self, text: str) -> tuple[float, ...]: ...


class SearchResults:
    def __init__(self, hits: list[SearchHit], candidate_count: int, strategy: str) -> None:
        self.hits = hits
        self.candidate_count = candidate_count
        self.strategy = strategy


class Retriever(Protocol):
    async def search(self, question: str, principal: Principal, top_k: int) -> SearchResults: ...

    async def ready(self) -> bool: ...


class IndexWriter(Protocol):
    async def upsert(
        self, record: DocumentRecord, chunks: list[Chunk]
    ) -> tuple[IngestionStatus, int]: ...


class AnswerGenerator(Protocol):
    async def generate(
        self,
        question: str,
        evidence: list[SearchHit],
        route: ModelRoute,
        prompt_version: str,
    ) -> AnswerDraft: ...
