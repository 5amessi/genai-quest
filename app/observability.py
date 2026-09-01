from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from time import perf_counter

logger = logging.getLogger("knowledge_platform")


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


@dataclass(slots=True)
class MetricsRecorder:
    """Small local metric sink; production exports the same fields through OpenTelemetry."""

    counters: Counter[str] = field(default_factory=Counter)
    observations: dict[str, list[float]] = field(default_factory=dict)

    def increment(self, name: str, value: int = 1) -> None:
        self.counters[name] += value

    def observe(self, name: str, value: float) -> None:
        self.observations.setdefault(name, []).append(value)

    def request_event(self, **fields: object) -> None:
        # Callers pass fingerprints and metadata only, never prompts or retrieved text.
        logger.info(json.dumps(fields, sort_keys=True, default=str))


class StageTimer:
    def __init__(self, metrics: MetricsRecorder, metric_name: str) -> None:
        self.metrics = metrics
        self.metric_name = metric_name
        self.started = 0.0
        self._span_manager = None

    def __enter__(self) -> StageTimer:
        self.started = perf_counter()
        try:
            from opentelemetry import trace

            self._span_manager = trace.get_tracer(__name__).start_as_current_span(
                self.metric_name.removeprefix("latency.").removesuffix("_ms")
            )
            self._span_manager.__enter__()
        except ImportError:
            self._span_manager = None
        return self

    def __exit__(self, *_: object) -> None:
        self.metrics.observe(self.metric_name, (perf_counter() - self.started) * 1000)
        if self._span_manager is not None:
            self._span_manager.__exit__(*_)
