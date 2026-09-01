import pytest

from app.domain.errors import TransientProviderError
from app.domain.models import AnswerDraft, AnswerStatus
from app.resilience import RetryPolicy, retry_async


@pytest.mark.asyncio
async def test_search_failure_degrades_and_never_calls_model(service, engineer):
    response = await service.ask(
        "How does vendor approval work?", engineer, failure_mode="search_unavailable"
    )
    assert response.status == AnswerStatus.DEGRADED
    assert response.model_route.value == "no_llm"
    assert response.citations == []


@pytest.mark.asyncio
async def test_model_timeout_returns_predictable_degraded_response(service, engineer):
    response = await service.ask(
        "How does vendor approval work?", engineer, failure_mode="llm_timeout"
    )
    assert response.status == AnswerStatus.DEGRADED
    assert response.citations == []


@pytest.mark.asyncio
async def test_retry_succeeds_after_transient_failure():
    calls = 0

    async def flaky():
        nonlocal calls
        calls += 1
        if calls < 3:
            raise TransientProviderError("try again")
        return "ok"

    result = await retry_async(
        flaky, RetryPolicy(attempts=3, timeout_seconds=1, base_delay_seconds=0)
    )
    assert result == "ok"
    assert calls == 3


@pytest.mark.asyncio
async def test_invalid_model_citation_is_rejected(service, engineer):
    class InvalidGenerator:
        async def generate(self, *_args, **_kwargs):
            return AnswerDraft(
                answer="Invented answer", cited_chunk_ids=["unauthorized-chunk"], confidence=1
            )

    service.generator = InvalidGenerator()
    response = await service.ask("How does vendor approval work?", engineer)
    assert response.status == AnswerStatus.DEGRADED
    assert response.citations == []
