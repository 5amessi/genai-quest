# Enterprise Knowledge Intelligence Platform

A security-first, production-oriented RAG reference implementation for the Kentrick Senior AI Engineer hiring quest. It is deliberately more than “chat with a PDF”: the code enforces identity-derived access control before retrieval, treats retrieved text as untrusted, refuses weak evidence, detects unresolved source conflicts, validates citations, records privacy-safe telemetry, and fails closed when providers are unavailable.

The default local mode is deterministic and requires no cloud account. Azure AI Search, Azure OpenAI, Entra ID, managed identity, OpenTelemetry/Application Insights, Container Apps, and Bicep mappings are included without claiming that cloud resources were deployed.

## Submission index

This README is the single entry point for the submission. Detailed artifacts remain in focused files and folders so architecture, implementation, evaluation, security, and operational concerns can be reviewed independently without duplicating content.

| Deliverable | Review link | Status |
|---|---|---|
| Source code | [`app/`](app/) | Complete and locally verified; publish from a reviewed Git commit/tag |
| Setup, assumptions, configuration and limitations | [`README.md`](README.md) | Complete |
| Code-first walkthrough | [`docs/code-walkthrough.md`](docs/code-walkthrough.md) | Complete |
| Production architecture and security boundaries | [`docs/architecture.md`](docs/architecture.md) | Complete |
| Editable architecture diagram | [`docs/architecture.mmd`](docs/architecture.mmd) | Complete; render to PNG/PDF before submission if the portal cannot render Mermaid |
| Threat model and prompt-injection analysis | [`docs/threat-model.md`](docs/threat-model.md) | Complete |
| Evaluation methodology and conclusions | [`docs/evaluation-report.md`](docs/evaluation-report.md) | Complete |
| Evaluation dataset, baseline and per-case results | [`evaluation/`](evaluation/) | Complete; 17/17 deterministic cases passing |
| Automated test suite | [`tests/`](tests/) | Complete; 23/23 tests passing |
| API documentation | [`docs/api.md`](docs/api.md) | Complete |
| Postman collection and local environment | [`postman/`](postman/) | Complete |
| Architecture Decision Records | [`docs/adr/`](docs/adr/) | Complete; four ADRs provided |
| Azure infrastructure and Search schema | [`infra/`](infra/) | Complete as an unexecuted reference design; not Azure-deployed |
| CI quality and evaluation gates | [`.github/workflows/ci.yml`](.github/workflows/ci.yml) | Complete |
| AI Usage Report | [`AI_USAGE_REPORT.md`](AI_USAGE_REPORT.md) | Complete disclosure; candidate review/update required before submission |
| Technical walkthrough video | Not provided | Intentionally omitted; the written walkthrough and reproducible demo assets do not claim to satisfy the video requirement |
| Written recording/demo guide | [`docs/walkthrough-script.md`](docs/walkthrough-script.md) | Complete supplementary artifact |

### Recommended reviewer path

1. Read the [code walkthrough](docs/code-walkthrough.md) for the end-to-end request and ingestion flows.
2. Review the [production architecture](docs/architecture.md), [diagram](docs/architecture.mmd), and [threat model](docs/threat-model.md).
3. Run the automated tests and deterministic evaluation using the commands below.
4. Start the local API and exercise the supplied [Postman collection](postman/Kentrick-Knowledge-Platform.postman_collection.json).
5. Inspect the four [ADRs](docs/adr), [Azure reference infrastructure](infra), and [AI Usage Report](AI_USAGE_REPORT.md).

### Evidence level

- The local deterministic application, tests, evaluation, API examples, package build, and JSON assets were executed or validated during preparation.
- The Azure adapters, Bicep, private-network design, managed-identity/RBAC model, and Search schema are production mappings; no Azure deployment is claimed.
- The required technical video is not included. The omission should remain explicit in any submission email or portal entry.

## Quick start

Prerequisites: Python 3.11+ (3.12 used in CI) or Docker.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install --editable ".[dev]"
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

The local defaults load the versioned demo corpus in [`data/demo`](data/demo) and enable four explicit demo bearer tokens:

| Token | Groups/roles | Purpose |
|---|---|---|
| `demo-engineer` | `company-all`, `engineering` | Public policies + engineering docs |
| `demo-hr` | `company-all`, `hr` | Public policies + HR-confidential docs |
| `demo-finance` | `company-all`, `finance` | Demonstrates conflicting authorized sources |
| `demo-admin` | all demo groups + `knowledge.ingest`, `knowledge.debug` | Ingestion and retrieval debugging |

Example:

```bash
curl -s http://127.0.0.1:8000/v1/query \
  -H "Authorization: Bearer demo-engineer" \
  -H "Content-Type: application/json" \
  -d '{"question":"What is our workflow for approval of an enterprise vendor?"}'
```

Docker is equally self-contained:

```bash
docker compose up --build
```

Import the collection and environment from [`postman`](postman) for successful, invalid, unauthorized, insufficient-evidence, ACL, injection, debug, idempotency, and dependency-failure examples.

## Architecture overview

```text
Employee -> Entra/APIM -> FastAPI guarded workflow
                              |
validated claims -> Principal -> ACL prefilter -> hybrid Search -> evidence gate
                                                               -> bounded model
                                                               -> output/citation validator
                                                               -> answer or typed non-answer

Publisher -> Blob quarantine -> Event Grid/Service Bus -> ingestion worker
          -> parse/normalize -> structure-aware chunks -> embeddings -> versioned index
```

The production diagram, trust boundaries, scaling, model routing, reliability, cost, deployment and data-residency design are in [`docs/architecture.md`](docs/architecture.md); editable Mermaid is in [`docs/architecture.mmd`](docs/architecture.mmd). The design follows Azure AI Search’s hybrid pattern—text and vector retrieval in one request with rank fusion—and applies a prefilter built only from validated identity claims. Microsoft’s current guidance describes [hybrid search](https://learn.microsoft.com/en-us/azure/search/hybrid-search-overview), [vector filters](https://learn.microsoft.com/en-us/azure/search/vector-search-filters), [semantic ranking](https://learn.microsoft.com/en-us/azure/search/semantic-search-overview), and [managed identity for Container Apps](https://learn.microsoft.com/en-us/azure/container-apps/managed-identity).

For a code-first explanation in execution order, use [`docs/code-walkthrough.md`](docs/code-walkthrough.md).

### Online request flow

1. [`app/security/auth.py`](app/security/auth.py) validates a demo token locally or an Entra JWT in production. Groups/roles come from the verified token, never request JSON.
2. [`app/service.py`](app/service.py) applies deterministic request policy. Direct exfiltration/jailbreak requests stop before retrieval.
3. [`app/retrieval/local.py`](app/retrieval/local.py) filters to current chunks whose `allowed_groups` intersect the principal, then computes keyword, vector and reranker-like signals. [`app/adapters/azure.py`](app/adapters/azure.py) maps the same port to an Azure Search hybrid/semantic query with `vectorFilterMode=preFilter`.
4. [`app/security/guards.py`](app/security/guards.py) removes injection-marked chunks, requires relevance within individual evidence chunks, and detects equal-authority contradictions.
5. [`app/generation/local.py`](app/generation/local.py) provides a deterministic reviewer path. The Azure adapter sends only filtered evidence, declares it untrusted data, exposes no tools, and requests strict structured output.
6. [`app/service.py`](app/service.py) verifies every cited chunk was in authorized context, rejects sensitive-looking output, calculates usage/cost, and returns `answered`, `insufficient_evidence`, `refused`, `clarification_required`, or `degraded`.

### Ingestion flow

[`app/ingestion/service.py`](app/ingestion/service.py) normalizes content, hashes the entire input and trusted metadata, chunks on headings/paragraphs/tables with a token ceiling, scans every chunk for injection indicators, embeds all chunks, and only then performs one index commit. [`app/retrieval/local.py`](app/retrieval/local.py) makes duplicate retries no-ops, rejects a reused version with changed content, marks superseded versions inactive, and never exposes a partially embedded document.

The production worker adds Blob quarantine, malware/safe parsing, a Service Bus DLQ, an idempotency ledger, inactive-first indexing and reconciliation; see [`docs/architecture.md`](docs/architecture.md).

## Requirement traceability

| Quest requirement | Evidence in this submission |
|---|---|
| 1. Production RAG | Parser/chunker/metadata/versioning in `app/ingestion`; hybrid retrieval in `app/retrieval` and Azure adapter; citations/evidence refusal in `app/service.py`; demo versions/corpus in `data/demo` |
| 2. Enterprise access control | Immutable `Principal`; token validation; group filter inside retrieval before scoring/context; admin-only ingest/debug; paired Engineer/HR leakage tests |
| 3. Prompt injection | Direct request rules, indirect-content scan/quarantine, untrusted-context contract, no tools, output/schema/citation validation, canary red-team tests, documented residual risk |
| 4. Evaluation | 17-case [`evaluation/dataset.jsonl`](evaluation/dataset.jsonl), deterministic runner, frozen baseline/results, metrics, thresholds, failure analysis and CI gate |
| 5. Observability/cost | Stage latency, refusal/provider counters, token/cost estimates, hashed subject/question logs, optional OpenTelemetry/Application Insights export; no raw prompt/chunk logs |
| 6. Reliability | Per-provider deadlines, bounded exponential retry for transient errors only, fail-closed Search behavior, degraded model behavior, atomic local indexing, duplicate/version conflict handling |
| 7. Model strategy | Cost/privacy/complexity router in `app/generation/local.py`; detailed route matrix in architecture docs; deterministic local, small/frontier/private/no-LLM routes |
| 8. Automated tests | Unit/API/adversarial tests under [`tests`](tests) for chunking, ACLs, versioning, grounding, refusal, injection, retries, provider failures, structured citations and validation |
| 9. Azure production design | Diagram, trust boundaries, secure Bicep, Search schema, Entra, managed identity/RBAC, private endpoints, Key Vault, scaling, CI/CD, rollback, versioning and residency considerations |
| ADRs | Four records under [`docs/adr`](docs/adr), including hybrid retrieval, structure-aware chunking, deterministic workflow and Azure Search |
| API collection | Postman v2.1 collection + local environment under [`postman`](postman) |
| Technical walkthrough | Written code walkthrough and timed recording script are provided; the required video is explicitly not included |
| AI usage policy | Honest disclosure, prompts, AI-generated areas and verification steps in [`AI_USAGE_REPORT.md`](AI_USAGE_REPORT.md) |

## Testing and evaluation

```bash
pytest -q
ruff check app tests scripts
python scripts/evaluate.py \
  --dataset evaluation/dataset.jsonl \
  --baseline evaluation/baseline-results.json \
  --output evaluation/results.json \
  --fail-on-regression
```

The verified local run currently has 23/23 automated tests and 17/17 evaluation cases passing. These are deterministic reference results, not Azure production SLOs. See [`docs/evaluation-report.md`](docs/evaluation-report.md) for methodology, metrics, initial failures, corrections, results and limitations.

## Configuration

All variables and safe local defaults are documented in [`.env.example`](.env.example). Important controls:

- `APP_ENV`, `BACKEND`, `AUTH_MODE`: `AUTH_MODE=demo` is rejected outside `local`/`test`.
- `MAX_CHUNK_TOKENS`, `CHUNK_OVERLAP_TOKENS`: structure remains primary; these are safety ceilings, not arbitrary boundary rules.
- `RETRIEVAL_TOP_K`, `MIN_RETRIEVAL_SCORE`, `MIN_EVIDENCE_COVERAGE`: must be recalibrated whenever retrieval changes.
- provider deadlines/retries and example cost rates are explicit.
- Azure endpoint/deployment/index/API versions are configurable; managed identity supplies data-plane credentials.
- `ALLOW_FAILURE_INJECTION` works only in local/test and defaults off.

For Azure mode, install `.[azure,observability]`, provision the resources, create the index from [`infra/search-index.json`](infra/search-index.json) after setting its vector dimensions to match the selected embedding deployment, assign least-privilege data-plane roles, set `BACKEND=azure` and `AUTH_MODE=entra`, and configure the remaining variables. [`infra/README.md`](infra/README.md) intentionally lists what still belongs to the landing zone rather than pretending a single template completes enterprise governance.

## Design decisions

- A deterministic workflow was selected over an agent because the use case needs bounded calls, explicit authorization and testable failure states more than autonomous tool use.
- Structure-aware chunks preserve headings, clauses, lists and short tables; the token limit prevents pathological context growth. See [ADR 0002](docs/adr/0002-structure-aware-chunking.md).
- Hybrid retrieval retains exact identifiers and semantic recall. Production adds Azure semantic reranking after the ACL-prefiltered candidate set. See [ADR 0001](docs/adr/0001-hybrid-retrieval.md).
- Azure AI Search is a rebuildable serving index, not the source of truth. Blob/metadata catalog remain authoritative. See [ADR 0004](docs/adr/0004-azure-ai-search.md).
- A confident hallucination is worse than a typed refusal. Coverage, relevance, conflict, provider and citation failures therefore stop generation or invalidate its result.

## Assumptions

- A trusted publishing workflow owns document ACL, classification, authority and effective-date metadata; document prose cannot grant itself access.
- Extracted text is the API boundary for the core sample. Production uses a sandboxed parser/Document Intelligence path for PDF/DOCX/OCR/table extraction.
- One enterprise tenant is represented in the runnable sample; the production index adds tenant filtering as shown in the architecture.
- Demo tokens, hashing embeddings and deterministic generation exist for reproducibility only; no local result claims Azure/OpenAI quality parity.
- The provided pricing numbers are configuration examples, not current commercial quotes.

## Known limitations and production improvements

- The local hashing embedder is lexical-semantic scaffolding, not a foundation-model embedding. Calibrate Azure embeddings/reranking against the labeled corpus before release.
- Injection pattern matching can miss obfuscation. The blast radius is reduced by ACL prefiltering, no tools/credentials, minimal context, structured output and canary tests, but prompt injection is not “solved.”
- Conflict detection uses trusted metadata (`conflict_key`, `assertion`, `authority_rank`) rather than open-ended natural-language contradiction detection.
- The local index is process memory. Production needs the documented durable queue/ledger/source-of-truth and inactive-first activation protocol.
- Token counting and cost are estimates; production should use provider usage and approved price/version tables.
- The Bicep is a reference baseline and has not been deployed here. Entra registration, APIM/WAF, DNS, RBAC review, model deployment, policy assignments, budgets and residency approval remain deployment work.
- The required 15–20 minute technical video is not provided. The written walkthrough, Postman collection, evaluation evidence, and recording script are supplementary and do not claim to replace it.

## Repository map

```text
app/                 FastAPI, domain workflow, security, ingestion, retrieval, providers
data/demo/           Versioned public/confidential/restricted/adversarial sample corpus
tests/               Deterministic unit and API tests
evaluation/          Labeled JSONL, baseline and verified result snapshot
scripts/evaluate.py  Offline evaluator and regression gate
docs/                Architecture, threat model, evaluation report, API, ADRs, walkthrough
infra/               Azure Bicep, Search schema, parameters and deployment notes
postman/             API collection and local environment
.github/workflows/   Test/lint/evaluation/container CI
```
