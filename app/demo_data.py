from __future__ import annotations

import json
from pathlib import Path

from app.domain.models import DocumentInput


def demo_documents(root: Path | None = None) -> list[DocumentInput]:
    packaged_root = Path(__file__).resolve().parent / "demo_corpus"
    data_root = root or (
        packaged_root
        if packaged_root.exists()
        else Path(__file__).resolve().parents[1] / "data" / "demo"
    )
    manifest = json.loads((data_root / "manifest.json").read_text(encoding="utf-8"))
    documents: list[DocumentInput] = []
    for entry in manifest:
        payload = dict(entry)
        filename = payload.pop("file")
        payload["content"] = (data_root / filename).read_text(encoding="utf-8")
        payload["content_type"] = "markdown"
        documents.append(DocumentInput.model_validate(payload))
    return documents
