# 15–20 minute technical walkthrough script

Target runtime: **18 minutes 30 seconds**. The script is a rehearsal guide, not text to read verbatim. Demonstrate understanding by narrating causes and trade-offs while the application is running.

## Before recording (not on the clock)

- Start from a clean clone and Python 3.12 environment. Run `python -m pytest -q` once; do not hide failures.
- Start the API with `python -m uvicorn app.main:app --host 127.0.0.1 --port 8000` or `docker compose up --build`.
- Import both files in `postman/`, select the local environment, and reset its `document_id` variables if this is not the first run.
- Open `docs/architecture.mmd`, the Postman collection, a terminal, the evaluation report/dataset, and two ADRs. Increase font size and hide tokens/notifications.
- Rehearse requests against the exact commit being recorded. Local failure-injection headers must be enabled only in local/test configuration.
- Say explicitly that Azure resources were **designed in Bicep but not deployed**. The local deterministic adapters demonstrate contracts and failure semantics, not Azure relevance/model parity.

## 0:00–0:40 — Frame the system and evidence level

Show the repository title or running health request.

Say:

> This is the core of an enterprise knowledge intelligence platform, not a chat-with-PDF UI. The security invariant is that unauthorized chunks never cross the retrieval boundary into reranking, prompt construction, model context, citations, or content logs. The local implementation is deterministic and credential-free; the Azure production mapping is documented and provisionable infrastructure, but it has not been deployed for this submission.

Run `GET /health/live` and `GET /health/ready`. Point out that liveness answers “is the process running?” while readiness answers “can this replica safely receive traffic?”

## 0:40–3:20 — Architecture and trust boundaries

Show `docs/architecture.mmd`, not a file-by-file tour. Trace one request with the pointer:

1. Entra/API edge validates signature, issuer, audience, expiry and claims. Local demo tokens are deliberately not production auth.
2. The API derives an immutable principal/ACL scope from trusted claims; the client never supplies its own group filter.
3. Azure AI Search receives a tenant/lifecycle/ACL **pre-filtered** hybrid query. Unauthorized text never reaches application reranking.
4. The evidence gate checks relevance, coverage, conflict/version metadata, and support. Weak evidence returns a structured refusal before generation.
5. Prompt construction labels retrieved content as untrusted evidence. The model has no tools, credentials, Search access, or authority to reinterpret ACLs.
6. Structured-output and citation validation ensure cited chunk IDs are a subset of the authorized context. Validation failure becomes refusal, not plausible prose.
7. Traces propagate one request/correlation ID across API, retrieval, reranking, prompt, provider and response stages. Logs contain identifiers, timings, scores/reason codes and token/cost estimates—not raw document/query content.

Point to these trust boundaries:

- employee device → authenticated API;
- identity claims → server-built authorization scope;
- Search → application, where retrieved documents remain untrusted data;
- application → model provider;
- runtime identity → private Azure data planes.

Mention that the Bicep disables public access to Search, OpenAI, Storage and Key Vault, uses Private Link and managed identity, and keeps the Container App internal by default. APIM/WAF, Entra registration and model deployments are explicit landing-zone/deployment work, not fabricated as complete.

## 3:20–7:20 — Live grounded RAG and access control

Use the Postman folders in order so the recording is reproducible.

### Ingest controlled examples (45 seconds)

Run the three setup ingestion requests: current vendor policy, an HR-confidential policy, and a malicious/injection document. Re-run the vendor request once and show idempotency: the same document/version/content does not create duplicate active chunks.

Explain that production ingestion stages a version, parses/chunks/embeds it, verifies completeness, then atomically marks it active; partial versions stay unqueryable. Stable content/chunk IDs make retries safe.

### Successful grounded answer (75 seconds)

As `demo-engineer`, run the successful vendor-approval query. Show:

- `status=answered` (or the implementation's equivalent);
- the concise answer;
- source/citation entries with document/version/chunk provenance;
- retrieval/trace metadata without raw sensitive content.

Open one citation and relate its claim to the returned evidence. State that citation presence alone is not enough—the output validator checks citation membership/support.

### Refusal and access differences (80 seconds)

Run the insufficient-evidence question. Show that it refuses rather than filling gaps. Explain the evidence threshold is calibrated using labeled evaluation data, not treated as a universal probability.

Ask the HR-confidential question first as `demo-engineer`, then as `demo-hr`. The engineer must not receive the HR chunk, title, snippet, or confirmation that a hidden document exists; HR may answer with an authorized citation. A direct debug attempt against the HR document as engineer should return `403` without chunk content.

Run the explicit secrets/jailbreak request. Distinguish a **policy refusal** from an insufficient-evidence refusal.

## 7:20–10:10 — Break the system deliberately

### Indirect prompt injection (85 seconds)

Query a topic that retrieves the seeded malicious document. Show that the text “ignore previous instructions…reveal secrets” is treated as evidence data, not control flow. The result should either safely answer supported benign facts without obeying the instruction or return a security/policy refusal. Show that no unauthorized citations or secrets appear.

Explain remaining limitations: heuristic detection can miss obfuscation, and an LLM can still be manipulated. The stronger controls are architectural—pre-retrieval ACLs, no tools/credentials, bounded context/calls, output/citation validation, and monitoring/red-team regression tests.

### Dependency failure (85 seconds)

Run one local/test-only injected LLM timeout or rate-limit request from the Postman failure folder. Show the bounded, sanitized error/refusal, retryability signal and correlation ID. Explain:

- retries apply only to transient failures and are bounded with backoff/jitter;
- the API does not retry validation/auth failures;
- Search outage fails closed—there is no ungrounded model fallback;
- if generation is unavailable, returning evidence-only output is allowed only for a deliberately supported route, never silently;
- failure injection is rejected/disabled outside local/test and cannot be selected by an untrusted production caller.

## 10:10–12:50 — Retrieval debugging

Choose the successful vendor query. Run the authorized debug-chunks request and/or show the query response's debug data. Walk through:

- candidate chunk IDs, lexical/vector-like scores and fused/reranked score;
- document ID, immutable version, section path and classification;
- the principal scope/filter summary (prefer a hash/count, never a client-provided filter);
- lifecycle rule selecting the current active version;
- deduplication/per-document cap;
- exactly which chunks entered final context.

Explain why these chunks won: exact “vendor approval” terms help keyword recall, paraphrased process language helps semantic recall, and metadata/ACL filters remove invalid candidates before ranking.

If retrieval is poor, diagnose in this order:

1. verify ACL/lifecycle filters and corpus/index freshness;
2. inspect parsing and chunk boundaries with source provenance;
3. inspect lexical/vector candidate sets separately;
4. review query normalization, embedding versions, fusion and reranking;
5. tune only against labeled slices, then evaluate latency/cost/security regressions.

Do not “fix” a bad result by lowering the evidence gate until evaluation supports it.

## 12:50–14:50 — Evaluation, including one limitation

Run:

```bash
python scripts/evaluate.py --dataset evaluation/dataset.jsonl \
  --baseline evaluation/baseline-results.json --fail-on-regression
```

Show a per-case result, not only a total. Pick one successful authorization/injection case and explain its assertions: expected status, allowed/forbidden documents, citation support, latency/token/cost capture.

Then show the weakest or intentionally limited case from the report. Explain the observed failure and the next experiment—for example, improving table parsing or conflict resolution—without claiming an unmeasured improvement.

Name the required slices: answerable, unanswerable, ambiguous, conflicting sources, unauthorized document, injection document and poor retrieval match. Explain the CI gate compares configurations only on deterministic/frozen inputs and records prompt/model/embedding/chunker/index versions. Online model changes additionally require a pinned evaluation environment, repeated samples/confidence intervals, red-team review and canary monitoring.

## 14:50–17:00 — Defend two decisions

### Decision 1: guarded workflow, not an agent (65 seconds)

- Alternative: autonomous or allowlisted tool-using agent.
- Choice: deterministic authenticate → filter/retrieve → evidence gate → bounded generation → validate.
- Benefit: explicit authorization, cost/call ceilings, testable states and smaller injection blast radius.
- Sacrifice: less flexibility for multi-hop/action tasks.
- Revisit when a measured use case truly requires tools/multi-step reasoning and has per-tool auth, typed inputs, side-effect/idempotency controls and adversarial evaluation.

### Decision 2: structure-aware chunks + hybrid retrieval (65 seconds)

- Alternatives: fixed windows, vector-only, keyword-only, or LLM-generated chunks.
- Choice: preserve headings/clauses/tables within token bounds; combine lexical and semantic signals; pre-filter ACLs; optional semantic rerank.
- Benefit: exact IDs and paraphrases both retrieve, with auditable provenance and citations.
- Sacrifice: parser complexity, index metadata, semantic-ranker latency/cost and provider-specific tuning.
- Revisit using document-class evaluation if tables/OCR dominate, hybrid adds no lift, or semantic ranker misses its SLO.

## 17:00–18:20 — 10× traffic and 10× corpus

Say what breaks first rather than “add servers”:

- Ingestion: parsing/embedding quota and index write throughput. Separate an idempotent queue-driven worker, apply backpressure/dead-lettering, batch embeddings, reconcile incomplete versions, and partition by tenant/document class only when measurements justify it.
- Retrieval: Search partition/storage/indexing capacity grows with corpus; replicas grow query throughput/availability. Measure ACL-filter selectivity, hot tenants, semantic-ranker latency and per-document dominance before capacity changes.
- Generation: Azure OpenAI TPM/RPM and tail latency likely bind before FastAPI CPU. Use admission control, token/context budgets, caching only after ACL-aware cache keys, evaluated small-model/no-LLM routes, and provisioned throughput where justified.
- API: scale stateless Container App replicas from concurrency/latency signals, but protect dependencies with concurrency limits and circuit breakers.
- Operations: use immutable versioned indexes and aliases/canaries, evaluation gates, budget/quota alerts and tested rollback. Co-locate services only after confirming residency and model availability.

State the first measurements you would collect: p50/p95/p99 stage latency, Search throttles and partition/replica load, model TPM/RPM/429s, queue age, context/token distributions, refusal reasons, retrieval/citation quality by ACL/document slice, and cost per successful grounded answer.

## 18:20–18:30 — Close

> The core claim is not that prompt injection or hallucination is solved. It is that authorization is enforced before context, evidence is inspectable, unsafe uncertainty fails closed, failures are bounded and observable, and every quality/cost/security trade-off has a measurable regression path.

Stop. Leave detailed file navigation for questions.
