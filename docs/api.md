# API contract

Base URL in local mode: `http://127.0.0.1:8000`. Every `/v1/*` request uses `Authorization: Bearer <token>`. Health endpoints are unauthenticated.

## `POST /v1/query`

```json
{"question": "What is our vendor approval workflow?", "debug": false}
```

`debug=true` requires `knowledge.debug`. The response includes a request ID, typed status, answer, citations, warnings, route, usage/cost estimate and latency. Debug adds only authorized candidates/scores/metadata and the server-built authorization filter.

Statuses:

- `answered`: sufficient authorized evidence and validated citations.
- `insufficient_evidence`: weak/missing/out-of-scope evidence; no model-backed guess.
- `refused`: request policy/security violation.
- `clarification_required`: ambiguous request or unresolved equal-authority conflict.
- `degraded`: Search/model unavailable or model output invalid after bounded retries.

## `POST /v1/ingest`

Requires `knowledge.ingest`.

```json
{
  "document_id": "travel-policy",
  "canonical_id": "travel-policy",
  "title": "Travel Policy",
  "content": "# Travel Policy\n\nEmployees submit expenses within 30 days.",
  "content_type": "markdown",
  "department": "Finance",
  "allowed_groups": ["company-all"],
  "classification": "internal",
  "version": 1,
  "effective_from": "2026-09-01",
  "source_uri": "https://knowledge.example/policies/travel?v=1",
  "metadata": {"owner": "Finance"}
}
```

Returns `ingested`, `duplicate`, or `stale`. Reusing the same `document_id`/`version` with different content returns HTTP 409.

## `GET /v1/debug/chunks/{document_id}`

Requires `knowledge.debug`. Returns only chunks the principal is authorized to see. Embedding vectors are excluded from serialization.

## Health

- `GET /health/live`: process is running.
- `GET /health/ready`: corpus/Search is ready; otherwise HTTP 503.

## Local failure demonstration

When and only when `APP_ENV` is `local`/`test` and `ALLOW_FAILURE_INJECTION=true`, `/v1/query` accepts `X-Demo-Failure: search_unavailable` or `llm_timeout`. The header is ignored otherwise. It is a walkthrough/testing feature, not a production control plane.

