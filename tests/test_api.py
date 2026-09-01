import httpx
import pytest

from app.main import create_app
from app.security.auth import DemoAuthenticator


@pytest.fixture
def api_app(settings, service):
    return create_app(settings, service, DemoAuthenticator())


@pytest.mark.asyncio
async def test_api_success_and_unauthenticated(api_app):
    async with api_app.router.lifespan_context(api_app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api_app), base_url="http://test"
        ) as client:
            unauthorized = await client.post(
                "/v1/query", json={"question": "How does vendor approval work?"}
            )
            assert unauthorized.status_code == 401

            success = await client.post(
                "/v1/query",
                headers={"Authorization": "Bearer demo-engineer"},
                json={"question": "How does vendor approval work?"},
            )
            assert success.status_code == 200
            assert success.json()["status"] == "answered"
            assert success.json()["citations"]


@pytest.mark.asyncio
async def test_api_validation_and_role_failure(api_app):
    async with api_app.router.lifespan_context(api_app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api_app), base_url="http://test"
        ) as client:
            invalid = await client.post(
                "/v1/query",
                headers={"Authorization": "Bearer demo-engineer"},
                json={"question": "x"},
            )
            assert invalid.status_code == 422

            forbidden = await client.post(
                "/v1/ingest",
                headers={"Authorization": "Bearer demo-engineer"},
                json={},
            )
            # FastAPI validates body before the endpoint; use debug to demonstrate a clean 403.
            assert forbidden.status_code == 422
            debug = await client.get(
                "/v1/debug/chunks/vendor-policy",
                headers={"Authorization": "Bearer demo-engineer"},
            )
            assert debug.status_code == 403


@pytest.mark.asyncio
async def test_failure_injection_is_local_and_explicit(api_app):
    async with api_app.router.lifespan_context(api_app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api_app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/v1/query",
                headers={
                    "Authorization": "Bearer demo-engineer",
                    "X-Demo-Failure": "search_unavailable",
                },
                json={"question": "How does vendor approval work?"},
            )
            assert response.status_code == 200
            assert response.json()["status"] == "degraded"
