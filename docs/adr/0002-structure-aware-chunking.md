# ADR 0002: Use structure-aware, token-bounded chunks with stable provenance

- Status: Accepted
- Date: 2026-08-31
- Decision owners: AI platform team
- Scope: Ingestion and indexing

## Context

The corpus contains policies, contracts, SOPs, technical documentation and tables. Arbitrary fixed windows split clauses from headings, detach table headers from rows, merge unrelated sections and make citations difficult to audit. Very large chunks dilute retrieval precision and waste context; very small chunks lose qualifying language and increase index and reranking cost. Re-ingestion and policy versions also require stable lineage.

## Options considered

1. **Fixed character/token windows.** Cheap and deterministic, but blind to document structure and especially poor for contracts and tables.
2. **Sentence-only chunks.** Precise but often omit definitions, headings and qualifications needed to interpret a sentence.
3. **Structure-aware parsing followed by token bounds.** Preserves semantic units and provenance while bounding cost, but requires format-specific parsers and more tests.
4. **LLM-generated semantic chunks.** Flexible, but nondeterministic, expensive, vulnerable to document instructions and difficult to reproduce.
5. **Late chunking over long-context embeddings.** Promising for context preservation, but increases serving complexity and is not necessary for the initial corpus.

## Decision

Parse each format into a canonical block model (`heading`, `paragraph`, `list`, `table`, `code`, page/section coordinates). Clean presentation artifacts without rewriting meaning. Build chunks along section and block boundaries, then apply configurable token bounds:

- target roughly 400 tokens;
- soft minimum roughly 150 tokens, merging adjacent blocks in the same section;
- hard maximum roughly 700 tokens, splitting at sentence/list boundaries; and
- at most roughly 60 tokens of overlap, used only where a boundary would otherwise lose local continuity.

These are starting values, not universal constants; evaluation determines per-document-class profiles. A contract clause, a complete SOP step group, or a table plus its title/header is preferred over reaching the target size. Oversized tables are split by row groups with headers repeated and a shared `table_id`. Page boundaries are recorded but do not force a semantic split.

Each chunk carries:

```text
tenant_id, document_id, document_version, content_hash, chunk_id,
source_uri, title, section_path, page_start, page_end, block_type,
effective_from, effective_to, authority, is_active, visibility,
allowed_principal_ids, allowed_group_ids, parser_version,
chunker_version, embedding_model_version
```

`chunk_id` is a deterministic hash of tenant, document, immutable version, section path, block ordinal and normalized content hash. The raw source is immutable. Normalized text and parsing diagnostics are retained separately for reproducibility.

Documents and embedded text are treated as untrusted data throughout parsing and generation. The chunker does not use an LLM and never executes macros, links, embedded code or document instructions.

## Trade-offs

- **Retrieval quality:** Heading inheritance and intact clauses/tables improve precision and citation usability. Bad source structure or parser errors can still produce poor chunks.
- **Cost:** More parsing and metadata increase ingestion cost and index size, but reduce irrelevant context and generation tokens.
- **Determinism:** Stable IDs make retries and evaluation reproducible. Parser or chunker upgrades intentionally change IDs and therefore require a versioned re-index.
- **Format coverage:** PDF/DOCX require specialized parsing and OCR/table fallbacks. The local demo uses normalized sample documents and cannot demonstrate full layout fidelity.
- **Overlap:** Small conditional overlap helps continuity but can create duplicate evidence; online retrieval collapses adjacent/overlapping results.

## Consequences

- Parser fixtures cover headings, lists, repeated headers, page breaks, tables, empty scans and malformed input.
- Ingestion writes a new version as inactive, verifies chunk count and embeddings, then activates it. Failed or partial versions never become queryable.
- Security-sensitive metadata is copied to every chunk so Search can enforce ACLs without a join.
- Chunk profiles, parser version and embedding version are part of evaluation experiment metadata.
- A document-quality signal is emitted when OCR confidence, table reconstruction or parse coverage falls below threshold; such documents can require human review.

## When to revisit

Change the profile when per-document-class evaluation shows systematic boundary failures, token cost rises without quality gain, or multimodal/table questions become a primary use case. Consider late chunking only after benchmarking it against this baseline on the enterprise corpus.
