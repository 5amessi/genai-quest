from __future__ import annotations

import math
import re
from collections import Counter

TOKEN_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_'-]*")

STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "do",
    "for",
    "from",
    "how",
    "i",
    "in",
    "is",
    "it",
    "me",
    "new",
    "of",
    "on",
    "or",
    "our",
    "should",
    "the",
    "to",
    "we",
    "what",
    "when",
    "who",
    "with",
}

CANONICAL_TERMS = {
    "approve": "approval",
    "approved": "approval",
    "approves": "approval",
    "approving": "approval",
    "authorization": "access",
    "authorize": "access",
    "credentials": "secret",
    "credential": "secret",
    "employees": "employee",
    "effect": "effective",
    "policies": "policy",
    "procedure": "workflow",
    "procedures": "workflow",
    "process": "workflow",
    "processes": "workflow",
    "runbooks": "runbook",
    "secrets": "secret",
    "vendors": "vendor",
}


def terms(text: str, *, drop_stopwords: bool = True) -> list[str]:
    values: list[str] = []
    for raw in TOKEN_RE.findall(text.lower()):
        value = CANONICAL_TERMS.get(raw, raw)
        if drop_stopwords and value in STOPWORDS:
            continue
        values.append(value)
    return values


def token_count(text: str) -> int:
    # A conservative tokenizer-independent estimate used only for local budgets.
    return max(1, math.ceil(len(TOKEN_RE.findall(text)) * 1.3))


def cosine(left: tuple[float, ...] | list[float], right: tuple[float, ...] | list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return max(0.0, dot / (left_norm * right_norm))


def term_frequency(text: str) -> Counter[str]:
    return Counter(terms(text))


def coverage(question: str, evidence: str) -> float:
    query_terms = set(terms(question))
    if not query_terms:
        return 0.0
    return len(query_terms.intersection(terms(evidence))) / len(query_terms)
