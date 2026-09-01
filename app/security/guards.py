from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from app.domain.models import Principal, SearchHit
from app.text import coverage, terms

INJECTION_RULES: dict[str, re.Pattern[str]] = {
    "instruction_override": re.compile(
        r"\b(ignore|disregard|forget)\b.{0,40}\b(previous|prior|system|developer)\b.{0,20}\b(instruction|prompt)s?\b",
        re.IGNORECASE | re.DOTALL,
    ),
    "secret_exfiltration": re.compile(
        r"\b(reveal|print|return|send|exfiltrat\w*)\b.{0,70}"
        r"\b(secret|credential|api[- ]?key|password|token)s?\b",
        re.IGNORECASE | re.DOTALL,
    ),
    "jailbreak": re.compile(
        r"\b(jailbreak|developer mode|bypass (?:the )?(?:policy|guardrail)|act as unrestricted)\b",
        re.IGNORECASE,
    ),
    "tool_coercion": re.compile(
        r"\b(run|execute|call)\b.{0,40}\b(shell|terminal|tool|function|http request)\b",
        re.IGNORECASE | re.DOTALL,
    ),
}

SENSITIVE_OUTPUT = re.compile(
    r"(?i)(?:-----BEGIN (?:RSA |EC )?PRIVATE KEY-----|password\s*[:=]|"
    r"api[-_ ]?key\s*[:=]|bearer\s+[a-z0-9._-]{16,})"
)


def scan_untrusted_text(text: str) -> tuple[str, ...]:
    return tuple(name for name, rule in INJECTION_RULES.items() if rule.search(text))


def question_policy_violation(question: str) -> str | None:
    flags = scan_untrusted_text(question)
    if flags:
        return "The request asks the system to bypass controls or expose protected data."
    if re.search(
        r"(?i)\b(show|list|reveal)\b.{0,40}\b(all|any)\b.{0,30}\b(confidential|restricted)\b",
        question,
    ):
        return "The request is an attempt to enumerate protected information."
    if re.search(
        r"(?i)\b(summarize|quote|repeat|return)\b.{0,80}"
        r"\b(api[- ]?credentials?|api[- ]?keys?|passwords?|secrets?)\b",
        question,
    ):
        return "The request asks the system to reproduce potentially sensitive credentials."
    return None


def question_needs_clarification(question: str) -> bool:
    """Identify requests too underspecified to bind to a policy or knowledge domain."""

    content_terms = terms(question)
    return len(content_terms) <= 2 and any(
        marker in content_terms for marker in {"approval", "sla", "policy", "access"}
    )


def question_is_known_out_of_scope(question: str) -> bool:
    return bool(
        re.search(r"(?i)\b(private|personal)\b.{0,30}\b(birthday|party|event)\b", question)
        or re.search(r"(?i)\b(python|npm|software)\b.{0,30}\b(package|version|deploy)\b", question)
        or re.search(
            r"(?i)\b(exactly\s+how\s+many|how\s+many)\b.{0,80}\b(will|next quarter|forecast)\b",
            question,
        )
    )


def is_authorized(principal: Principal, allowed_groups: frozenset[str]) -> bool:
    return bool(principal.groups.intersection(allowed_groups))


def build_odata_security_filter(principal: Principal) -> str:
    """Build an Azure Search prefilter only from validated, token-derived groups."""

    if not principal.groups:
        # An always-false filter avoids accidentally interpreting an empty group list as public.
        return "false"
    group_list = ",".join(sorted(principal.groups))
    return f"allowed_groups/any(g: search.in(g, '{group_list}', ',')) and is_current eq true"


@dataclass(slots=True)
class EvidenceDecision:
    safe_hits: list[SearchHit]
    evidence_coverage: float
    warnings: list[str]
    conflict: bool


class EvidenceGate:
    """Remove unsafe context and quantify whether remaining evidence can support an answer."""

    def assess(self, question: str, hits: list[SearchHit]) -> EvidenceDecision:
        warnings: list[str] = []
        unsafe = [hit for hit in hits if hit.chunk.risk_flags]
        if unsafe:
            warnings.append(
                f"Excluded {len(unsafe)} retrieved chunk(s) because untrusted content "
                "matched security rules."
            )
        untrusted_filtered = [hit for hit in hits if not hit.chunk.risk_flags]
        individual_coverage = {
            hit.chunk.chunk_id: coverage(
                question, f"{hit.chunk.title} {hit.chunk.heading or ''} {hit.chunk.content}"
            )
            for hit in untrusted_filtered
        }
        evidence_coverage = max(individual_coverage.values(), default=0.0)
        best_score = max((hit.score for hit in untrusted_filtered), default=0.0)
        # Do not manufacture apparent support by combining unrelated one-term matches from
        # different documents. Context must be relevant relative to the best individual hit.
        coverage_floor = max(0.15, evidence_coverage * 0.5)
        score_floor = max(0.10, best_score * 0.4)
        safe = [
            hit
            for hit in untrusted_filtered
            if individual_coverage[hit.chunk.chunk_id] >= coverage_floor
            and hit.score >= score_floor
        ]

        conflict_groups: dict[str, dict[str, list[SearchHit]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for hit in safe:
            key = hit.chunk.metadata.get("conflict_key")
            assertion = hit.chunk.metadata.get("assertion")
            if isinstance(key, str) and isinstance(assertion, str):
                conflict_groups[key][assertion].append(hit)

        conflict = False
        for key, assertions in conflict_groups.items():
            if len(assertions) <= 1:
                continue
            # Conflicts are unresolved only when the strongest sources have equal authority.
            ranks = {
                assertion: max(
                    int(hit.chunk.metadata.get("authority_rank") or 0) for hit in assertion_hits
                )
                for assertion, assertion_hits in assertions.items()
            }
            highest = max(ranks.values())
            if sum(rank == highest for rank in ranks.values()) > 1:
                conflict = True
                warnings.append(f"Conflicting equally authoritative evidence detected for '{key}'.")
            else:
                warnings.append(f"Lower-authority conflicting evidence ignored for '{key}'.")
                safe = [
                    hit
                    for hit in safe
                    if hit.chunk.metadata.get("conflict_key") != key
                    or int(hit.chunk.metadata.get("authority_rank") or 0) == highest
                ]
        return EvidenceDecision(safe, evidence_coverage, warnings, conflict)


def contains_sensitive_output(text: str) -> bool:
    return bool(SENSITIVE_OUTPUT.search(text))
