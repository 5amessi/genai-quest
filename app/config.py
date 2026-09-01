from __future__ import annotations

import os
from dataclasses import dataclass


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


@dataclass(frozen=True, slots=True)
class Settings:
    environment: str = "local"
    backend: str = "local"
    auth_mode: str = "demo"
    log_level: str = "INFO"
    max_chunk_tokens: int = 220
    chunk_overlap_tokens: int = 35
    retrieval_top_k: int = 8
    min_retrieval_score: float = 0.24
    min_evidence_coverage: float = 0.35
    llm_timeout_seconds: float = 12.0
    search_timeout_seconds: float = 3.0
    embedding_timeout_seconds: float = 5.0
    retry_attempts: int = 3
    prompt_version: str = "grounded-answer-v1"
    model_price_input_per_million: float = 0.15
    model_price_output_per_million: float = 0.60
    azure_search_endpoint: str | None = None
    azure_search_index: str = "knowledge-chunks-v1"
    azure_search_api_version: str = "2025-09-01"
    azure_openai_endpoint: str | None = None
    azure_openai_chat_deployment: str | None = None
    azure_openai_embedding_deployment: str | None = None
    azure_openai_api_version: str = "2024-10-21"
    azure_tenant_id: str | None = None
    entra_audience: str | None = None
    entra_issuer: str | None = None
    applicationinsights_connection_string: str | None = None
    allow_failure_injection: bool = False

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            environment=os.getenv("APP_ENV", "local"),
            backend=os.getenv("BACKEND", "local"),
            auth_mode=os.getenv("AUTH_MODE", "demo"),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            max_chunk_tokens=_int("MAX_CHUNK_TOKENS", 220),
            chunk_overlap_tokens=_int("CHUNK_OVERLAP_TOKENS", 35),
            retrieval_top_k=_int("RETRIEVAL_TOP_K", 8),
            min_retrieval_score=_float("MIN_RETRIEVAL_SCORE", 0.24),
            min_evidence_coverage=_float("MIN_EVIDENCE_COVERAGE", 0.35),
            llm_timeout_seconds=_float("LLM_TIMEOUT_SECONDS", 12.0),
            search_timeout_seconds=_float("SEARCH_TIMEOUT_SECONDS", 3.0),
            embedding_timeout_seconds=_float("EMBEDDING_TIMEOUT_SECONDS", 5.0),
            retry_attempts=_int("RETRY_ATTEMPTS", 3),
            prompt_version=os.getenv("PROMPT_VERSION", "grounded-answer-v1"),
            model_price_input_per_million=_float("MODEL_PRICE_INPUT_PER_MILLION", 0.15),
            model_price_output_per_million=_float("MODEL_PRICE_OUTPUT_PER_MILLION", 0.60),
            azure_search_endpoint=os.getenv("AZURE_SEARCH_ENDPOINT"),
            azure_search_index=os.getenv("AZURE_SEARCH_INDEX", "knowledge-chunks-v1"),
            azure_search_api_version=os.getenv("AZURE_SEARCH_API_VERSION", "2025-09-01"),
            azure_openai_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
            azure_openai_chat_deployment=os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT"),
            azure_openai_embedding_deployment=os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT"),
            azure_openai_api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21"),
            azure_tenant_id=os.getenv("AZURE_TENANT_ID"),
            entra_audience=os.getenv("ENTRA_AUDIENCE"),
            entra_issuer=os.getenv("ENTRA_ISSUER"),
            applicationinsights_connection_string=os.getenv(
                "APPLICATIONINSIGHTS_CONNECTION_STRING"
            ),
            allow_failure_injection=os.getenv("ALLOW_FAILURE_INJECTION", "false").lower() == "true",
        )

    def validate(self) -> None:
        if self.auth_mode == "demo" and self.environment not in {"local", "test"}:
            raise ValueError("AUTH_MODE=demo is forbidden outside local/test")
        if self.backend == "azure":
            required = {
                "AZURE_SEARCH_ENDPOINT": self.azure_search_endpoint,
                "AZURE_OPENAI_ENDPOINT": self.azure_openai_endpoint,
                "AZURE_OPENAI_CHAT_DEPLOYMENT": self.azure_openai_chat_deployment,
                "AZURE_OPENAI_EMBEDDING_DEPLOYMENT": self.azure_openai_embedding_deployment,
            }
            missing = [name for name, value in required.items() if not value]
            if missing:
                raise ValueError(f"missing Azure configuration: {', '.join(missing)}")
        if not 0 < self.min_retrieval_score <= 1:
            raise ValueError("MIN_RETRIEVAL_SCORE must be in (0, 1]")
        if not 0 < self.min_evidence_coverage <= 1:
            raise ValueError("MIN_EVIDENCE_COVERAGE must be in (0, 1]")
