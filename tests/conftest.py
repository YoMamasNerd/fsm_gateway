"""Pytest fixtures and test setup."""

import pytest
import pytest_asyncio
import httpx

from app.core.cache import cache
from app.core.client import fsm_client
from app.core.config import settings
from app.main import app


@pytest.fixture(autouse=True)
def setup_test_env(monkeypatch):
    """Setup test environment variables."""
    monkeypatch.setattr(settings, "FSM_EMAIL", "test@fahrschule.de")
    monkeypatch.setattr(settings, "FSM_PASSWORD", "secret123")
    monkeypatch.setattr(settings, "FSM_BASE_URL", "https://api.fahrschulmanager.de")
    monkeypatch.setattr(settings, "FSM_AUTH_URL", "https://login.fahren-lernen.de")
    monkeypatch.setattr(settings, "FSM_PORTAL_URL", "https://portal.fahrschulmanager.de")
    monkeypatch.setattr(settings, "GATEWAY_API_KEY", "test-gateway-key")
    monkeypatch.setattr(settings, "FSM_AUTH_TOKEN", "test-bearer-token-12345")


@pytest_asyncio.fixture(autouse=True)
async def clear_cache_and_client():
    """Clear cache and reset client before each test."""
    await cache.clear()
    await fsm_client.set_auth_token("test-bearer-token-12345")
    yield
    await cache.clear()
    await fsm_client.close()


@pytest_asyncio.fixture
async def async_client():
    """Asynchronous test client for testing FastAPI endpoints."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("172.18.0.5", 1234)),
        base_url="http://testserver",
        headers={"X-API-Key": "test-gateway-key"},
    ) as client:
        yield client
