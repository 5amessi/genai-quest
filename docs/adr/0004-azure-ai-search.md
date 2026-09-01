# ADR 0004: Use Azure AI Search as the production retrieval system

- Status: Accepted
- Date: 2026-08-31
- Decision owners: AI platform team
- Scope: Production search storage and serving

## Context

The production corpus needs lexical, vector and semantic retrieval, filterable per-chunk ACL metadata, operational support on Azure, predictable scaling and private networking. The challenge implementation must also run without Azure credentials, so production infrastructure must sit behind a narrow application port.

## Options considered

1. **Azure AI Search.** Native hybrid/semantic search, vector pre-filtering, index aliases, managed identity and private endpoints; higher service cost and Azure-specific query behavior.
2. **PostgreSQL + pgvector.** Consolidates metadata and vectors and offers transactional control. Hybrid ranking, semantic reranking, ACL-aware performance tuning and horizontal search operations require more team ownership.
3. **Azure Cosmos DB vector search.** Attractive when operational data already lives in Cosmos DB, but lexical/semantic search capabilities and relevance tooling are a weaker fit for this document-search workload.
4. **Self-hosted OpenSearch/Milvus/Qdrant.** High control and potential portability, but substantially more patching, scaling, backup and security operations.

## Decision

Use Azure AI Search for the production chunk index. The schema makes `tenant_id`, lifecycle/version fields, visibility and ACL collections filterable; source/version/citation metadata is retrievable; text is searchable; and embedding vectors are stored in a versioned vector field. Queries use pre-filtered hybrid retrieval as defined in [ADR 0001](0001-hybrid-retrieval.md).

The application accesses Search with a managed identity and a data-plane role scoped to the required index operations. Query and ingestion workloads use distinct identities: the API is read-only; the ingestion worker can write. Public network access is disabled and traffic uses a private endpoint with private DNS.

The `Retriever` and indexing ports isolate Azure SDK types. A deterministic in-memory adapter supplies local demos and tests. It matches filtering, versioning, evidence and failure contracts, but does not claim parity with Azure analyzers, HNSW, RRF or semantic scores.

## Trade-offs

- **Capability/time to market:** Managed hybrid and semantic retrieval avoid building a search platform.
- **Cost:** Search replicas, partitions and semantic ranker can be material fixed/variable costs. Capacity is measured and right-sized; non-production environments use lower tiers or local adapters.
- **Vendor coupling:** Index schemas, filters and relevance behavior are Azure-specific even behind a port. Offline evaluation and golden API contracts make a future migration measurable rather than nominal.
- **Consistency:** Search indexing is eventually consistent and does not provide a cross-chunk transaction. New document versions remain inactive until complete; security-sensitive revocations deactivate old chunks before publishing replacements, preferring temporary unavailability over leakage.
- **Scale:** Replicas address query throughput/availability and partitions address corpus/storage throughput. Neither solves Azure OpenAI token quotas or poor retrieval design.

## Consequences

- Production requires at least two replicas where the selected tier supports the availability target, capacity/load tests, quota alerts and recovery procedures.
- Index schema changes create a new versioned index. Backfill and evaluation run before an alias/canary cutover; rollback returns the alias/configuration to the prior compatible index.
- ACL synchronization and stale-index reconciliation are first-class jobs with alerts.
- Index backup is reconstruction from immutable Blob sources plus versioned processing artifacts; Search is not the system of record.
- Region selection must co-locate compatible Search and model services and satisfy data-residency policy.

## When to revisit

Revisit if search spend dominates the service budget, required regions/features are unavailable, transactional document visibility becomes mandatory, or measured relevance/scale needs exceed the service. Any replacement must demonstrate pre-retrieval ACL enforcement and equal or better evaluation results.
