from app.ingestion.chunker import StructureAwareChunker


def test_structure_aware_chunker_preserves_headings_and_budget():
    text = "# Policy\n\nIntro paragraph.\n\n## Approval\n\n" + " ".join(
        f"Sentence {index} contains approval evidence." for index in range(80)
    )
    chunks = StructureAwareChunker(max_tokens=90, overlap_tokens=15).split(text)

    assert len(chunks) > 2
    assert any(chunk.heading == "Approval" for chunk in chunks)
    assert all(chunk.token_count <= 90 for chunk in chunks)
    assert all(chunk.content.strip() for chunk in chunks)


def test_chunker_keeps_short_table_together():
    text = "## Limits\n\n| Tier | Approver |\n| --- | --- |\n| High | CFO |"
    chunks = StructureAwareChunker(max_tokens=80, overlap_tokens=10).split(text)
    assert len(chunks) == 1
    assert "| High | CFO |" in chunks[0].content
