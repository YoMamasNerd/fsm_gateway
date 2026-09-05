"""Tests for error reporting and explanations endpoints (/v1/errors, /v1/fehler)."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.metrics import metrics_collector
from app.main import app


@pytest.fixture(autouse=True)
def clean_error_logs():
    """Ensure error logs are clean before and after each test."""
    metrics_collector.clear_errors()
    yield
    metrics_collector.clear_errors()


@pytest.mark.asyncio
async def test_empty_errors_endpoint(async_client):
    # GET /v1/errors
    res = await async_client.get("/v1/errors")
    assert res.status_code == 200
    data = res.json()
    assert data["has_errors"] is False
    assert data["count"] == 0
    assert data["last_error"] is None
    assert data["errors"] == []
    assert "Keine Fehler" in data["message"]

    # GET /v1/errors/last
    res_last = await async_client.get("/v1/errors/last")
    assert res_last.status_code == 200
    last_data = res_last.json()
    assert last_data["has_error"] is False
    assert last_data["error"] is None


@pytest.mark.asyncio
async def test_record_error_and_retrieve(async_client):
    # Manually record an error with an explanation
    metrics_collector.record_error(
        method="POST",
        path="/v1/theorietermine",
        status_code=400,
        error_type="FsmApiError",
        message="Theorietermin liegt außerhalb des Kurszeitraums.",
        details={"errorCode": "INVALID_DATE_RANGE", "upstream": "FSM_CLOUD"},
        client_ip="127.0.0.1",
    )

    # Check /v1/errors
    res = await async_client.get("/v1/errors")
    assert res.status_code == 200
    data = res.json()
    assert data["has_errors"] is True
    assert data["count"] == 1
    assert data["last_error"] is not None
    assert data["last_error"]["begruendung"] == "Theorietermin liegt außerhalb des Kurszeitraums."
    assert data["last_error"]["status_code"] == 400
    assert data["last_error"]["details"]["errorCode"] == "INVALID_DATE_RANGE"

    # Check German alias /v1/fehler
    res_alias = await async_client.get("/v1/fehler")
    assert res_alias.status_code == 200
    assert res_alias.json()["count"] == 1

    # Check un-prefixed compat /errors and /fehler
    res_compat1 = await async_client.get("/errors")
    assert res_compat1.status_code == 200
    res_compat2 = await async_client.get("/fehler")
    assert res_compat2.status_code == 200

    # Check /v1/errors/last and /v1/fehler/letzter
    res_last = await async_client.get("/v1/errors/last")
    assert res_last.status_code == 200
    assert res_last.json()["has_error"] is True
    assert "Theorietermin liegt außerhalb des Kurszeitraums" in res_last.json()["message"]

    res_letzter = await async_client.get("/v1/fehler/letzter")
    assert res_letzter.status_code == 200
    assert res_letzter.json()["has_error"] is True


@pytest.mark.asyncio
async def test_error_filtering_and_deletion(async_client):
    # Record multiple errors
    metrics_collector.record_error(
        method="GET",
        path="/v1/schueler/123",
        status_code=404,
        error_type="FsmApiError",
        message="Schüler mit ID 123 existiert nicht in FSM.",
    )
    metrics_collector.record_error(
        method="POST",
        path="/v1/kalender/termine",
        status_code=500,
        error_type="FsmException",
        message="Verbindungsabbruch zur Fahrschulmanager-Cloud.",
    )

    # Filter by status code 404
    res_404 = await async_client.get("/v1/errors", params={"status_code": 404})
    assert res_404.status_code == 200
    d404 = res_404.json()
    assert d404["count"] == 1
    assert d404["errors"][0]["status_code"] == 404

    # Filter by path
    res_kalender = await async_client.get("/v1/errors", params={"path": "kalender"})
    assert res_kalender.status_code == 200
    dk = res_kalender.json()
    assert dk["count"] == 1
    assert dk["errors"][0]["path"] == "/v1/kalender/termine"

    # DELETE /v1/errors
    del_res = await async_client.delete("/v1/errors")
    assert del_res.status_code == 200
    assert del_res.json()["deleted_count"] >= 2

    # Verify empty now
    res_after = await async_client.get("/v1/errors")
    assert res_after.json()["count"] == 0


@pytest.mark.asyncio
async def test_validation_error_captured_automatically(async_client):
    """Verify that a 422 validation error on an endpoint automatically records to error logs."""
    # Calling an endpoint with invalid query parameter type
    res = await async_client.get("/v1/statistiken/pruefungen/lehrer", params={"jahr": "ungueltiges_jahr"})
    assert res.status_code == 422

    # Now check /v1/errors
    err_res = await async_client.get("/v1/errors")
    assert err_res.status_code == 200
    err_data = err_res.json()
    assert err_data["has_errors"] is True
    assert err_data["last_error"]["status_code"] == 422
    assert "statistiken" in err_data["last_error"]["path"]
    assert err_data["last_error"]["error_type"] == "RequestValidationError"
    assert len(err_data["last_error"]["begruendung"]) > 0
