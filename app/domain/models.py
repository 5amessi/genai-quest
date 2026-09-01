from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Classification(StrEnum):
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class AnswerStatus(StrEnum):
    ANSWERED = "answered"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    REFUSED = "refused"
    CLARIFICATION_REQUIRED = "clarification_required"
    DEGRADED = "degraded"


class IngestionStatus(StrEnum):
    INGESTED = "ingested"
    DUPLICATE = "duplicate"
    STALE = "stale"


class ModelRoute(StrEnum):
    NO_LLM = "no_llm"
    SMALL_HOSTED = "small_hosted"
    FRONTIER_HOSTED = "frontier_hosted"
    PRIVATE_MODEL = "private_model"
    DETERMINISTIC_LOCAL = "deterministic_local"


class Principal(BaseModel):
    """Identity and entitlements derived from a verified token, never request JSON."""

    model_config = ConfigDict(frozen=True)

    subject: str = Field(min_length=1, max_length=200)
    groups: frozenset[str] = Field(default_factory=frozenset)
    roles: frozenset[str] = Field(default_factory=frozenset)

    @field_validator("groups", "roles")
    @classmethod
    def validate_entitlements(cls, values: frozenset[str]) -> frozenset[str]:
        for value in values:
            if not value or len(value) > 128:
                raise ValueError("entitlements must be between 1 and 128 characters")
            if any(
                ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.:"
                for ch in value
            ):
                raise ValueError("entitlements contain an unsupported character")
        return values

    def has_role(self, role: str) -> bool:
        return role in self.roles


JsonScalar = str | int | float | bool | None


class DocumentInput(BaseModel):
    document_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    canonical_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1, max_length=2_000_000)
    content_type: Literal["text", "markdown", "pdf-extracted", "docx-extracted"] = "markdown"
    department: str = Field(min_length=1, max_length=100)
    allowed_groups: frozenset[str] = Field(min_length=1)
    classification: Classification = Classification.INTERNAL
    version: int = Field(ge=1)
    effective_from: date
    source_uri: str = Field(min_length=1, max_length=1000)
    metadata: dict[str, JsonScalar] = Field(default_factory=dict)

    @field_validator("allowed_groups")
    @classmethod
    def validate_allowed_groups(cls, groups: frozenset[str]) -> frozenset[str]:
        # Reuse the same restricted alphabet used for token-derived group claims.
        Principal.validate_entitlements(groups)
        return groups


class DocumentRecord(DocumentInput):
    canonical_id: str
    checksum: str
    ingested_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    is_current: bool = True


class Chunk(BaseModel):
    chunk_id: str
    document_id: str
    canonical_id: str
    title: str
    content: str
    heading: str | None = None
    ordinal: int = Field(ge=0)
    token_count: int = Field(ge=1)
    department: str
    allowed_groups: frozenset[str]
    classification: Classification
    version: int = Field(ge=1)
    effective_from: date
    source_uri: str
    is_current: bool = True
    checksum: str
    metadata: dict[str, JsonScalar] = Field(default_factory=dict)
    risk_flags: tuple[str, ...] = ()
    embedding: tuple[float, ...] = Field(default=(), exclude=True)


class SearchHit(BaseModel):
    chunk: Chunk
    score: float = Field(ge=0.0)
    keyword_score: float = Field(default=0.0, ge=0.0)
    vector_score: float = Field(default=0.0, ge=0.0)
    reranker_score: float = Field(default=0.0, ge=0.0)


class Citation(BaseModel):
    citation_id: str
    chunk_id: str
    document_id: str
    title: str
    version: int
    source_uri: str
    excerpt: str = Field(max_length=1000)


class Usage(BaseModel):
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    estimated_cost_usd: float = Field(default=0.0, ge=0.0)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class DebugHit(BaseModel):
    chunk_id: str
    document_id: str
    title: str
    version: int
    heading: str | None
    score: float
    keyword_score: float
    vector_score: float
    reranker_score: float
    risk_flags: tuple[str, ...]
    selected_for_context: bool


class RetrievalDebug(BaseModel):
    strategy: str = "local-hybrid"
    authorization_filter: str
    candidate_count: int = Field(ge=0)
    hits: list[DebugHit] = Field(default_factory=list)
    context_chunk_ids: list[str] = Field(default_factory=list)
    evidence_coverage: float = Field(default=0.0, ge=0.0, le=1.0)


class QueryRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    debug: bool = False

    @field_validator("question")
    @classmethod
    def normalize_question(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("question must not be blank")
        return value


class QueryResponse(BaseModel):
    request_id: str
    status: AnswerStatus
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    model_route: ModelRoute
    usage: Usage = Field(default_factory=Usage)
    latency_ms: float = Field(ge=0.0)
    retrieval_debug: RetrievalDebug | None = None


class IngestResponse(BaseModel):
    document_id: str
    version: int
    status: IngestionStatus
    chunks_indexed: int = Field(ge=0)
    checksum: str


class AnswerDraft(BaseModel):
    """Strict boundary for model output before it becomes an API response."""

    answer: str = Field(min_length=1, max_length=6000)
    cited_chunk_ids: list[str] = Field(default_factory=list, max_length=12)
    confidence: float = Field(ge=0.0, le=1.0)


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    checks: dict[str, str] = Field(default_factory=dict)
