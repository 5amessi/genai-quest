from __future__ import annotations

import hashlib
import json

from app.config import Settings
from app.domain.models import Chunk, DocumentInput, DocumentRecord, IngestResponse
from app.ingestion.chunker import StructureAwareChunker
from app.ingestion.parser import DocumentParser
from app.ports import EmbeddingProvider, IndexWriter
from app.resilience import RetryPolicy, retry_async
from app.security.guards import scan_untrusted_text


class IngestionService:
    def __init__(
        self,
        *,
        parser: DocumentParser,
        chunker: StructureAwareChunker,
        embedder: EmbeddingProvider,
        index: IndexWriter,
        settings: Settings,
    ) -> None:
        self.parser = parser
        self.chunker = chunker
        self.embedder = embedder
        self.index = index
        self.settings = settings

    async def ingest(self, document: DocumentInput) -> IngestResponse:
        normalized = self.parser.parse(document)
        checksum_payload = document.model_dump(mode="json", exclude={"content"})
        checksum_payload["content"] = normalized
        checksum = hashlib.sha256(
            json.dumps(checksum_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        canonical_id = document.canonical_id or document.document_id
        record = DocumentRecord(
            **document.model_dump(exclude={"canonical_id", "content"}),
            canonical_id=canonical_id,
            content=normalized,
            checksum=checksum,
        )

        drafts = self.chunker.split(normalized)
        chunks: list[Chunk] = []
        for ordinal, draft in enumerate(drafts):
            embedding = await retry_async(
                lambda content=draft.content: self.embedder.embed(content),
                RetryPolicy(
                    attempts=self.settings.retry_attempts,
                    timeout_seconds=self.settings.embedding_timeout_seconds,
                ),
            )
            chunk_hash = hashlib.sha256(
                f"{canonical_id}:{document.version}:{ordinal}:{checksum}".encode()
            ).hexdigest()[:24]
            chunks.append(
                Chunk(
                    chunk_id=f"chk-{chunk_hash}",
                    document_id=document.document_id,
                    canonical_id=canonical_id,
                    title=document.title,
                    content=draft.content,
                    heading=draft.heading,
                    ordinal=ordinal,
                    token_count=draft.token_count,
                    department=document.department,
                    allowed_groups=document.allowed_groups,
                    classification=document.classification,
                    version=document.version,
                    effective_from=document.effective_from,
                    source_uri=document.source_uri,
                    checksum=checksum,
                    metadata=document.metadata,
                    risk_flags=scan_untrusted_text(draft.content),
                    embedding=embedding,
                )
            )

        # Commit only after every parse/chunk/embed step succeeds: no partially searchable document.
        status, indexed = await self.index.upsert(record, chunks)
        return IngestResponse(
            document_id=document.document_id,
            version=document.version,
            status=status,
            chunks_indexed=indexed,
            checksum=checksum,
        )
