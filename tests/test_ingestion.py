import pytest

from app.demo_data import demo_documents
from app.domain.errors import IngestionConflictError
from app.domain.models import IngestionStatus


@pytest.mark.asyncio
async def test_duplicate_ingestion_is_idempotent(service, admin):
    await service.initialize()
    response = await service.ingest(demo_documents()[1], admin)
    assert response.status == IngestionStatus.DUPLICATE
    assert response.chunks_indexed == 0


@pytest.mark.asyncio
async def test_same_version_with_changed_content_is_conflict(service, admin):
    await service.initialize()
    changed = demo_documents()[1].model_copy(
        update={"content": demo_documents()[1].content + "\nChanged without a version bump."}
    )
    with pytest.raises(IngestionConflictError):
        await service.ingest(changed, admin)


@pytest.mark.asyncio
async def test_superseded_version_remains_debuggable_but_not_current(service, admin):
    chunks = await service.debug_chunks("vendor-policy", admin)
    assert {chunk.version for chunk in chunks} == {1, 2}
    assert all(not chunk.is_current for chunk in chunks if chunk.version == 1)
    assert all(chunk.is_current for chunk in chunks if chunk.version == 2)
