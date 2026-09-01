# ADR 0001: Use ACL-prefiltered hybrid retrieval with semantic reranking

- Status: Accepted
- Date: 2026-08-31
- Decision owners: AI platform team
- Scope: Online retrieval

## Context

Enterprise questions mix exact identifiers (policy numbers, clause names, product codes and acronyms) with conceptual language. Vector search is good at paraphrase but can miss exact tokens and can return topically similar, factually irrelevant passages. BM25 keyword search has the inverse profile. Retrieval must also enforce tenant and document permissions *before* a candidate can become model context, and it must expose enough evidence to support citations and refusal decisions.

The design must map directly to Azure AI Search while remaining executable with deterministic local adapters.

## Options considered

1. **Vector-only retrieval.** Simple query path and good semantic recall. It is weak for identifiers, rare terms and embedding drift; an embedding failure makes the whole query path unavailable.
2. **Keyword-only BM25.** Fast, explainable and strong for exact language. It performs poorly on paraphrases and vocabulary mismatch.
3. **Hybrid BM25 + vector retrieval, fused and optionally semantically reranked.** Better aggregate recall, but adds tuning, latency and Azure semantic-ranker cost.
4. **Retrieve broadly and filter ACLs in the application.** Rejected categorically: unauthorized text would cross the retrieval boundary and could leak through logs, reranking or model context.

## Decision

Use one Azure AI Search request containing:

- a lexical query over title, headings and chunk text;
- a vector query over the chunk embedding;
- reciprocal-rank fusion (Azure hybrid search);
- `vectorFilterMode=preFilter` with a server-constructed tenant, lifecycle and ACL predicate; and
- semantic ranking for eligible production traffic, after security filtering, with captions used only as ranking signals—not as authoritative evidence.

The filter is derived only from validated Entra claims and trusted document metadata. Clients cannot submit filter syntax or principal IDs. Conceptually:

```text
tenant_id == principal.tenant_id
AND is_active == true
AND (
  visibility == "company"
  OR allowed_principal_ids contains principal.object_id
  OR allowed_group_ids intersects principal.transitive_group_ids
)
```

The query service deduplicates overlapping chunks, limits per-document dominance, retains source/version metadata, and runs an evidence gate. Low relevance, poor coverage, missing citation support or unresolved authoritative conflicts produce a structured refusal rather than a best-effort answer.

The local adapter implements deterministic lexical/vector-like scoring and the same filter contract. It is a behavioral test double, not a claim of score parity with Azure AI Search.

## Trade-offs

- **Quality:** Higher recall across exact and semantic queries, at the cost of more parameters to evaluate (`k`, semantic top-N, fusion and evidence thresholds).
- **Latency:** Embedding and semantic reranking add network calls. They are independently budgeted and traced; semantic ranking can be shed under overload without removing ACL filtering.
- **Cost:** Semantic ranking and query embeddings cost more than BM25 alone. Simple exact-lookup routes may skip generation, but never security filtering.
- **Security:** Pre-filtering sharply reduces exposure. It depends on correct group synchronization and index metadata; stale ACLs remain an operational risk addressed by fail-closed ingestion and reconciliation.
- **Portability:** Search query construction is isolated behind a retrieval port, but semantic behavior and scoring remain provider-specific.

## Consequences

- Every retrieval test must assert both relevance and absence of unauthorized chunk IDs.
- Search index changes require versioned schemas and offline evaluation before promotion.
- Thresholds are calibrated by query class; Azure scores are not treated as probabilities.
- Search telemetry records query class, filter hash, result IDs and scores—not raw queries or content.
- If semantic ranking is unavailable, the supported fallback is fused BM25/vector results with a stricter evidence gate; if Search itself is unavailable, the request fails closed with a retryable `503`.

## When to revisit

Revisit if evaluation shows no measurable hybrid lift, semantic-ranker latency breaches the SLO, the corpus becomes primarily structured data, or tenant scale requires isolated indexes/shards. A learned reranker is justified only after a labeled dataset demonstrates material benefit over Azure semantic ranking.
