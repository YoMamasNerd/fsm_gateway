"""Tests for course-level theory schedule endpoints (Theorietermine)."""

import json

import httpx
import pytest
import respx


@pytest.mark.asyncio
@respx.mock
async def test_create_theorietermine_bulk(async_client: httpx.AsyncClient):
    kurs_id = "6c2186b4-c868-4ff5-a94a-1b6c030d0036"
    create_route = respx.post("https://api.fahrschulmanager.de/v1/termine/theorietermin/bulk").respond(
        status_code=201,
        json={
            "viewModel": [
                {
                    "id": "termin-1",
                    "fidKurs": kurs_id,
                    "von": "2026-12-01T18:00:00+01:00",
                    "bis": "2026-12-01T19:30:00+01:00",
                    "kapitel": "1 Persönliche Voraussetzungen",
                    "fidFahrlehrer": ["fl-1"],
                    "fidSystemtheoriegruppe": "*",
                },
                {
                    "id": "termin-2",
                    "fidKurs": kurs_id,
                    "von": "2026-12-01T19:30:00+01:00",
                    "bis": "2026-12-01T21:00:00+01:00",
                    "kapitel": "2 Risikofaktor Mensch",
                    "fidFahrlehrer": ["fl-1"],
                    "fidSystemtheoriegruppe": "*",
                },
            ]
        },
    )

    payload = {
        "termine": [
            {
                "von": "2026-12-01T18:00:00",
                "bis": "2026-12-01T19:30:00",
                "kapitel": "1 Persönliche Voraussetzungen",
                "fahrlehrer_id": "fl-1",
            },
            {
                "von": "2026-12-01T19:30:00",
                "bis": "2026-12-01T21:00:00",
                "kapitel": "2 Risikofaktor Mensch",
                "fahrlehrer_id": "fl-1",
            },
        ]
    }
    resp = await async_client.post(f"/v1/kurse/{kurs_id}/theorietermine", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["created_count"] == 2
    assert data["termine"][0]["kapitel"] == "1 Persönliche Voraussetzungen"

    sent = json.loads(create_route.calls.last.request.content)
    termine = sent["viewModel"]["termine"]
    assert len(termine) == 2
    assert termine[0]["fidKurs"] == kurs_id
    assert termine[0]["fidTerminart"] == "PT"
    assert termine[0]["fidFahrlehrer"] == ["fl-1"]


@pytest.mark.asyncio
async def test_create_theorietermine_rejects_bis_before_von(async_client: httpx.AsyncClient):
    payload = {
        "termine": [
            {
                "von": "2026-12-01T19:30:00",
                "bis": "2026-12-01T18:00:00",
                "kapitel": "Kaputt",
                "fahrlehrer_id": "fl-1",
            }
        ]
    }
    resp = await async_client.post("/v1/kurse/kurs-x/theorietermine", json=payload)
    assert resp.status_code == 400


@pytest.mark.asyncio
@respx.mock
async def test_list_theorietermine(async_client: httpx.AsyncClient):
    kurs_id = "kurs-mit-plan"
    respx.get(f"https://api.fahrschulmanager.de/v1/kurse/{kurs_id}/termine").respond(
        status_code=200,
        json={
            "rows": [
                {
                    "data": {
                        "id": "termin-1",
                        "fidKurs": kurs_id,
                        "von": "2026-12-01T18:00:00+01:00",
                        "bis": "2026-12-01T19:30:00+01:00",
                        "kapitel": "1 Persönliche Voraussetzungen",
                        "fidFahrlehrer": ["fl-1"],
                        "fidSystemtheoriegruppe": "*",
                    }
                }
            ]
        },
    )
    resp = await async_client.get(f"/v1/kurse/{kurs_id}/theorietermine")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    assert data["termine"][0]["kapitel"] == "1 Persönliche Voraussetzungen"


@pytest.mark.asyncio
@respx.mock
async def test_list_theorietermine_kein_tagesplan(async_client: httpx.AsyncClient):
    """Die meisten realen Kurse haben (noch) keinen Tagesplan - leere Liste ist normal, kein Fehler."""
    kurs_id = "kurs-ohne-plan"
    respx.get(f"https://api.fahrschulmanager.de/v1/kurse/{kurs_id}/termine").respond(
        status_code=200, json={"rows": []}
    )
    resp = await async_client.get(f"/v1/kurse/{kurs_id}/theorietermine")
    assert resp.status_code == 200
    assert resp.json()["count"] == 0


@pytest.mark.asyncio
@respx.mock
async def test_update_theorietermin_merges_into_full_object(async_client: httpx.AsyncClient):
    """FSM's PUT braucht das volle Objekt - der Gateway muss es also erst holen."""
    termin_id = "termin-1"
    respx.get(f"https://api.fahrschulmanager.de/v1/termine/theorietermin/{termin_id}").respond(
        status_code=200,
        json={
            "id": termin_id,
            "fidKurs": "kurs-x",
            "von": "2026-12-01T18:00:00+01:00",
            "bis": "2026-12-01T19:30:00+01:00",
            "kapitel": "Alt",
            "bemerkung": "Alt",
            "texte": "TH-Grundstoff\nAlt",
            "fidFahrlehrer": ["fl-1"],
            "fidSystemtheoriegruppe": "*",
            "geloescht": False,
        },
    )
    put_route = respx.put("https://api.fahrschulmanager.de/v1/termine/theorietermin").respond(
        status_code=200, json={"viewModel": {"id": termin_id}}
    )

    resp = await async_client.put(f"/v1/theorietermine/{termin_id}", json={"kapitel": "Neu"})
    assert resp.status_code == 200
    assert resp.json()["success"] is True

    sent = json.loads(put_route.calls.last.request.content)
    vm = sent["viewModel"]
    assert vm["kapitel"] == "Neu"
    assert vm["id"] == termin_id
    assert vm["von"] == "2026-12-01T18:00:00+01:00"  # unveraendert mitgeschickt


@pytest.mark.asyncio
@respx.mock
async def test_delete_theorietermin(async_client: httpx.AsyncClient):
    termin_id = "termin-1"
    delete_route = respx.delete("https://api.fahrschulmanager.de/v1/termine/theorietermin").respond(
        status_code=200, json={"viewModel": {"id": termin_id}}
    )
    resp = await async_client.delete(f"/v1/theorietermine/{termin_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["deleted_id"] == termin_id

    sent = json.loads(delete_route.calls.last.request.content)
    assert sent["viewModel"]["id"] == termin_id
