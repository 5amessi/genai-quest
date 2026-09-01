# ADR 0003: Use a deterministic guarded RAG workflow, not an autonomous agent

- Status: Accepted
- Date: 2026-08-31
- Decision owners: AI platform team
- Scope: Query orchestration and model access

## Context

The primary use case is evidence retrieval and grounded question answering. The system handles confidential documents and adversarial content, and its decisions must be testable, traceable and cost-bounded. An agent that can choose arbitrary tools or repeat model calls expands the prompt-injection blast radius and makes latency, cost and failure behavior difficult to predict.

## Options considered

1. **Autonomous tool-using agent.** Flexible for open-ended tasks, but introduces uncontrolled loops, tool authorization complexity and nondeterministic side effects.
2. **Framework-defined agent with an allowlist and step limit.** Better bounded, but still unnecessary for read-only evidence synthesis and harder to evaluate than a fixed workflow.
3. **Deterministic state machine / guarded pipeline.** Explicit transitions, budgets and failure semantics; less flexible for future multi-step actions.
4. **Single prompt with documents.** Minimal code, but conflates retrieval, policy and validation and cannot enforce architectural authorization.

## Decision

Implement an explicit workflow:

```text
authenticate → derive principal → validate/classify request
→ authorize within retrieval → fuse/rerank → evidence gate
→ construct delimited context → one bounded generation call
→ schema/citation/policy validation → answer or refusal
```

The model receives only already-authorized chunks. Retrieved text is labeled as untrusted evidence and cannot modify system instructions. The model has no credentials, Search connection or tool registry. Output must conform to a versioned schema containing answer status, cited chunk IDs and reason codes. Citation IDs must be a subset of the supplied evidence; unsupported or malformed output is rejected or repaired once under a strict budget, then refused.

Routing is deterministic and policy-driven. Exact metadata/navigation queries and insufficient-evidence outcomes may use no generative model. A configured small or frontier deployment may be selected by sensitivity, query complexity, conflict count, evidence volume, latency class and quality policy, subject to evaluation gates. The initial implementation keeps the routing interface but does not pretend to operate every model option.

## Trade-offs

- **Security:** Removing tools and open-ended planning reduces indirect prompt-injection and exfiltration paths, but no prompt defense guarantees that a model will never follow malicious text.
- **Reliability:** Bounded calls simplify timeouts, retries and idempotency. A rigid path may refuse questions that a carefully designed multi-step process could answer.
- **Observability:** Every stage has a named span and reason code. This creates more orchestration code than a single framework call.
- **Cost/latency:** Call ceilings make budgets predictable. Query embedding, optional reranking and generation still consume separate quotas.
- **Maintainability:** Provider ports prevent orchestration from depending on one SDK; the team owns the state-machine logic and validation.

## Consequences

- Retries occur only for transient, pre-response provider failures and are bounded with jitter; prompts are deterministic so a retry is semantically safe, though model output can still vary.
- The public response distinguishes `answered`, `insufficient_evidence`, `conflict`, `policy_refusal` and `dependency_unavailable` rather than hiding all failures behind prose.
- Prompt templates, output schemas and policies are immutable versioned artifacts captured in traces and evaluation results.
- Adding a tool requires a separate ADR, per-tool authorization, typed parameters, least-privilege identity, side-effect/idempotency analysis, confirmation semantics and adversarial tests.

## When to revisit

Consider a bounded workflow graph or specialized agent only when a validated business case needs multi-hop retrieval or actions, and offline red-team/evaluation data shows it meets security, quality, latency and cost budgets. General flexibility alone is not sufficient justification.
