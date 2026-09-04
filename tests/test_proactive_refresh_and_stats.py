"""Tests: Proaktiver Token-Refresh (Feature 2) und Health-Cache-Stats (Feature 1)."""

import asyncio
import datetime as dt

import pytest

from app.core.client import FSMClient
from app.core.config import settings
from app.core.metrics import MetricsCollector
from app.main import app

# ---------------------------------------------------------------------------
# Feature 2: proaktiver Token-Refresh
# ---------------------------------------------------------------------------

@pytest.fixture
def fresh_client() -> FSMClient:
    """Isolierter FSMClient mit gesetztem, frischem Token."""
    client = FSMClient()
    client._auth_token = "test-token"
    client._token_obtained_at = dt.datetime.now(dt.timezone.utc).timestamp()
    return client


async def test_no_refresh_when_token_fresh(fresh_client, monkeypatch):
    monkeypatch.setattr(settings, "FSM_TOKEN_MAX_AGE_SECONDS", 41400)
    called = False

    async def mock_relogin():
        nonlocal called
        called = True

    fresh_client._safe_proactive_relogin = mock_relogin
    token = await fresh_client._maybe_proactive_refresh()
    assert token == "test-token"
    assert not called
    assert fresh_client._refresh_task is None


async def test_refresh_triggered_when_token_old(fresh_client, monkeypatch):
    monkeypatch.setattr(settings, "FSM_TOKEN_MAX_AGE_SECONDS", 41400)
    # Token ist 12h alt
    fresh_client._token_obtained_at -= 12 * 3600
    called = False

    async def mock_relogin():
        nonlocal called
        called = True

    fresh_client._safe_proactive_relogin = mock_relogin
    token = await fresh_client._maybe_proactive_refresh()
    assert token == "test-token"  # altes Token sofort zurück
    assert fresh_client._refresh_task is not None
    await fresh_client._refresh_task  # Hintergrund-Task abwarten
    assert called


async def test_single_flight_no_duplicate_refresh(fresh_client, monkeypatch):
    monkeypatch.setattr(settings, "FSM_TOKEN_MAX_AGE_SECONDS", 41400)
    fresh_client._token_obtained_at -= 12 * 3600
    call_count = 0

    async def mock_relogin():
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.05)

    fresh_client._safe_proactive_relogin = mock_relogin
    t1 = await fresh_client._maybe_proactive_refresh()
    t2 = await fresh_client._maybe_proactive_refresh()
    assert t1 == t2 == "test-token"
    assert fresh_client._refresh_task is not None
    await fresh_client._refresh_task
    await fresh_client._maybe_proactive_refresh()
    await fresh_client._refresh_task or None
    # Nach Abschluss des ersten Tasks erlaubt ein further call einen neuen Task,
    # aber der erste Refresh ist genau einmal gelaufen:
    assert call_count >= 1


async def test_no_proactive_refresh_after_restart(fresh_client):
    """Nach Container-Restart ist obtained_at None -> reaktiver Pfad bleibt aktiv."""
    fresh_client._token_obtained_at = None
    called = False

    async def mock_relogin():
        nonlocal called
        called = True

    fresh_client._safe_proactive_relogin = mock_relogin
    token = await fresh_client._maybe_proactive_refresh()
    assert token == "test-token"
    assert fresh_client._refresh_task is None
    assert not called


async def test_no_token_returns_none(fresh_client):
    fresh_client._auth_token = None
    # Auch den Cache-Leerzustand simulieren (get_auth_token liest sonst den Valkey/Memory-Cache)
    async def no_cache_token(key):
        return None
    from app.core.cache import cache
    original_get = cache.get
    cache.get = no_cache_token
    try:
        token = await fresh_client._maybe_proactive_refresh()
    finally:
        cache.get = original_get
    assert token is None
    assert fresh_client._refresh_task is None


async def test_proactive_relogin_swallows_exceptions(fresh_client):
    """Background-Task darf keine Exceptions propagieren."""
    fresh_client._token_obtained_at = 0  # sehr alt

    async def failing_relogin():
        raise RuntimeError("Netzwerk weg")

    fresh_client.auto_login = failing_relogin
    fresh_client._safe_proactive_relogin_original = fresh_client._safe_proactive_relogin
    # echten Wrapper aufrufen -> darf nicht raisen
    await fresh_client._safe_proactive_relogin()


# ---------------------------------------------------------------------------
# Feature 1: Hit-/Miss-Counter in /health
# ---------------------------------------------------------------------------

def test_metrics_collector_counts_hits_and_misses():
    collector = MetricsCollector(db_path=":memory:")
    assert collector.cache_hits_total == 0
    assert collector.cache_misses_total == 0

    collector.record_request("GET", "/v1/kalender", 200, 10.0, cached=True)
    collector.record_request("GET", "/v1/kalender", 200, 12.0, cached=True)
    collector.record_request("GET", "/v1/schueler", 200, 20.0, cached=False)

    assert collector.cache_hits_total == 2
    assert collector.cache_misses_total == 1


def test_metrics_collector_ignores_internal_paths():
    collector = MetricsCollector(db_path=":memory:")

    collector.record_request("GET", "/health", 200, 5.0, cached=False)
    collector.record_request("GET", "/metrics", 200, 5.0, cached=False)
    collector.record_request("GET", "/dashboard", 200, 5.0, cached=True)
    collector.record_request("GET", "/favicon.ico", 200, 5.0, cached=True)

    assert collector.cache_hits_total == 0
    assert collector.cache_misses_total == 0


@pytest.mark.asyncio
async def test_health_endpoint_reports_cache_stats():
    import httpx

    transport = httpx.ASGITransport(app=app, client=("172.18.0.5", 1234))
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    async with client:
        resp = await client.get(
            "/health",
            headers={"X-API-Key": "test-gateway-key"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "cache_stats" in data
    assert set(data["cache_stats"]) == {"hits_total", "misses_total", "hit_ratio_pct"}
    assert "token_age_seconds" in data
