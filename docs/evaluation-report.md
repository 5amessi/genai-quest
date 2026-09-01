# Evaluation report

## Scope and methodology

The evaluation is a frozen, deterministic local regression suite—not an Azure production benchmark. The dataset has 17 hand-labeled cases with explicit principals, expected behavior/status, expected facts/sources/versions, and forbidden answer/retrieval/citation terms. It exercises:

- grounded answer and citation behavior;
- unanswerable and future-prediction refusal;
- ambiguous questions that require clarification;
- current versus superseded document versions;
- equal-authority conflicting sources;
- HR, Legal and Engineering access-control pairs;
- direct jailbreak and indirect prompt-injection canary behavior; and
- semantically/lexically tempting but factually irrelevant matches.

The runner calls the same `KnowledgeService` used by the API. It uses deterministic labels rather than an LLM judge, so CI is repeatable and free of judge drift/cost. Debug output remains privileged: evaluation principals do not receive `knowledge.debug`; the evaluator scores returned citations as the safe retrieval proxy.

## Metrics

| Metric | Method | Limitation |
|---|---|---|
| Retrieval recall@5 | Expected document/version appears in safe returned evidence/citations | Does not measure rank-sensitive NDCG or hidden unauthorized candidates |
| Answer correctness | Fraction of required fact groups with at least one accepted phrase | Lexical proxy, not semantic equivalence |
| Citation correctness | F1 over expected versus returned source IDs/versions | Source-level, not claim-level entailment |
| Groundedness proxy | Required answer facts also occur in cited excerpts | Cannot prove entailment or detect every unsupported connective claim |
| Behavior pass | Status, citation and forbidden-output contract | Labels are limited to represented behaviors |
| Refusal/security pass | Required non-answer plus zero forbidden content/source leakage | Small red-team seed, not exhaustive adversarial assurance |
| Latency | Service-reported local wall time | No network, Azure throttling or model tail latency |
| Token/cost | Tokenizer-independent estimate and configured example rates | Not a provider invoice |

Absolute gates require at least 0.90 for retrieval/correctness/citation/grounding, 0.95 behavior, 1.00 refusal and security, p95 local latency below 500 ms, mean estimated tokens below 400 and mean estimated request cost below USD 0.001. Baseline gates allow a 0.05 absolute drop for quality proxies and no drop for pass/refusal/security rates.

## Verified result

Command:

```bash
python scripts/evaluate.py --dataset evaluation/dataset.jsonl \
  --baseline evaluation/baseline-results.json \
  --output evaluation/results.json --fail-on-regression
```

Result snapshot on 2026-09-01:

| Result | Value |
|---|---:|
| Cases passed | 17 / 17 |
| Retrieval recall@5 | 1.000 |
| Answer correctness | 1.000 |
| Citation correctness | 1.000 |
| Groundedness proxy | 1.000 |
| Behavior/refusal/security pass | 1.000 / 1.000 / 1.000 |
| Mean / p95 local latency | Under 1 ms / under 2 ms in the checked-in local run; see `evaluation/results.json` for exact values |
| Mean estimated tokens | 101 |
| Mean / total estimated cost | USD 0.00002391 / USD 0.0004065 |

The complete per-case snapshot is [`evaluation/results.json`](../evaluation/results.json). Very small latency is expected because local embeddings/generation are deterministic in-process functions; it must not be quoted as an Azure SLO.

## What failed and what was learned

The first complete run passed 13/17. Most importantly, the evidence gate calculated coverage over concatenated chunks: one unrelated document matched “annual,” another matched “employee,” and together they made an unauthorized HR question look answerable. Authorization itself held—the HR chunk never appeared—but the system could still synthesize a misleading answer from individually weak public matches. The fix requires coverage and score floors on each authorized chunk relative to the best hit before combining context. A targeted test now locks this behavior.

The next run passed 16/17. The deterministic procedure generator selected only sentences that repeated query terms, omitting necessary intermediate workflow steps such as Security, Legal and Finance. The fix treats a retrieved structure-labeled workflow/list as an ordered unit, while still citing only that authorized chunk. The expected labels were also corrected where their wording was narrower than the source sentence; labels now include semantically equivalent, source-verifiable alternatives.

These failures reinforce three production lessons:

1. corpus-level coverage can create false confidence from unrelated partial matches;
2. retrieval relevance does not guarantee answer completeness for multi-step procedures; and
3. evaluation labels are code and require review/versioning just like prompts and chunkers.

## Regression strategy

CI runs unit tests, lint, JSON validation and this frozen evaluation. Any prompt/model/chunker/embedding/index/retrieval change must:

1. record configuration and dataset hashes;
2. run the full suite and relevant security/document slices;
3. meet absolute and baseline-delta gates;
4. receive human review for changed answers and citations; and
5. canary in production with refusal, quality, latency, provider-failure and cost monitoring.

Model-backed evaluation should add repeated samples, confidence intervals, a separately versioned semantic judge, human calibration and adversarial mutation. The local deterministic suite remains the fast presubmit layer.

## Conclusions and next experiments

The implementation satisfies the represented behaviors and fails closed on the seeded threats. It does not establish production quality. Highest-value next work is a larger enterprise-owned dataset by department/document class, Azure hybrid-versus-keyword/vector ablations, semantic-ranker lift measurement, OCR/table-specific retrieval tests, multilingual cases, mutation-based injection tests, and load tests that include real Search/OpenAI quotas and tail latency.
