import pytest

from app.domain.models import AnswerStatus, Principal
from app.security.guards import build_odata_security_filter


@pytest.mark.asyncio
async def test_unauthorized_document_never_reaches_answer(service, engineer):
    response = await service.ask("When is the annual employee compensation review?", engineer)

    assert response.status == AnswerStatus.INSUFFICIENT_EVIDENCE
    assert response.citations == []
    assert "November" not in response.answer


@pytest.mark.asyncio
async def test_authorized_hr_user_can_retrieve_hr_document(service, hr):
    response = await service.ask("When is the annual employee compensation review?", hr)

    assert response.status == AnswerStatus.ANSWERED
    assert "November" in response.answer
    assert {citation.document_id for citation in response.citations} == {"hr-compensation"}


def test_security_filter_comes_from_validated_groups():
    principal = Principal(subject="user", groups=frozenset({"engineering", "company-all"}))
    value = build_odata_security_filter(principal)
    assert "company-all,engineering" in value
    assert "is_current eq true" in value


def test_empty_entitlements_create_always_false_filter():
    principal = Principal(subject="user")
    assert build_odata_security_filter(principal) == "false"
