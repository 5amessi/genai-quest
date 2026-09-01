from __future__ import annotations

import pytest

from app.bootstrap import build_demo_service
from app.config import Settings
from app.security.auth import DEMO_IDENTITIES


@pytest.fixture
def settings() -> Settings:
    return Settings(
        environment="test",
        backend="local",
        auth_mode="demo",
        retry_attempts=2,
        allow_failure_injection=True,
    )


@pytest.fixture
def service(settings: Settings):
    return build_demo_service(settings)


@pytest.fixture
def engineer():
    return DEMO_IDENTITIES["demo-engineer"]


@pytest.fixture
def hr():
    return DEMO_IDENTITIES["demo-hr"]


@pytest.fixture
def finance():
    return DEMO_IDENTITIES["demo-finance"]


@pytest.fixture
def admin():
    return DEMO_IDENTITIES["demo-admin"]
