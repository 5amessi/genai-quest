from __future__ import annotations

from app.adapters.azure import (
    AzureAiSearchStore,
    AzureIdentityTokenProvider,
    AzureOpenAIEmbeddingProvider,
    AzureOpenAIGroundedGenerator,
)
from app.config import Settings
from app.generation.local import ModelRouter
from app.ingestion.chunker import StructureAwareChunker
from app.ingestion.parser import DocumentParser
from app.ingestion.service import IngestionService
from app.observability import MetricsRecorder
from app.security.guards import EvidenceGate
from app.service import KnowledgeService


def build_azure_service(settings: Settings) -> KnowledgeService:
    settings.validate()
    tokens = AzureIdentityTokenProvider()
    embedder = AzureOpenAIEmbeddingProvider(settings, tokens)
    search = AzureAiSearchStore(settings, tokens, embedder)
    ingestion = IngestionService(
        parser=DocumentParser(),
        chunker=StructureAwareChunker(
            max_tokens=settings.max_chunk_tokens,
            overlap_tokens=settings.chunk_overlap_tokens,
        ),
        embedder=embedder,
        index=search,
        settings=settings,
    )
    return KnowledgeService(
        settings=settings,
        ingestion=ingestion,
        retriever=search,
        generator=AzureOpenAIGroundedGenerator(settings, tokens),
        model_router=ModelRouter(),
        evidence_gate=EvidenceGate(),
        metrics=MetricsRecorder(),
    )
