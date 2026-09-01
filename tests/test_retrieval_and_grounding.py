import pytest

from app.domain.models import AnswerStatus


@pytest.mark.asyncio
async def test_answer_is_grounded_and_cites_current_version(service, engineer):
    response = await service.ask(
        "What is our workflow for approval of an enterprise vendor?", engineer
    )

    assert response.status == AnswerStatus.ANSWERED
    assert response.citations
    assert {citation.document_id for citation in response.citations} == {"vendor-policy"}
    assert {citation.version for citation in response.citations} == {2}
    assert "Procurement" in response.answer


@pytest.mark.asyncio
async def test_poor_retrieval_match_refuses_without_citations(service, engineer):
    response = await service.ask("What color is the cafeteria telescope?", engineer)
    assert response.status == AnswerStatus.INSUFFICIENT_EVIDENCE
    assert response.citations == []


@pytest.mark.asyncio
async def test_equal_authority_conflict_requests_clarification(service, finance):
    response = await service.ask("Who gives final approval for an emergency vendor?", finance)
    assert response.status == AnswerStatus.CLARIFICATION_REQUIRED
    assert {citation.document_id for citation in response.citations} >= {
        "vendor-policy",
        "vendor-exception-memo",
    }


@pytest.mark.asyncio
async def test_debug_includes_scores_and_filter_but_only_for_debug_role(service, admin):
    response = await service.ask("How does vendor approval work?", admin, debug=True)
    assert response.retrieval_debug is not None
    assert response.retrieval_debug.authorization_filter
    assert response.retrieval_debug.hits
    assert all(hit.score >= 0 for hit in response.retrieval_debug.hits)
