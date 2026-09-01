from __future__ import annotations

import re

from app.domain.models import AnswerDraft, ModelRoute, SearchHit
from app.text import coverage, terms


class DeterministicGroundedGenerator:
    """Reviewer-friendly generator test double whose output is entirely evidence extractive."""

    async def generate(
        self,
        question: str,
        evidence: list[SearchHit],
        route: ModelRoute,
        prompt_version: str,
    ) -> AnswerDraft:
        question_terms = set(terms(question))
        if question_terms.intersection({"approval", "workflow", "checks", "onboarding"}):
            workflow_hits = [
                hit
                for hit in evidence
                if hit.chunk.heading
                and (
                    any(
                        marker in hit.chunk.heading.lower()
                        for marker in ("workflow", "procedure", "steps")
                    )
                    or bool(re.search(r"(?m)^\s*\d+[.)]\s+", hit.chunk.content))
                )
            ]
            if workflow_hits:
                workflow_hit = max(workflow_hits, key=lambda hit: hit.score)
                lines = [
                    line.strip(" -\t")
                    for line in workflow_hit.chunk.content.splitlines()
                    if len(line.strip(" -\t")) >= 12 and not line.lstrip().startswith("#")
                ]
                cited_ids = [workflow_hit.chunk.chunk_id]
                if "effective" in question_terms:
                    for hit in evidence:
                        date_lines = [
                            line.strip()
                            for line in hit.chunk.content.splitlines()
                            if "effective" in line.lower()
                        ]
                        if date_lines:
                            lines = [*date_lines, *lines]
                            cited_ids.append(hit.chunk.chunk_id)
                            break
                return AnswerDraft(
                    answer="Based on the current authorized sources: " + " ".join(lines),
                    cited_chunk_ids=list(dict.fromkeys(cited_ids)),
                    confidence=min(0.95, workflow_hit.score),
                )

        candidates: list[tuple[float, int, str, str]] = []
        for hit in evidence:
            parts = re.split(r"\n+|(?<=[.!?])\s+(?=[A-Z0-9])", hit.chunk.content)
            for part in parts:
                sentence = part.strip(" -\t")
                if len(sentence) < 12 or sentence.startswith("#"):
                    continue
                overlap = len(question_terms.intersection(terms(sentence)))
                sentence_score = 0.65 * coverage(question, sentence) + 0.35 * hit.score
                candidates.append((sentence_score, overlap, sentence, hit.chunk.chunk_id))
        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)

        selected: list[tuple[str, str]] = []
        seen: set[str] = set()
        for _score, overlap, sentence, chunk_id in candidates:
            if overlap == 0 or sentence.lower() in seen:
                continue
            selected.append((sentence, chunk_id))
            seen.add(sentence.lower())
            if len(selected) == 8:
                break
        if not selected:
            selected = [(evidence[0].chunk.content[:500], evidence[0].chunk.chunk_id)]

        answer = "Based on the current authorized sources: " + " ".join(
            sentence for sentence, _ in selected
        )
        cited = list(dict.fromkeys(chunk_id for _, chunk_id in selected))
        confidence = min(
            0.95, max(0.0, sum(hit.score for hit in evidence[:3]) / min(3, len(evidence)))
        )
        return AnswerDraft(answer=answer, cited_chunk_ids=cited, confidence=confidence)


class ModelRouter:
    """Cost-aware routing policy; local mode still uses the deterministic generator."""

    def route(self, question: str, evidence: list[SearchHit]) -> ModelRoute:
        if any(hit.chunk.classification.value == "restricted" for hit in evidence):
            return ModelRoute.PRIVATE_MODEL
        document_count = len({hit.chunk.document_id for hit in evidence[:5]})
        if len(terms(question)) > 40 or document_count >= 3:
            return ModelRoute.FRONTIER_HOSTED
        return ModelRoute.SMALL_HOSTED
