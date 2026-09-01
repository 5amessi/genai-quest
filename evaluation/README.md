# Evaluation assets

- `dataset.jsonl`: 17 labeled cases covering answerable, unanswerable, ambiguous, current/superseded versions, equal-authority conflicts, unauthorized documents, direct/indirect injection, role differences and poor retrieval matches.
- `baseline-results.json`: reviewed local baseline and regression thresholds. It contains aggregates, not fabricated production measurements.
- `results.json`: latest full local run with per-case evidence and metrics.
- `scripts/evaluate.py`: deterministic offline evaluator; no LLM judge or cloud credential is required.

Run:

```bash
python scripts/evaluate.py \
  --dataset evaluation/dataset.jsonl \
  --baseline evaluation/baseline-results.json \
  --output evaluation/results.json \
  --fail-on-regression
```

Run one case with `--case CASE_ID`. The exit code is `2` when an absolute or baseline gate fails. Metrics and their limits are explained in [`docs/evaluation-report.md`](../docs/evaluation-report.md).

