from __future__ import annotations

from app.config import Settings
from app.demo_data import demo_documents
from app.generation.local import DeterministicGroundedGenerator, ModelRouter
from app.ingestion.chunker import StructureAwareChunker
from app.ingestion.parser import DocumentParser
from app.ingestion.service import IngestionService
from app.observability import MetricsRecorder
from app.retrieval.local import HashingEmbedder, InMemorySearchIndex, LocalHybridRetriever
from app.security.guards import EvidenceGate
from app.service import KnowledgeService


def build_demo_service(settings: Settings | None = None) -> KnowledgeService:
    settings = settings or Settings(environment="test")
    embedder = HashingEmbedder()
    index = InMemorySearchIndex()
    ingestion = IngestionService(
        parser=DocumentParser(),
        chunker=StructureAwareChunker(
            max_tokens=settings.max_chunk_tokens,
            overlap_tokens=settings.chunk_overlap_tokens,
        ),
        embedder=embedder,
        index=index,
        settings=settings,
    )
    return KnowledgeService(
        settings=settings,
        ingestion=ingestion,
        retriever=LocalHybridRetriever(index, embedder),
        generator=DeterministicGroundedGenerator(),
        model_router=ModelRouter(),
        evidence_gate=EvidenceGate(),
        metrics=MetricsRecorder(),
        bootstrap_documents=demo_documents(),
    )
