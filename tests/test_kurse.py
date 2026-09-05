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


@pytest.mark.asyncio
@respx.mock
async def test_add_kursteilnehmer_success(async_client: httpx.AsyncClient):
    kurs_id = "a65bc36b-9d3f-46f2-9ac1-44ecefc94162"
    schueler_id = "f2473831-0523-47ef-be01-4501c3877239"

    add_route = respx.post("https://api.fahrschulmanager.de/v1/kursteilnehmer").respond(
        status_code=200,
        json={
            "viewModel": {"kursId": kurs_id, "teilnehmer": [schueler_id]},
            "responses": [],
            "location": None,
        },
    )

    resp = await async_client.post(
        f"/v1/kurse/{kurs_id}/teilnehmer", json={"schueler_ids": [schueler_id]}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["kurs_id"] == kurs_id
    assert data["schueler_ids"] == [schueler_id]
    assert data["added_count"] == 1

    sent_body = json.loads(add_route.calls.last.request.content)
    assert sent_body["viewModel"]["kursId"] == kurs_id
    assert sent_body["viewModel"]["teilnehmer"] == [schueler_id]


@pytest.mark.asyncio
async def test_add_kursteilnehmer_rejects_empty_list(async_client: httpx.AsyncClient):
    resp = await async_client.post(
        "/v1/kurse/a65bc36b-9d3f-46f2-9ac1-44ecefc94162/teilnehmer", json={"schueler_ids": []}
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
@respx.mock
async def test_list_kurse(async_client: httpx.AsyncClient):
    respx.get("https://api.fahrschulmanager.de/v2/kurse").respond(
        status_code=200,
        json={
            "rows": [
                {"data": {"id": "kurs-1", "bezeichnung": "Kurs Eins", "kennung": "01/26", "anzahlTeilnehmer": 5}},
                {"data": {"id": "kurs-2", "bezeichnung": "Kurs Zwei", "kennung": "02/26", "anzahlTeilnehmer": 0}},
            ]
        },
    )
    resp = await async_client.get("/v1/kurse")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 2
    assert data["kurse"][0]["id"] == "kurs-1"
    assert data["kurse"][0]["anzahl_teilnehmer"] == 5


@pytest.mark.asyncio
@respx.mock
async def test_get_kurs_detail(async_client: httpx.AsyncClient):
    kurs_id = "a65bc36b-9d3f-46f2-9ac1-44ecefc94162"
    respx.get(f"https://api.fahrschulmanager.de/v1/kurse/{kurs_id}").respond(
        status_code=200,
        json={
            "id": kurs_id,
            "bezeichnung": "Theoriekurs August 26",
            "kennung": "08/26",
            "theoriegruppen": ["A", "B", "*"],
            "anzahlTeilnehmer": 22,
            "maximalteilnehmer": 22,
        },
    )
    resp = await async_client.get(f"/v1/kurse/{kurs_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == kurs_id
    assert set(data["theoriegruppen"]) == {"A", "B", "*"}


@pytest.mark.asyncio
@respx.mock
async def test_get_kurs_detail_not_found(async_client: httpx.AsyncClient):
    kurs_id = "does-not-exist"
    respx.get(f"https://api.fahrschulmanager.de/v1/kurse/{kurs_id}").respond(status_code=200, json=None)
    resp = await async_client.get(f"/v1/kurse/{kurs_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
@respx.mock
async def test_list_kurs_teilnehmer(async_client: httpx.AsyncClient):
    kurs_id = "b0e0107c-0c90-4ef5-bc1a-4cfe4a11cdbc"
    respx.get(f"https://api.fahrschulmanager.de/v1/kursteilnehmer/{kurs_id}").respond(
        status_code=200,
        json={
            "rows": [
                {"data": {"id": "s-1", "vorname": "Max", "nachname": "Muster", "klassen": ["B"]}},
                {"data": {"id": "s-2", "vorname": "Erika", "nachname": "Beispiel", "klassen": ["B"]}},
            ]
        },
    )
    resp = await async_client.get(f"/v1/kurse/{kurs_id}/teilnehmer")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 2
    assert data["teilnehmer"][0]["vorname"] == "Max"
