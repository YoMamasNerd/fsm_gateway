"""Tests for MetricsCollector, Dashboard UI, and Prometheus endpoint."""

import pytest
import time
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.core.config import settings
from app.core.metrics import MetricsCollector


@pytest.fixture
def temp_metrics(tmp_path):
    db_file = tmp_path / "test_metrics.db"
    collector = MetricsCollector(db_path=str(db_file))
    collector.init_db()
    return collector


@pytest.mark.asyncio
async def test_metrics_collector_record_and_aggregate(temp_metrics):
    # Record some sample requests
    temp_metrics.record_request("GET", "/v1/fahrlehrer", 200, 25.5, cached=False)
    temp_metrics.record_request("GET", "/v1/fahrlehrer", 200, 1.2, cached=True)
    temp_metrics.record_request("GET", "/v1/schueler/suche", 200, 45.0, cached=False)
    temp_metrics.record_request("POST", "/v1/schueler", 500, 110.0, cached=False)

    # Flush batch to SQLite
    await temp_metrics._flush_queue()

    # Verify live stats
    live = temp_metrics.get_live_stats()
    assert live["lifetime_total"] == 4
    assert live["requests_last_60s"] == 4
    assert live["cached_last_60s"] == 1
    assert live["errors_last_60s"] == 1

    # Verify 24h time-series stats
    stats = temp_metrics.get_timeseries_stats("24h")
    summary = stats["summary"]
    assert summary["total_requests"] == 4
    assert summary["cache_hits"] == 1
    assert summary["error_requests"] == 1
    assert summary["cache_hit_ratio_pct"] == 25.0
    assert summary["error_rate_pct"] == 25.0

    # Top endpoints
    top = stats["top_endpoints"]
    assert len(top) == 3
    assert top[0]["path"] == "/v1/fahrlehrer"
    assert top[0]["count"] == 2

    # Prometheus output
    prom = temp_metrics.get_prometheus_metrics()
    assert "fsm_gateway_requests_total 4" in prom
    assert "fsm_gateway_cache_hits_last_24h 1" in prom
    assert "fsm_gateway_errors_last_24h 1" in prom


@pytest.mark.asyncio
async def test_dashboard_endpoints():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Prometheus /metrics endpoint
        res = await client.get("/metrics")
        assert res.status_code == 200
        assert "fsm_gateway_uptime_seconds" in res.text

        # 2. Dashboard HTML
        res_dash = await client.get("/dashboard")
        assert res_dash.status_code == 200
        assert "FSM Gateway" in res_dash.text

        # 3. Dashboard Stats API
        res_stats = await client.get("/dashboard/api/stats?range=24h")
        assert res_stats.status_code == 200
        data = res_stats.json()
        assert "summary" in data
        assert "timeseries" in data
        assert "top_endpoints" in data

        # 4. Dashboard Live API
        res_live = await client.get("/dashboard/api/live")
        assert res_live.status_code == 200
        live_data = res_live.json()
        assert "live" in live_data
        assert "recent" in live_data


@pytest.mark.asyncio
async def test_dashboard_auth_protection(monkeypatch):
    # Set dashboard password
    monkeypatch.setattr(settings, "DASHBOARD_PASSWORD", "secret123")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Without auth: /dashboard shows login page
        res = await client.get("/dashboard")
        assert res.status_code == 200
        assert "Admin Passwort" in res.text

        # Unauthenticated API calls return 401
        res_stats = await client.get("/dashboard/api/stats")
        assert res_stats.status_code == 401

        # Wrong login password
        res_login_bad = await client.post("/dashboard/api/login", json={"password": "wrongpassword"})
        assert res_login_bad.status_code == 401

        # Correct login password
        res_login_ok = await client.post("/dashboard/api/login", json={"password": "secret123"})
        assert res_login_ok.status_code == 200
        assert "fsm_dash_auth" in res_login_ok.cookies

        # Subsequent API call with session cookie works
        cookie = res_login_ok.cookies["fsm_dash_auth"]
        client.cookies.set("fsm_dash_auth", cookie)
        res_auth_stats = await client.get("/dashboard/api/stats")
        assert res_auth_stats.status_code == 200
