#!/usr/bin/env python3
"""Deterministic offline evaluation runner for the demo knowledge service.

The scorer deliberately uses labelled facts and source IDs instead of an LLM
judge.  That keeps CI repeatable, cheap, and usable without cloud credentials.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import hashlib
import importlib
import inspect
import json
import math
import re
import statistics
import sys
import time
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

EVALUATOR_VERSION = "1.0.0"

DEFAULT_THRESHOLDS: dict[str, dict[str, float | str]] = {
    "case_pass_rate": {"operator": ">=", "value": 0.90},
    "retrieval_recall_at_k": {"operator": ">=", "value": 0.90},
    "answer_correctness": {"operator": ">=", "value": 0.90},
    "citation_correctness": {"operator": ">=", "value": 0.90},
    "groundedness_proxy": {"operator": ">=", "value": 0.90},
    "behavior_pass_rate": {"operator": ">=", "value": 0.95},
    "refusal_pass_rate": {"operator": ">=", "value": 1.00},
    "security_pass_rate": {"operator": ">=", "value": 1.00},
    "p95_latency_ms": {"operator": "<=", "value": 500.0},
    "mean_total_tokens": {"operator": "<=", "value": 400.0},
    "mean_estimated_cost_usd": {"operator": "<=", "value": 0.001},
}

DEFAULT_REGRESSION_TOLERANCES: dict[str, dict[str, float | str]] = {
    "case_pass_rate": {"direction": "higher", "absolute": 0.0},
    "retrieval_recall_at_k": {"direction": "higher", "absolute": 0.05},
    "answer_correctness": {"direction": "higher", "absolute": 0.05},
    "citation_correctness": {"direction": "higher", "absolute": 0.05},
    "groundedness_proxy": {"direction": "higher", "absolute": 0.05},
    "refusal_pass_rate": {"direction": "higher", "absolute": 0.0},
    "security_pass_rate": {"direction": "higher", "absolute": 0.0},
    "p95_latency_ms": {"direction": "lower", "relative": 0.20},
    "mean_estimated_cost_usd": {"direction": "lower", "relative": 0.20},
}

ANSWER_STATUSES = {"answered"}
NONANSWER_STATUSES = {"insufficient_evidence", "refused"}
CLARIFY_STATUSES = {"clarification_required"}


def _plain(value: Any) -> Any:
    """Convert Pydantic/dataclass/enum response objects to JSON-compatible data."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return _plain(value.value)
    if hasattr(value, "model_dump"):
        return _plain(value.model_dump(mode="json"))
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _plain(dataclasses.asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset, tuple, list)):
        return [_plain(item) for item in value]
    if hasattr(value, "__dict__"):
        return {key: _plain(item) for key, item in vars(value).items() if not key.startswith("_")}
    return str(value)


def _import_symbol(spec: str) -> Any:
    module_name, separator, symbol_name = spec.partition(":")
    if not separator:
        raise ValueError(f"Import must be MODULE:SYMBOL, received {spec!r}")
    return getattr(importlib.import_module(module_name), symbol_name)


def _load_dataset(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                case = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
            for required in ("id", "category", "question", "principal", "expected_behavior"):
                if required not in case:
                    raise ValueError(f"{path}:{line_number}: missing {required!r}")
            if case["id"] in seen:
                raise ValueError(f"{path}:{line_number}: duplicate case id {case['id']!r}")
            seen.add(case["id"])
            cases.append(case)
    if not cases:
        raise ValueError(f"{path}: no evaluation cases found")
    return cases


def _build_principal(principal_type: type[Any], payload: Mapping[str, Any]) -> Any:
    materialized = dict(payload)
    # The domain object uses immutable sets. Pydantic models can still validate them.
    for field in ("groups", "roles"):
        if field in materialized and isinstance(materialized[field], list):
            materialized[field] = frozenset(materialized[field])
    if hasattr(principal_type, "model_validate"):
        return principal_type.model_validate(materialized)
    return principal_type(**materialized)


def _normalize_text(value: Any) -> str:
    text = str(value or "").casefold()
    text = re.sub(r"[^\w.%-]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def _contains(haystack: str, needle: str) -> bool:
    return _normalize_text(needle) in _normalize_text(haystack)


def _status(value: Any) -> str:
    if isinstance(value, Enum):
        value = value.value
    normalized = _normalize_text(value).replace(" ", "_")
    return normalized or "missing"


def _get(mapping: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return default


def _as_items(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, Mapping):
        return [value]
    return [value]


def _source_id(item: Any) -> str | None:
    data = _plain(item)
    if not isinstance(data, Mapping):
        return None
    for key in ("document_id", "doc_id", "source_id", "id"):
        candidate = data.get(key)
        if candidate not in (None, ""):
            return str(candidate)
    metadata = data.get("metadata")
    if isinstance(metadata, Mapping):
        return _source_id(metadata)
    return None


def _source_version(item: Any) -> str | None:
    data = _plain(item)
    if not isinstance(data, Mapping):
        return None
    for key in ("version", "document_version", "source_version"):
        candidate = data.get(key)
        if candidate not in (None, ""):
            return str(candidate)
    metadata = data.get("metadata")
    if isinstance(metadata, Mapping):
        return _source_version(metadata)
    return None


def _citation_excerpt(item: Any) -> str:
    data = _plain(item)
    if not isinstance(data, Mapping):
        return ""
    return str(_get(data, "excerpt", "quote", "text", "content", default=""))


def _retrieval_items(debug: Any) -> list[Any]:
    """Prefer final/selected chunks, while supporting several debug schemas."""
    debug = _plain(debug)
    if isinstance(debug, list):
        return debug
    if not isinstance(debug, Mapping):
        return []
    for key in (
        "selected_chunks",
        "final_chunks",
        "results",
        "retrieved_chunks",
        "hits",
        "candidates",
        "chunks",
    ):
        value = debug.get(key)
        if isinstance(value, list):
            return value
    # Some implementations expose rank -> hit or document_id -> score.
    if debug and all(isinstance(value, Mapping) for value in debug.values()):
        return list(debug.values())
    return []


def _ordered_unique(values: Iterable[str | None]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value is None or value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output


def _fact_groups(case: Mapping[str, Any]) -> list[list[str]]:
    groups = case.get("expected_fact_groups", [])
    output: list[list[str]] = []
    for group in groups:
        if isinstance(group, str):
            output.append([group])
        else:
            output.append([str(item) for item in group])
    return output


def _fact_score(answer: str, groups: Sequence[Sequence[str]]) -> tuple[float | None, list[int]]:
    if not groups:
        return None, []
    missing = [
        index
        for index, alternatives in enumerate(groups)
        if not any(_contains(answer, alternative) for alternative in alternatives)
    ]
    return (len(groups) - len(missing)) / len(groups), missing


def _citation_score(
    citations: Sequence[Any], expected_ids: Sequence[str], expected_versions: Mapping[str, str]
) -> tuple[float | None, list[str]]:
    if not expected_ids:
        return None, []
    observed = [(_source_id(item), _source_version(item)) for item in citations]
    matched: list[str] = []
    for expected in expected_ids:
        wanted_version = str(expected_versions.get(expected, ""))
        if any(
            source_id == expected and (not wanted_version or version == wanted_version)
            for source_id, version in observed
        ):
            matched.append(expected)
    expected_set = set(expected_ids)
    relevant_count = sum(
        1
        for source_id, version in observed
        if source_id in expected_set
        and (
            not expected_versions.get(str(source_id))
            or version == str(expected_versions[str(source_id)])
        )
    )
    precision = relevant_count / len(observed) if observed else 0.0
    recall = len(set(matched)) / len(expected_set)
    score = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
    return score, [item for item in expected_ids if item not in matched]


def _retrieval_recall(
    items: Sequence[Any], expected_ids: Sequence[str], expected_versions: Mapping[str, str]
) -> float | None:
    if not expected_ids:
        return None
    matched = 0
    for expected in set(expected_ids):
        wanted_version = str(expected_versions.get(expected, ""))
        if any(
            _source_id(item) == expected
            and (not wanted_version or _source_version(item) == wanted_version)
            for item in items
        ):
            matched += 1
    return matched / len(set(expected_ids))


def _groundedness_proxy(
    answer: str,
    citations: Sequence[Any],
    groups: Sequence[Sequence[str]],
    expected_ids: Sequence[str],
) -> float | None:
    """Fraction of labelled facts present in the answer and backed by evidence.

    Excerpt overlap is used when citations expose excerpts. Otherwise a citation
    to a labelled source is treated as evidence. This is a regression proxy, not
    semantic entailment and is complemented by human review in the report.
    """
    if not groups:
        return None
    excerpts = " ".join(_citation_excerpt(item) for item in citations if _citation_excerpt(item))
    citation_ids = {_source_id(item) for item in citations}
    has_labelled_source = bool(set(expected_ids) & citation_ids)
    supported = 0
    for alternatives in groups:
        present = any(_contains(answer, alternative) for alternative in alternatives)
        excerpt_support = bool(excerpts) and any(
            _contains(excerpts, alternative) for alternative in alternatives
        )
        if present and (excerpt_support or (not excerpts and has_labelled_source)):
            supported += 1
    return supported / len(groups)


def _expected_statuses(case: Mapping[str, Any]) -> set[str]:
    explicit = case.get("expected_statuses")
    if explicit:
        return {_status(item) for item in explicit}
    behavior = case["expected_behavior"]
    if behavior == "answer":
        return ANSWER_STATUSES
    if behavior == "clarify":
        return CLARIFY_STATUSES
    if behavior == "refuse":
        return NONANSWER_STATUSES
    raise ValueError(f"Unknown expected_behavior {behavior!r} in {case['id']}")


def _usage(response: Mapping[str, Any], input_rate: float, output_rate: float) -> dict[str, float]:
    usage = _get(response, "usage", "token_usage", default={})
    usage = usage if isinstance(usage, Mapping) else {}
    input_tokens = float(_get(usage, "input_tokens", "prompt_tokens", default=0) or 0)
    output_tokens = float(_get(usage, "output_tokens", "completion_tokens", default=0) or 0)
    total_tokens = float(_get(usage, "total_tokens", default=input_tokens + output_tokens) or 0)
    supplied_cost = _get(usage, "estimated_cost_usd", "cost_usd")
    estimated_cost = (
        float(supplied_cost)
        if supplied_cost is not None
        else (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000
    )
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "estimated_cost_usd": estimated_cost,
    }


async def _ask(service: Any, question: str, principal: Any, debug: bool) -> Any:
    ask = service.ask
    try:
        parameters = inspect.signature(ask).parameters
    except (TypeError, ValueError):
        parameters = {}
    roles = set(getattr(principal, "roles", ()) or ())
    # Debug output is privileged in the application. Do not grant that role in the
    # evaluator; fall back to returned citations when the test principal lacks it.
    kwargs = {"debug": debug and "knowledge.debug" in roles} if "debug" in parameters else {}
    result = ask(question, principal, **kwargs)
    return await result if inspect.isawaitable(result) else result


def _score_case(
    case: Mapping[str, Any],
    raw_response: Any,
    wall_latency_ms: float,
    top_k: int,
    input_rate: float,
    output_rate: float,
) -> dict[str, Any]:
    response = _plain(raw_response)
    if not isinstance(response, Mapping):
        response = {"answer": str(response)}
    answer = str(_get(response, "answer", "text", default="") or "")
    observed_status = _status(_get(response, "status", default="missing"))
    expected_statuses = _expected_statuses(case)
    status_pass = observed_status in expected_statuses
    citations = _as_items(_get(response, "citations", "sources", default=[]))
    retrieval = _retrieval_items(_get(response, "retrieval_debug", "debug", default=[]))
    retrieval_source = "retrieval_debug"
    if not retrieval:
        retrieval = list(citations)
        retrieval_source = "citations_fallback"

    expected_ids = [str(item) for item in case.get("expected_source_ids", [])]
    expected_versions = {
        str(key): str(value) for key, value in case.get("expected_source_versions", {}).items()
    }
    top_retrieval = retrieval[:top_k]
    retrieved_ids = _ordered_unique(_source_id(item) for item in top_retrieval)
    citation_ids = _ordered_unique(_source_id(item) for item in citations)
    retrieval_recall = _retrieval_recall(top_retrieval, expected_ids, expected_versions)

    groups = _fact_groups(case)
    answer_correctness, missing_fact_groups = _fact_score(answer, groups)
    citation_correctness, missing_citations = _citation_score(
        citations, expected_ids, expected_versions
    )
    groundedness = _groundedness_proxy(answer, citations, groups, expected_ids)

    forbidden_terms = [str(item) for item in case.get("forbidden_answer_terms", [])]
    forbidden_terms_seen = [item for item in forbidden_terms if _contains(answer, item)]
    forbidden_retrieval_ids = {str(item) for item in case.get("forbidden_retrieval_source_ids", [])}
    forbidden_citation_ids = {str(item) for item in case.get("forbidden_citation_source_ids", [])}
    retrieval_leaks = sorted(forbidden_retrieval_ids & set(retrieved_ids))
    citation_leaks = sorted(forbidden_citation_ids & set(citation_ids))

    behavior = str(case["expected_behavior"])
    score_content = behavior == "answer" or bool(case.get("score_answer_content"))
    no_nonanswer_citations = (
        behavior == "answer" or bool(case.get("allow_citations_on_nonanswer")) or not citations
    )
    refusal_pass = (
        status_pass and not forbidden_terms_seen and no_nonanswer_citations
        if behavior == "refuse"
        else None
    )
    clarification_pass = status_pass and no_nonanswer_citations if behavior == "clarify" else None
    behavior_pass = status_pass and not forbidden_terms_seen and no_nonanswer_citations

    security_scored = bool(
        case.get("security_case") or forbidden_retrieval_ids or forbidden_citation_ids
    )
    security_pass = (
        not forbidden_terms_seen and not retrieval_leaks and not citation_leaks
        if security_scored
        else None
    )

    usage = _usage(response, input_rate, output_rate)
    latency_ms = float(_get(response, "latency_ms", default=wall_latency_ms) or wall_latency_ms)

    metric_requirements: list[bool] = [behavior_pass]
    if score_content:
        metric_requirements.extend(
            [
                answer_correctness == 1.0,
                citation_correctness == 1.0,
                groundedness == 1.0,
            ]
        )
        if expected_ids:
            metric_requirements.append(retrieval_recall == 1.0)
    if refusal_pass is not None:
        metric_requirements.append(refusal_pass)
    if clarification_pass is not None:
        metric_requirements.append(clarification_pass)
    if security_pass is not None:
        metric_requirements.append(security_pass)

    return {
        "id": case["id"],
        "category": case["category"],
        "question": case["question"],
        "expected_behavior": behavior,
        "expected_statuses": sorted(expected_statuses),
        "observed_status": observed_status,
        "passed": all(metric_requirements),
        "metrics": {
            "retrieval_recall_at_k": retrieval_recall,
            "answer_correctness": answer_correctness if score_content else None,
            "citation_correctness": citation_correctness if score_content else None,
            "groundedness_proxy": groundedness if score_content else None,
            "behavior_pass": behavior_pass,
            "refusal_pass": refusal_pass,
            "clarification_pass": clarification_pass,
            "security_pass": security_pass,
            "latency_ms": latency_ms,
            **usage,
        },
        "evidence": {
            "retrieval_source": retrieval_source,
            "expected_source_ids": expected_ids,
            "retrieved_source_ids_at_k": retrieved_ids,
            "retrieved_sources_at_k": [
                {"document_id": _source_id(item), "version": _source_version(item)}
                for item in top_retrieval
            ],
            "citation_source_ids": citation_ids,
            "missing_fact_group_indexes": missing_fact_groups,
            "missing_citation_source_ids": missing_citations,
            "forbidden_answer_terms_seen": forbidden_terms_seen,
            "forbidden_retrieval_source_ids_seen": retrieval_leaks,
            "forbidden_citation_source_ids_seen": citation_leaks,
        },
    }


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def _mean_metric(results: Sequence[Mapping[str, Any]], name: str) -> float | None:
    values = [
        float(result["metrics"][name])
        for result in results
        if result.get("metrics", {}).get(name) is not None
    ]
    return statistics.fmean(values) if values else None


def _aggregate(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    latencies = [
        float(result["metrics"]["latency_ms"])
        for result in results
        if result.get("metrics", {}).get("latency_ms") is not None
    ]
    total = len(results)
    passed = sum(bool(result.get("passed")) for result in results)
    aggregates: dict[str, Any] = {
        "total_cases": total,
        "passed_cases": passed,
        "case_pass_rate": passed / total if total else 0.0,
        "retrieval_recall_at_k": _mean_metric(results, "retrieval_recall_at_k"),
        "answer_correctness": _mean_metric(results, "answer_correctness"),
        "citation_correctness": _mean_metric(results, "citation_correctness"),
        "groundedness_proxy": _mean_metric(results, "groundedness_proxy"),
        "behavior_pass_rate": _mean_metric(results, "behavior_pass"),
        "refusal_pass_rate": _mean_metric(results, "refusal_pass"),
        "clarification_pass_rate": _mean_metric(results, "clarification_pass"),
        "security_pass_rate": _mean_metric(results, "security_pass"),
        "mean_latency_ms": statistics.fmean(latencies) if latencies else None,
        "p50_latency_ms": _percentile(latencies, 0.50),
        "p95_latency_ms": _percentile(latencies, 0.95),
        "mean_input_tokens": _mean_metric(results, "input_tokens"),
        "mean_output_tokens": _mean_metric(results, "output_tokens"),
        "mean_total_tokens": _mean_metric(results, "total_tokens"),
        "total_tokens": sum(float(result["metrics"].get("total_tokens", 0)) for result in results),
        "mean_estimated_cost_usd": _mean_metric(results, "estimated_cost_usd"),
        "total_estimated_cost_usd": sum(
            float(result["metrics"].get("estimated_cost_usd", 0)) for result in results
        ),
    }
    return aggregates


def _compare(operator: str, actual: float, limit: float) -> bool:
    if operator == ">=":
        return actual >= limit
    if operator == "<=":
        return actual <= limit
    raise ValueError(f"Unsupported threshold operator {operator!r}")


def _quality_gates(
    aggregates: Mapping[str, Any],
    thresholds: Mapping[str, Mapping[str, Any]],
    baseline: Mapping[str, Any] | None,
    tolerances: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    gates: list[dict[str, Any]] = []
    for metric, rule in thresholds.items():
        actual = aggregates.get(metric)
        operator = str(rule["operator"])
        limit = float(rule["value"])
        passed = actual is not None and _compare(operator, float(actual), limit)
        gates.append(
            {
                "kind": "absolute_threshold",
                "metric": metric,
                "operator": operator,
                "limit": limit,
                "actual": actual,
                "passed": passed,
            }
        )

    baseline_aggregates = baseline.get("aggregates", {}) if baseline else {}
    for metric, tolerance in tolerances.items():
        actual = aggregates.get(metric)
        prior = baseline_aggregates.get(metric)
        if actual is None or prior is None:
            continue
        direction = str(tolerance.get("direction", "higher"))
        if "absolute" in tolerance:
            allowance = float(tolerance["absolute"])
        else:
            allowance = abs(float(prior)) * float(tolerance.get("relative", 0.0))
        floor_or_ceiling = (
            float(prior) - allowance if direction == "higher" else float(prior) + allowance
        )
        operator = ">=" if direction == "higher" else "<="
        gates.append(
            {
                "kind": "baseline_regression",
                "metric": metric,
                "operator": operator,
                "limit": floor_or_ceiling,
                "actual": actual,
                "baseline": prior,
                "allowance": allowance,
                "passed": _compare(operator, float(actual), floor_or_ceiling),
            }
        )
    return gates


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    dataset_path = Path(args.dataset)
    all_cases = _load_dataset(dataset_path)
    selected_ids = set(args.case or [])
    cases = [case for case in all_cases if not selected_ids or case["id"] in selected_ids]
    if selected_ids - {case["id"] for case in all_cases}:
        unknown = ", ".join(sorted(selected_ids - {case["id"] for case in all_cases}))
        raise ValueError(f"Unknown case id(s): {unknown}")

    bootstrap = _import_symbol(args.bootstrap)
    principal_type = _import_symbol(args.principal_type)
    service = bootstrap()
    if inspect.isawaitable(service):
        service = await service

    results: list[dict[str, Any]] = []
    for case in cases:
        started = time.perf_counter()
        try:
            principal = _build_principal(principal_type, case["principal"])
            response = await _ask(service, case["question"], principal, not args.no_debug)
            wall_ms = (time.perf_counter() - started) * 1000
            result = _score_case(
                case,
                response,
                wall_ms,
                args.top_k,
                args.input_price_per_million,
                args.output_price_per_million,
            )
        except Exception as exc:  # keep the report useful when one case fails
            wall_ms = (time.perf_counter() - started) * 1000
            result = {
                "id": case["id"],
                "category": case["category"],
                "question": case["question"],
                "expected_behavior": case["expected_behavior"],
                "expected_statuses": sorted(_expected_statuses(case)),
                "observed_status": "runner_error",
                "passed": False,
                "metrics": {
                    "retrieval_recall_at_k": None,
                    "answer_correctness": None,
                    "citation_correctness": None,
                    "groundedness_proxy": None,
                    "behavior_pass": False,
                    "refusal_pass": False if case["expected_behavior"] == "refuse" else None,
                    "clarification_pass": False if case["expected_behavior"] == "clarify" else None,
                    "security_pass": False if case.get("security_case") else None,
                    "latency_ms": wall_ms,
                    "input_tokens": 0.0,
                    "output_tokens": 0.0,
                    "total_tokens": 0.0,
                    "estimated_cost_usd": 0.0,
                },
                "evidence": {},
                "error": {"type": type(exc).__name__, "message": str(exc)},
            }
        results.append(result)
        state = "PASS" if result["passed"] else "FAIL"
        print(f"[{state}] {case['id']}: {result['observed_status']}", file=sys.stderr)

    dataset_bytes = dataset_path.read_bytes()
    baseline = None
    if args.baseline:
        baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    thresholds = (baseline or {}).get("thresholds", DEFAULT_THRESHOLDS)
    tolerances = (baseline or {}).get("regression_tolerances", DEFAULT_REGRESSION_TOLERANCES)
    aggregates = _aggregate(results)
    gates = _quality_gates(aggregates, thresholds, baseline, tolerances)
    return {
        "schema_version": "1.0",
        "snapshot_kind": "evaluation_run",
        "generated_at": datetime.now(UTC).isoformat(),
        "evaluator_version": EVALUATOR_VERSION,
        "dataset": {
            "path": str(dataset_path.as_posix()),
            "sha256": hashlib.sha256(dataset_bytes).hexdigest(),
            "selected_cases": len(cases),
            "available_cases": len(all_cases),
            "top_k": args.top_k,
        },
        "cost_model": {
            "input_usd_per_million_tokens": args.input_price_per_million,
            "output_usd_per_million_tokens": args.output_price_per_million,
            "note": "Used only when the service does not return estimated_cost_usd.",
        },
        "thresholds": thresholds,
        "regression_tolerances": tolerances,
        "aggregates": aggregates,
        "gates": gates,
        "all_gates_passed": all(gate["passed"] for gate in gates),
        "cases": results,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default="evaluation/dataset.jsonl")
    parser.add_argument("--output", default="evaluation/results.json")
    parser.add_argument("--baseline", help="Checked-in snapshot for thresholds and delta gates")
    parser.add_argument("--bootstrap", default="app.bootstrap:build_demo_service")
    parser.add_argument("--principal-type", default="app.domain.models:Principal")
    parser.add_argument("--case", action="append", help="Run one case ID; may be repeated")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--input-price-per-million", type=float, default=0.15)
    parser.add_argument("--output-price-per-million", type=float, default=0.60)
    parser.add_argument("--no-debug", action="store_true", help="Do not request retrieval debug")
    parser.add_argument(
        "--fail-on-regression",
        action="store_true",
        help="Exit 2 when any absolute or baseline gate fails",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.top_k < 1:
        raise SystemExit("--top-k must be at least 1")
    try:
        report = asyncio.run(_run(args))
    except (ImportError, AttributeError, OSError, ValueError) as exc:
        print(f"evaluation setup failed: {exc}", file=sys.stderr)
        return 1
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report["aggregates"], indent=2, sort_keys=True))
    print(f"Wrote {output}", file=sys.stderr)
    if args.fail_on_regression and not report["all_gates_passed"]:
        failed = [gate["metric"] for gate in report["gates"] if not gate["passed"]]
        print(f"quality gates failed: {', '.join(failed)}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
