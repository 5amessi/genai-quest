from __future__ import annotations

import re
from dataclasses import dataclass

from app.text import token_count

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    heading: str | None
    content: str
    token_count: int


class StructureAwareChunker:
    """Respect document structure, then enforce a model-context safety ceiling."""

    def __init__(self, max_tokens: int = 220, overlap_tokens: int = 35) -> None:
        if max_tokens < 40:
            raise ValueError("max_tokens must be at least 40")
        if overlap_tokens < 0 or overlap_tokens >= max_tokens // 2:
            raise ValueError("overlap_tokens must be non-negative and less than half max_tokens")
        self.max_tokens = max_tokens
        self.overlap_tokens = overlap_tokens

    def split(self, text: str) -> list[ChunkDraft]:
        sections = self._sections(text)
        chunks: list[ChunkDraft] = []
        for heading, body in sections:
            units = self._semantic_units(body)
            if not units and heading:
                units = [heading]
            chunks.extend(self._pack(heading, units))
        return chunks

    def _sections(self, text: str) -> list[tuple[str | None, str]]:
        sections: list[tuple[str | None, str]] = []
        heading: str | None = None
        body: list[str] = []
        for line in text.splitlines():
            match = HEADING_RE.match(line)
            if match:
                if body or heading:
                    sections.append((heading, "\n".join(body).strip()))
                heading = match.group(2).strip()
                body = []
            else:
                body.append(line)
        if body or heading:
            sections.append((heading, "\n".join(body).strip()))
        return sections or [(None, text)]

    def _semantic_units(self, body: str) -> list[str]:
        if not body:
            return []
        blocks = [block.strip() for block in re.split(r"\n\s*\n", body) if block.strip()]
        units: list[str] = []
        for block in blocks:
            if token_count(block) <= self.max_tokens:
                units.append(block)
                continue
            sentences = [part.strip() for part in SENTENCE_RE.split(block) if part.strip()]
            if len(sentences) == 1:
                units.extend(self._word_windows(block))
            else:
                current: list[str] = []
                for sentence in sentences:
                    candidate = " ".join([*current, sentence])
                    if current and token_count(candidate) > self.max_tokens:
                        units.append(" ".join(current))
                        current = [sentence]
                    else:
                        current.append(sentence)
                if current:
                    joined = " ".join(current)
                    units.extend(
                        self._word_windows(joined)
                        if token_count(joined) > self.max_tokens
                        else [joined]
                    )
        return units

    def _word_windows(self, text: str) -> list[str]:
        words = text.split()
        # token_count uses a 1.3 multiplier, so this word ceiling remains conservative.
        window = max(1, int(self.max_tokens / 1.3))
        overlap = min(int(self.overlap_tokens / 1.3), window - 1)
        step = max(1, window - overlap)
        return [" ".join(words[start : start + window]) for start in range(0, len(words), step)]

    def _pack(self, heading: str | None, units: list[str]) -> list[ChunkDraft]:
        packed: list[ChunkDraft] = []
        current: list[str] = []
        heading_prefix = f"## {heading}\n" if heading else ""
        for unit in units:
            candidate_body = "\n\n".join([*current, unit])
            candidate = heading_prefix + candidate_body
            if current and token_count(candidate) > self.max_tokens:
                content = heading_prefix + "\n\n".join(current)
                packed.append(ChunkDraft(heading, content, token_count(content)))
                previous = current[-1]
                current = (
                    [previous, unit] if token_count(previous) <= self.overlap_tokens else [unit]
                )
            else:
                current.append(unit)
        if current:
            content = heading_prefix + "\n\n".join(current)
            if token_count(content) > self.max_tokens:
                for window in self._word_windows("\n\n".join(current)):
                    window_content = heading_prefix + window
                    packed.append(ChunkDraft(heading, window_content, token_count(window_content)))
            else:
                packed.append(ChunkDraft(heading, content, token_count(content)))
        return packed
