from __future__ import annotations

import re

from app.domain.models import DocumentInput


class DocumentParser:
    """Normalize already-extracted text while preserving headings, lists, and tables."""

    def parse(self, document: DocumentInput) -> str:
        text = document.content.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
        text = "\n".join(line.rstrip() for line in text.splitlines())
        text = re.sub(r"\n{4,}", "\n\n\n", text).strip()
        if not text:
            raise ValueError("document contains no usable text")
        return text
