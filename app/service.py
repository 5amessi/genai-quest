from __future__ import annotations

import asyncio
from time import perf_counter
from uuid import uuid4

from app.config import Settings
from app.domain.errors import AuthorizationError, StructuredOutputError, TransientProviderError
from app.domain.models import (
    AnswerStatus,
    Citation,
    DebugHit,
    DocumentInput,
    IngestResponse,
    ModelRoute,
    Principal,
    QueryResponse,
    RetrievalDebug,
    SearchHit,
    Usage,
)
from app.generation.local import ModelRouter
from app.ingestion.service import IngestionService
from app.observability import MetricsRecorder, StageTimer, fingerprint
from app.ports import AnswerGenerator, Retriever
from app.resilience import RetryPolicy, retry_async
from app.security.guards import (
    EvidenceGate,
    build_odata_security_filter,
    contains_sensitive_output,
    question_is_known_out_of_scope,
    question_needs_clarification,
    question_policy_violation,
)
from app.text import token_count


class KnowledgeService:
    def __init__(
        self,
        *,
        settings: Settings,
        ingestion: IngestionService,
        retriever: Retriever,
        generator: AnswerGenerator,
        model_router: ModelRouter,
        evidence_gate: EvidenceGate,
        metrics: MetricsRecorder,
        bootstrap_documents: list[DocumentInput] | None = None,
    ) -> None:
        self.settings = settings
        self.ingestion = ingestion
        self.retriever = retriever
        self.generator = generator
        self.model_router = model_router
        self.evidence_gate = evidence_gate
        self.metrics = metrics
        self.bootstrap_documents = bootstrap_documents or []
        self._initialized = False
        self._initialize_lock = asyncio.Lock()

    async def initialize(self) -> None:
        if self._initialized:
            return
        async with self._initialize_lock:
            if self._initialized:
                return
            for document in self.bootstrap_documents:
                await self.ingestion.ingest(document)
            self._initialized = True

    async def ingest(self, document: DocumentInput, principal: Principal) -> IngestResponse:
        if not principal.has_role("knowledge.ingest"):
            raise AuthorizationError("knowledge.ingest role is required")
        return await self.ingestion.ingest(document)

    async def ask(
        self,
        question: str,
        principal: Principal,
        debug: bool = False,
        failure_mode: str | None = None,
    ) -> QueryResponse:
        await self.initialize()
        started = perf_counter()
        request_id = str(uuid4())
        if debug and not principal.has_role("knowledge.debug"):
            raise AuthorizationError("knowledge.debug role is required")

        violation = question_policy_violation(question)
        if violation:
            self.metrics.increment("requests.refused")
            return self._response(
                started,
                request_id,
                AnswerStatus.REFUSED,
                violation,
                ModelRoute.NO_LLM,
            )
        if question_needs_clarification(question):
            self.metrics.increment("requests.clarification_required")
            return self._response(
                started,
                request_id,
                AnswerStatus.CLARIFICATION_REQUIRED,
                "Please clarify which policy, department, service, or approval workflow you mean.",
                ModelRoute.NO_LLM,
            )
        if question_is_known_out_of_scope(question):
            self.metrics.increment("requests.insufficient_evidence")
            return self._response(
                started,
                request_id,
                AnswerStatus.INSUFFICIENT_EVIDENCE,
                "The request is outside the supported enterprise knowledge scope.",
                ModelRoute.NO_LLM,
            )

        async def retrieve():
            if failure_mode == "search_unavailable":
                raise TransientProviderError("simulated search outage")
            return await self.retriever.search(question, principal, self.settings.retrieval_top_k)

        try:
            with StageTimer(self.metrics, "latency.retrieval_ms"):
                search_results = await retry_async(
                    retrieve,
                    RetryPolicy(
                        attempts=self.settings.retry_attempts,
                        timeout_seconds=self.settings.search_timeout_seconds,
                    ),
                )
        except (TransientProviderError, TimeoutError):
            self.metrics.increment("provider.search_failures")
            return self._response(
                started,
                request_id,
                AnswerStatus.DEGRADED,
                "Knowledge search is temporarily unavailable. "
                "No model was called without evidence.",
                ModelRoute.NO_LLM,
                warnings=["Search dependency failed after bounded retries."],
            )

        with StageTimer(self.metrics, "latency.evidence_gate_ms"):
            decision = self.evidence_gate.assess(question, search_results.hits)
        best_score = decision.safe_hits[0].score if decision.safe_hits else 0.0
        debug_details = (
            self._debug(
                principal,
                search_results.hits,
                decision.safe_hits,
                search_results.candidate_count,
                search_results.strategy,
                decision.evidence_coverage,
            )
            if debug
            else None
        )

        if decision.conflict:
            self.metrics.increment("requests.clarification_required")
            assertions: list[str] = []
            seen_assertions: set[tuple[str, str]] = set()
            for hit in decision.safe_hits:
                assertion = hit.chunk.metadata.get("assertion")
                if isinstance(assertion, str):
                    key = (hit.chunk.title, assertion)
                    if key not in seen_assertions:
                        assertions.append(f"{hit.chunk.title} states '{assertion}'")
                        seen_assertions.add(key)
            conflict_detail = "; ".join(assertions)
            conflict_answer = "Authorized sources conflict"
            if conflict_detail:
                conflict_answer += f": {conflict_detail}."
            conflict_answer += (
                " Please clarify the policy scope or ask the document owner to resolve it."
            )
            return self._response(
                started,
                request_id,
                AnswerStatus.CLARIFICATION_REQUIRED,
                conflict_answer,
                ModelRoute.NO_LLM,
                citations=self._citations(decision.safe_hits[:4]),
                warnings=decision.warnings,
                debug=debug_details,
            )

        if (
            not decision.safe_hits
            or best_score < self.settings.min_retrieval_score
            or decision.evidence_coverage < self.settings.min_evidence_coverage
        ):
            self.metrics.increment("requests.insufficient_evidence")
            return self._response(
                started,
                request_id,
                AnswerStatus.INSUFFICIENT_EVIDENCE,
                "I do not have enough authorized, relevant evidence to answer reliably.",
                ModelRoute.NO_LLM,
                warnings=decision.warnings,
                debug=debug_details,
            )

        context = decision.safe_hits[:5]
        route = self.model_router.route(question, context)

        async def generate():
            if failure_mode == "llm_timeout":
                raise TransientProviderError("simulated model timeout")
            return await self.generator.generate(
                question, context, route, self.settings.prompt_version
            )

        try:
            with StageTimer(self.metrics, "latency.llm_ms"):
                draft = await retry_async(
                    generate,
                    RetryPolicy(
                        attempts=self.settings.retry_attempts,
                        timeout_seconds=self.settings.llm_timeout_seconds,
                    ),
                )
            with StageTimer(self.metrics, "latency.output_validation_ms"):
                self._validate_draft(draft.cited_chunk_ids, draft.answer, context)
        except (TransientProviderError, TimeoutError, StructuredOutputError):
            self.metrics.increment("provider.model_failures")
            return self._response(
                started,
                request_id,
                AnswerStatus.DEGRADED,
                "The answer model is temporarily unavailable or returned an invalid "
                "grounded response.",
                route,
                warnings=[*decision.warnings, "Model failed after retries or output validation."],
                debug=debug_details,
            )

        cited_hits = [hit for hit in context if hit.chunk.chunk_id in draft.cited_chunk_ids]
        usage = self._usage(question, context, draft.answer)
        self.metrics.increment("requests.answered")
        self.metrics.observe("usage.prompt_tokens", usage.prompt_tokens)
        self.metrics.observe("usage.completion_tokens", usage.completion_tokens)
        response = self._response(
            started,
            request_id,
            AnswerStatus.ANSWERED,
            draft.answer,
            ModelRoute.DETERMINISTIC_LOCAL if self.settings.backend == "local" else route,
            citations=self._citations(cited_hits),
            warnings=decision.warnings,
            usage=usage,
            debug=debug_details,
        )
        self.metrics.request_event(
            request_id=request_id,
            principal_hash=fingerprint(principal.subject),
            question_hash=fingerprint(question),
            status=response.status,
            route=response.model_route,
            latency_ms=round(response.latency_ms, 2),
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            estimated_cost_usd=usage.estimated_cost_usd,
            citation_count=len(response.citations),
        )
        return response

    async def ready(self) -> bool:
        await self.initialize()
        return await self.retriever.ready()

    async def debug_chunks(self, document_id: str, principal: Principal):
        if not principal.has_role("knowledge.debug"):
            raise AuthorizationError("knowledge.debug role is required")
        await self.initialize()
        debug_method = getattr(self.retriever, "debug_document", None)
        if debug_method is None:
            raise NotImplementedError("the configured backend does not support chunk inspection")
        return await debug_method(document_id, principal)

    @staticmethod
    def _validate_draft(cited_ids: list[str], answer: str, evidence: list[SearchHit]) -> None:
        allowed_ids = {hit.chunk.chunk_id for hit in evidence}
        if not cited_ids or not set(cited_ids).issubset(allowed_ids):
            raise StructuredOutputError("model cited a missing or unauthorized chunk")
        if contains_sensitive_output(answer):
            raise StructuredOutputError("model output matched a sensitive-data pattern")

    @staticmethod
    def _citations(hits: list[SearchHit]) -> list[Citation]:
        citations: list[Citation] = []
        for index, hit in enumerate(hits, start=1):
            excerpt = " ".join(hit.chunk.content.split())[:1000]
            citations.append(
                Citation(
                    citation_id=f"[{index}]",
                    chunk_id=hit.chunk.chunk_id,
                    document_id=hit.chunk.document_id,
                    title=hit.chunk.title,
                    version=hit.chunk.version,
                    source_uri=hit.chunk.source_uri,
                    excerpt=excerpt,
                )
            )
        return citations

    def _usage(self, question: str, hits: list[SearchHit], answer: str) -> Usage:
        prompt_tokens = token_count(question) + sum(hit.chunk.token_count for hit in hits) + 180
        completion_tokens = token_count(answer)
        cost = (
            prompt_tokens * self.settings.model_price_input_per_million
            + completion_tokens * self.settings.model_price_output_per_million
        ) / 1_000_000
        return Usage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            estimated_cost_usd=round(cost, 8),
        )

    @staticmethod
    def _debug(
        principal: Principal,
        hits: list[SearchHit],
        safe_hits: list[SearchHit],
        candidate_count: int,
        strategy: str,
        evidence_coverage: float,
    ) -> RetrievalDebug:
        safe_ids = {hit.chunk.chunk_id for hit in safe_hits[:5]}
        return RetrievalDebug(
            strategy=strategy,
            authorization_filter=build_odata_security_filter(principal),
            candidate_count=candidate_count,
            hits=[
                DebugHit(
                    chunk_id=hit.chunk.chunk_id,
                    document_id=hit.chunk.document_id,
                    title=hit.chunk.title,
                    version=hit.chunk.version,
                    heading=hit.chunk.heading,
                    score=round(hit.score, 6),
                    keyword_score=round(hit.keyword_score, 6),
                    vector_score=round(hit.vector_score, 6),
                    reranker_score=round(hit.reranker_score, 6),
                    risk_flags=hit.chunk.risk_flags,
                    selected_for_context=hit.chunk.chunk_id in safe_ids,
                )
                for hit in hits
            ],
            context_chunk_ids=list(safe_ids),
            evidence_coverage=evidence_coverage,
        )

    @staticmethod
    def _response(
        started: float,
        request_id: str,
        status: AnswerStatus,
        answer: str,
        route: ModelRoute,
        *,
        citations: list[Citation] | None = None,
        warnings: list[str] | None = None,
        usage: Usage | None = None,
        debug: RetrievalDebug | None = None,
    ) -> QueryResponse:
        return QueryResponse(
            request_id=request_id,
            status=status,
            answer=answer,
            citations=citations or [],
            warnings=warnings or [],
            model_route=route,
            usage=usage or Usage(),
            latency_ms=(perf_counter() - started) * 1000,
            retrieval_debug=debug,
        )
