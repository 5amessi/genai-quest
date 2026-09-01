# Code walkthrough and requirement mapping

This guide follows the code in execution order. The most important design principle is: **the model is the last component allowed to contribute, not the first component trusted to decide**.

## 1. Composition and startup

`app/main.py` creates the FastAPI app and selects configuration from `app/config.py`.

- `BACKEND=local` calls `build_demo_service()` in `app/bootstrap.py`.
- `BACKEND=azure` calls `build_azure_service()` in `app/azure_bootstrap.py`.
- `AUTH_MODE=demo` is rejected unless `APP_ENV` is `local` or `test`.
- Lifespan initialization loads the demo corpus once and readiness verifies the retrieval backend.

The two bootstraps compose the same domain workflow through the protocols in `app/ports.py`. Local and Azure implementations can change without weakening the workflow’s policy gates.

## 2. Domain contracts

`app/domain/models.py` defines strict Pydantic boundaries:

- `Principal` is immutable and contains server-derived groups/roles.
- `DocumentInput`, `DocumentRecord` and `Chunk` carry ACL, version, effective date, classification, checksum, source and risk metadata.
- `SearchHit` keeps component scores with the evidence.
- `QueryResponse` has typed statuses rather than overloading HTTP 200 with free-form model prose.
- `AnswerDraft` is the only model-output shape accepted by the validator.

This meets the structured-output, validation, evidence, metadata, versioning and maintainability requirements.

## 3. Authentication and authorization

`app/security/auth.py` has two adapters:

1. `DemoAuthenticator` maps four fixed local tokens to principals.
2. `EntraJwtAuthenticator` fetches Entra signing keys and validates signature, algorithm, audience, issuer, expiry and required claims. Group-overage claims fail closed rather than silently losing ACLs.

The client never sends a group or department in the query body. `build_odata_security_filter()` in `app/security/guards.py` uses only validated `Principal.groups` and produces an Azure filter equivalent to:

```text
allowed_groups intersects verified_token_groups AND is_current = true
```

In local retrieval, `current_authorized_chunks()` runs before embedding/scoring. In Azure, the filter is sent with `vectorFilterMode=preFilter`. Therefore an HR chunk cannot become a candidate for an Engineering user and cannot reach reranking, prompt construction, logs or citations.

## 4. Ingestion path

`POST /v1/ingest` requires `knowledge.ingest` and calls `IngestionService.ingest()`:

1. `DocumentParser` removes unsafe nulls, normalizes line endings/blank space and preserves headings, lists and tables.
2. The complete normalized document plus trusted metadata is hashed. The hash is the idempotency identity.
3. `StructureAwareChunker` splits by headings and semantic blocks. The token setting is a safety ceiling; it does not replace structural boundaries. Oversized blocks use sentence/word windows with bounded overlap.
4. Every chunk receives provenance, ACL, classification, version, effective date and checksum.
5. `scan_untrusted_text()` labels injection/exfiltration/tool-coercion indicators.
6. All chunks are embedded before the index commit. An embedding failure leaves no partially searchable document.
7. The local index makes exact retries `duplicate`, rejects changed content under the same version, returns `stale` for older versions and deactivates superseded chunks when a newer effective version commits.

The production design extends this with Blob quarantine, safe PDF/DOCX/OCR parsing, Service Bus, a DLQ/idempotency ledger, inactive-first indexing and reconciliation.

## 5. Query path

`POST /v1/query` calls `KnowledgeService.ask()` in `app/service.py`.

### 5.1 Request policy

Before any provider call, deterministic rules detect direct instruction override, credential exfiltration, obviously underspecified questions, unsupported personal/software scope and unsupported future-count predictions.

- dangerous requests return `refused`;
- ambiguous requests return `clarification_required`;
- unsupported/missing-knowledge requests return `insufficient_evidence`.

These rules reduce cost and attack surface, but they are not presented as the only injection defense.

### 5.2 Retrieval with bounded failure handling

The service calls the retriever through `retry_async()` in `app/resilience.py`:

- each attempt has a hard timeout;
- only transient provider/time-out failures retry;
- attempts and exponential delay are bounded;
- permanent validation/auth/output errors are not retried.

If Search remains unavailable, the service returns `degraded` with no citations and **does not call a model without evidence**.

### 5.3 Local hybrid retrieval

`LocalHybridRetriever.search()` is a deterministic Azure-mappable test double:

1. authorization/current-version prefilter;
2. keyword score over canonicalized terms;
3. deterministic hashing-vector cosine score;
4. coverage/title/heading reranker-like score;
5. weighted fused ranking and top-k.

The hashing embedder is intentionally not claimed as a production semantic model. `AzureAiSearchStore.search()` sends text and vector queries together, uses `k=50` for semantic ranking input, applies the ACL prefilter and returns Search/semantic scores with the chunk.

### 5.4 Evidence gate

`EvidenceGate.assess()` is the critical boundary between retrieval and generation:

- injection-marked chunks are removed from model context;
- relevance coverage is calculated per chunk, not over an unrelated concatenation;
- weak hits are removed relative to the best authorized hit;
- equal-authority sources with a shared conflict key and different assertions cause `clarification_required` with both citations;
- lower-authority conflicts may be ignored with a warning.

The per-chunk rule was added after evaluation exposed that unrelated partial matches could otherwise manufacture apparent support.

### 5.5 Model routing and generation

`ModelRouter` demonstrates the requested routing signals:

- no LLM for policy refusals, ambiguity, evidence failure and conflict;
- smaller hosted model for short, bounded synthesis;
- frontier model for long/multi-document synthesis;
- private model route for restricted evidence.

Local mode uses `DeterministicGroundedGenerator` for reproducibility. It extracts supported facts and preserves ordered workflows. Azure mode uses `AzureOpenAIGroundedGenerator`, which:

- sends only the already-authorized safe context;
- labels evidence as untrusted data;
- exposes no tool definitions;
- sets temperature to zero;
- requests the strict `AnswerDraft` JSON schema; and
- uses managed identity rather than an API key.

### 5.6 Output validation

The model does not get the final word. `_validate_draft()` verifies:

- at least one citation exists for an answer;
- every cited chunk ID is a subset of the exact authorized context;
- output does not match sensitive credential/private-key patterns.

Invalid structured output becomes `degraded`; it is never returned as plausible prose. Citations expose document, immutable version, chunk, URI and excerpt.

## 6. Prompt-injection defense in depth

The malicious demo document contains the challenge’s exact indirect instruction and a fake canary. Defenses are layered:

1. ACL filtering prevents unauthorized data from entering any later layer.
2. Retrieved text is always treated as untrusted.
3. Risk scanning removes the seeded malicious chunk from context.
4. Minimal context limits exposure.
5. The model has no tools, Search identity, credential store or arbitrary URL ability.
6. The system prompt declares evidence data-only and the provider must return a schema.
7. Citation membership and sensitive-output validation run after the model.
8. Tests assert the canary and forbidden document facts never leak.

Heuristic scanners can be bypassed by novel obfuscation. The design reduces impact; it does not claim prompt injection is solved.

## 7. Observability and cost

`app/observability.py` records retrieval, evidence-gate, model and output-validation stage latency, behavior/provider counters, token estimates and cost. `KnowledgeService` emits one structured request event containing request ID, hashed subject/question, status, route, latency, usage/cost and citation count.

Raw questions, prompts and retrieved text are intentionally absent. If `APPLICATIONINSIGHTS_CONNECTION_STRING` is set with the observability extra installed, Azure Monitor OpenTelemetry exports the same spans/metrics. Production dashboards and alerts are specified in the architecture document.

## 8. Automated tests and evaluation

The 23 tests under `tests/` cover:

- structure-aware chunk limits and table preservation;
- authorization filtering and Azure filter construction;
- current-version citations and poor-match refusal;
- equal-authority conflict handling;
- direct/indirect injection and admin-only debug;
- duplicate/stale/conflicting ingestion;
- Search/model failures and transient retries;
- invalid/unauthorized model citations; and
- API success, 401, 403, 422 and local failure injection.

`evaluation/dataset.jsonl` adds 17 representative end-to-end cases. `scripts/evaluate.py` reports retrieval recall, answer correctness, citation correctness, groundedness proxy, behavior/refusal/security pass, latency, tokens and cost, then compares them to absolute and frozen baseline gates.

Current verified local result: 23/23 tests and 17/17 evaluation cases pass. This proves deterministic behavior on the checked-in corpus, not production model quality.

## 9. Azure production mapping

`infra/main.bicep` provisions a private-default reference environment with Container Apps, Search, Azure OpenAI, Storage, Key Vault, managed identities/RBAC, private endpoints/DNS, Log Analytics and Application Insights. `infra/search-index.json` supplies the data-plane schema. The code uses `DefaultAzureCredential` for Search/OpenAI tokens.

The template intentionally does not fabricate model deployments, Entra registration, APIM/WAF, ACR/image publication, enterprise DNS, Azure Monitor Private Link, budgets/policy assignments or an executed deployment. Those gaps and exact validation/what-if steps are listed in `infra/README.md`.

## Requirement summary

| Requirement | Core mechanism |
|---|---|
| Production RAG | Versioned structure-aware ingestion, hybrid retrieval, evidence gate, grounded citations |
| Access control | Entra principal -> Search prefilter before scoring/context |
| Injection/security | Untrusted evidence, scan, no tools/secrets, output/citation validation, red-team tests |
| Evaluation | Labeled slices, deterministic metrics, baseline deltas and CI gate |
| Observability/cost | Stage spans, counters, token/cost estimates, content-free structured logs |
| Reliability | Timeouts, transient-only retry, atomic/idempotent ingestion, safe degraded responses |
| Model strategy | no-LLM/small/frontier/private routes with explicit trade-offs |
| Tests | 23 automated tests with deterministic provider doubles |
| Production design | Secure Azure diagram, Bicep, Search schema, CI/CD and four ADRs |

## How to explain it in the review

Start with the two hardest invariants, not the framework names:

1. “Authorization is enforced inside retrieval, so hidden text cannot reach the model.”
2. “Generation occurs only after evidence quality/security checks, and its citations are validated afterward.”

Then demonstrate one normal answer, the same confidential question under two principals, the malicious canary, a Search outage, retrieval debug and one evaluation limitation. The timed sequence is in `docs/walkthrough-script.md`.

