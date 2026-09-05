"""Tests for course container endpoints (POST/DELETE /v1/kurse)."""

import json

import httpx
import pytest
import respx


@pytest.mark.asyncio
@respx.mock
async def test_create_kurs_success(async_client: httpx.AsyncClient):
    respx.post("https://api.fahrschulmanager.de/v1/kurse").respond(
        status_code=201,
        json={
            "viewModel": {
                "id": "a65bc36b-9d3f-46f2-9ac1-44ecefc94162",
                "bezeichnung": "Intensivkurs Oktober",
                "kennung": "10/26",
                "beginn": "2026-10-01T18:00:00+02:00",
                "ende": "2026-10-08T00:00:00+02:00",
                "theoriegruppen": ["*"],
                "fidFiliale": "1a8f1403-93ca-4127-962b-ab7c4c917154",
                "anzahlTeilnehmer": 0,
                "maximalteilnehmer": None,
            }
        },
    )

    payload = {
        "kennung": "10/26",
        "bezeichnung": "Intensivkurs Oktober",
        "beginn": "2026-10-01T18:00:00",
        "ende": "2026-10-08T00:00:00",
        "uhrzeit_von": "2026-10-01T18:00:00",
        "uhrzeit_bis": "2026-10-01T21:00:00",
    }
    resp = await async_client.post("/v1/kurse", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["id"] == "a65bc36b-9d3f-46f2-9ac1-44ecefc94162"
    assert data["theoriegruppen"] == ["*"]

    # Verify the outgoing FSM request shape matches the reverse-engineered payload
    sent = respx.calls.last.request
    body = json.loads(sent.content)
    vm = body["viewModel"]
    assert vm["kennung"] == "10/26"
    assert vm["theoriegruppen"] == ["*"]
    assert vm["fidFiliale"] == "1a8f1403-93ca-4127-962b-ab7c4c917154"
    assert vm["id"] == "00000000-0000-0000-0000-000000000000"


@pytest.mark.asyncio
async def test_create_kurs_rejects_end_before_start(async_client: httpx.AsyncClient):
    payload = {
        "kennung": "10/26",
        "bezeichnung": "Kaputter Kurs",
        "beginn": "2026-10-08T00:00:00",
        "ende": "2026-10-01T00:00:00",
        "uhrzeit_von": "2026-10-01T18:00:00",
        "uhrzeit_bis": "2026-10-01T21:00:00",
    }
    resp = await async_client.post("/v1/kurse", json=payload)
    assert resp.status_code == 400


@pytest.mark.asyncio
@respx.mock
async def test_create_kurs_missing_id_returns_bad_gateway(async_client: httpx.AsyncClient):
    respx.post("https://api.fahrschulmanager.de/v1/kurse").respond(
        status_code=201, json={"viewModel": {}}
    )
    payload = {
        "kennung": "10/26",
        "bezeichnung": "Kurs ohne ID",
        "beginn": "2026-10-01T18:00:00",
        "ende": "2026-10-08T00:00:00",
        "uhrzeit_von": "2026-10-01T18:00:00",
        "uhrzeit_bis": "2026-10-01T21:00:00",
    }
    resp = await async_client.post("/v1/kurse", json=payload)
    assert resp.status_code == 502


@pytest.mark.asyncio
@respx.mock
async def test_delete_kurs_success(async_client: httpx.AsyncClient):
    kurs_id = "a65bc36b-9d3f-46f2-9ac1-44ecefc94162"
    delete_route = respx.delete("https://api.fahrschulmanager.de/v1/kurse").respond(
        status_code=200,
        json={"viewModel": {"id": kurs_id}, "responses": [], "location": None},
    )

    resp = await async_client.delete(f"/v1/kurse/{kurs_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["deleted_id"] == kurs_id

    # FSM's delete takes the id in the body, not the path
    sent_body = json.loads(delete_route.calls.last.request.content)
    assert sent_body["viewModel"]["id"] == kurs_id
