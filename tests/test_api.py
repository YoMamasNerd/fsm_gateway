"""Integration tests for all FSM Gateway API endpoints."""

import pytest
import respx
import httpx


@pytest.mark.asyncio
async def test_root_and_health(async_client: httpx.AsyncClient):
    resp_root = await async_client.get("/")
    assert resp_root.status_code == 200
    assert resp_root.json()["service"] == "FSM-Gateway"

    resp_health = await async_client.get("/health")
    assert resp_health.status_code == 200
    assert resp_health.json()["status"] == "healthy"


@pytest.mark.asyncio
@respx.mock
async def test_auth_status_endpoint(async_client: httpx.AsyncClient):
    # Mock lightweight test call to verify token
    respx.get("https://api.fahrschulmanager.de/v1/lehrer/fahrlehrer").respond(
        status_code=200,
        json=[{"id": "1", "vorname": "Jonas"}],
    )

    resp = await async_client.get("/v1/auth/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["authenticated"] is True
    assert data["valid"] is True
    assert data["has_credentials"] is True


@pytest.mark.asyncio
@respx.mock
async def test_fahrlehrer_endpoint(async_client: httpx.AsyncClient):
    respx.get("https://api.fahrschulmanager.de/v1/lehrer/fahrlehrer").respond(
        status_code=200,
        json=[
            {
                "id": "658688b4-eb51-418a-9811-bc5445281319",
                "vorname": "Jonas",
                "nachname": "Eisele",
                "istAktiv": True,
                "email": "jonas@fahrschule.de",
            }
        ],
    )

    resp = await async_client.get("/v1/fahrlehrer")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    fl = data["fahrlehrer"][0]
    assert fl["voller_name"] == "Jonas Eisele"
    assert fl["id"] == "658688b4-eb51-418a-9811-bc5445281319"


@pytest.mark.asyncio
@respx.mock
async def test_kalender_and_termine_endpoints(async_client: httpx.AsyncClient):
    fl_id = "658688b4-eb51-418a-9811-bc5445281319"

    # Mock Kalender GET
    respx.get(f"https://api.fahrschulmanager.de/v1/termine/lehrer/{fl_id}").respond(
        status_code=200,
        json=[
            {
                "id": "t-1",
                "von": "2026-08-20T08:00:00.000Z",
                "bis": "2026-08-20T09:30:00.000Z",
                "fidTerminart": "FS",
                "texte": "Fahrstunde Stadtfahrt",
                "schuelername": "Max Mustermann",
            },
            {
                "id": "t-2",
                "von": "2026-08-20T10:00:00.000Z",
                "bis": "2026-08-20T11:30:00.000Z",
                "fidTerminart": "ST",
                "texte": "Sperrzeit Urlaub",
            },
        ],
    )

    resp_cal = await async_client.get(f"/v1/kalender/{fl_id}?von=2026-08-20&bis=2026-08-21")
    assert resp_cal.status_code == 200
    cal_data = resp_cal.json()
    assert cal_data["count"] == 2
    assert cal_data["events"][0]["ist_fahrstunde"] is True
    assert cal_data["events"][0]["dauer_minuten"] == 90.0
    assert cal_data["events"][1]["ist_blocker"] is True

    # Mock POST /v1/termine
    respx.post("https://api.fahrschulmanager.de/v1/termine").respond(
        status_code=200,
        json={"viewModel": {"id": "created-termin-uuid"}},
    )

    create_payload = {
        "fahrlehrer_id": fl_id,
        "von": "2026-08-22T14:00:00",
        "bis": "2026-08-22T15:00:00",
        "titel": "Blocker Schalti Termine",
        "terminart": "PX",
    }
    resp_create = await async_client.post("/v1/termine", json=create_payload)
    assert resp_create.status_code == 201
    assert resp_create.json()["created_ids"] == ["created-termin-uuid"]

    # Mock DELETE /v1/termine
    respx.delete("https://api.fahrschulmanager.de/v1/termine").respond(
        status_code=200,
        json={"success": True},
    )
    resp_del = await async_client.delete("/v1/termine/created-termin-uuid")
    assert resp_del.status_code == 200
    assert resp_del.json()["deleted_id"] == "created-termin-uuid"


@pytest.mark.asyncio
@respx.mock
async def test_schueler_endpoints(async_client: httpx.AsyncClient):
    # Mock Schülersuche
    respx.get("https://api.fahrschulmanager.de/v2/schueler/suche").respond(
        status_code=200,
        json={
            "rows": [
                {
                    "data": {
                        "id": "student-uuid-99",
                        "vorname": "Erika",
                        "nachname": "Musterfrau",
                        "karteiNr": "10025",
                        "saldo": 150.0,
                        "klassen": ["B", "BE"],
                    }
                }
            ]
        },
    )

    resp_search = await async_client.post(
        "/v1/schueler/suche",
        json={"query": "Erika", "only_active": True},
    )
    assert resp_search.status_code == 200
    search_data = resp_search.json()
    assert search_data["count"] == 1
    assert search_data["schueler"][0]["voller_name"] == "Erika Musterfrau"

    # Mock Schülerkartei Details
    respx.get("https://api.fahrschulmanager.de/v1/schueler/kartei/student-uuid-99").respond(
        status_code=200,
        json={
            "id": "student-uuid-99",
            "vorname": "Erika",
            "nachname": "Musterfrau",
            "anrede": "Frau",
            "strasse": "Musterstr. 12",
            "plz": "70173",
            "ort": "Stuttgart",
            "email": "erika@example.com",
            "saldo": 150.0,
            "karteiNr": "10025",
        },
    )

    resp_det = await async_client.get("/v1/schueler/student-uuid-99")
    assert resp_det.status_code == 200
    det_data = resp_det.json()
    assert det_data["strasse"] == "Musterstr. 12"
    assert det_data["anrede"] == "Frau"


@pytest.mark.asyncio
@respx.mock
async def test_finanzen_and_zahlung_endpoints(async_client: httpx.AsyncClient):
    student_id = "student-uuid-99"

    # Mock Fahrstunden
    respx.get(f"https://api.fahrschulmanager.de/v2/fahrstunden/kunde/{student_id}").respond(
        status_code=200,
        json={
            "rows": [
                {
                    "data": {
                        "id": "lesson-1",
                        "beschreibung": "Grundfahrstunde Kl.B am 15.08.2026 um 14:00 Uhr",
                        "minuten": 45.0,
                        "fahrlehrer": "Jonas Eisele",
                        "fahrstundenart": "UW",
                        "kfz": "015",
                        "bezahlt": False,
                        "betrag": 90.0,
                    }
                }
            ]
        },
    )

    resp_fs = await async_client.get(f"/v1/schueler/{student_id}/fahrstunden")
    assert resp_fs.status_code == 200
    fs_data = resp_fs.json()
    assert fs_data["count"] == 1
    assert fs_data["total_minutes"] == 45.0
    assert fs_data["fahrstunden"][0]["datum"] == "15.08.2026"
    assert fs_data["fahrstunden"][0]["zeit"] == "14:00"

    # Mock Leistungen
    respx.get(f"https://api.fahrschulmanager.de/v2/leistungen/{student_id}").respond(
        status_code=200,
        json={
            "rows": [
                {
                    "data": {
                        "id": "fee-1",
                        "leistungsart": "GG",
                        "text": "Grundbetrag Klasse B",
                        "kosten": 350.0,
                        "datum": "2026-08-01",
                    }
                },
                {
                    "data": {
                        "id": "pay-1",
                        "leistungsart": "ZG",
                        "text": "Zahlung SumUp",
                        "zahlung": 350.0,
                        "datum": "2026-08-05",
                    }
                },
            ]
        },
    )

    resp_lst = await async_client.get(f"/v1/schueler/{student_id}/leistungen")
    assert resp_lst.status_code == 200
    lst_data = resp_lst.json()
    assert lst_data["count"] == 2
    assert lst_data["total_kosten"] == 350.0
    assert lst_data["total_zahlungen"] == 350.0

    # Mock Zahlung POST
    respx.post("https://api.fahrschulmanager.de/v2/leistungen/zahlung").respond(
        status_code=200,
        json={"success": True},
    )

    resp_pay = await async_client.post(
        f"/v1/schueler/{student_id}/zahlung",
        json={
            "betrag": 180.0,
            "zahlungsart": "Kartenzahlung",
            "text": "SumUp Terminal",
            "belegnummer": "SUMUP-TX-12345",
        },
    )
    assert resp_pay.status_code == 201
    assert resp_pay.json()["success"] is True
    assert resp_pay.json()["betrag"] == 180.0


@pytest.mark.asyncio
@respx.mock
async def test_sumup_webhook(async_client: httpx.AsyncClient):
    student_id = "student-uuid-99"

    # Mock search for student if needed
    respx.get("https://api.fahrschulmanager.de/v2/schueler/suche").respond(
        status_code=200,
        json={
            "rows": [
                {
                    "data": {
                        "id": student_id,
                        "vorname": "Erika",
                        "nachname": "Musterfrau",
                    }
                }
            ]
        },
    )

    # Mock Zahlung POST
    respx.post("https://api.fahrschulmanager.de/v2/leistungen/zahlung").respond(
        status_code=200,
        json={"success": True},
    )

    webhook_payload = {
        "id": "evt_sumup_12345",
        "event_type": "TRANSACTION_SUCCESSFUL",
        "amount": 90.0,
        "resource_id": "TX-998877",
        "student_name": "Erika Musterfrau",
        "description": "Fahrstunde 15.08",
    }

    resp = await async_client.post("/v1/webhooks/sumup", json=webhook_payload)
    assert resp.status_code == 200
    res_data = resp.json()
    assert res_data["success"] is True
    assert res_data["action_taken"] == "booked_payment"
    assert res_data["student_uuid"] == student_id
    assert res_data["betrag"] == 90.0

    # Second call with same resource_id -> must be idempotent (already_processed)
    resp2 = await async_client.post("/v1/webhooks/sumup", json=webhook_payload)
    assert resp2.status_code == 200
    res2_data = resp2.json()
    assert res2_data["success"] is True
    assert res2_data["action_taken"] == "already_processed"


@pytest.mark.asyncio
@respx.mock
async def test_cache_hit_and_invalidation(async_client: httpx.AsyncClient):
    fl_id = "test-fl-cache"

    respx.get(f"https://api.fahrschulmanager.de/v1/termine/lehrer/{fl_id}").respond(
        status_code=200,
        json=[{"id": "t-cache-1", "von": "2026-08-20T08:00:00.000Z", "bis": "2026-08-20T09:30:00.000Z", "fidTerminart": "FS"}],
    )

    # 1. First call: Cache miss
    resp1 = await async_client.get(f"/v1/kalender/{fl_id}?von=2026-08-20&bis=2026-08-21")
    assert resp1.status_code == 200
    assert resp1.headers.get("x-cache-hit") == "0"

    # 2. Second call: Cache hit (without reaching FSM API)
    resp2 = await async_client.get(f"/v1/kalender/{fl_id}?von=2026-08-20&bis=2026-08-21")
    assert resp2.status_code == 200
    assert resp2.headers.get("x-cache-hit") == "1"

    # 3. Clear cache endpoint
    clear_resp = await async_client.post("/v1/fahrlehrer/cache/clear")
    assert clear_resp.status_code == 200
    assert clear_resp.json()["success"] is True

    # 4. Third call after clear: Cache miss again
    resp3 = await async_client.get(f"/v1/kalender/{fl_id}?von=2026-08-20&bis=2026-08-21")
    assert resp3.status_code == 200
    assert resp3.headers.get("x-cache-hit") == "0"

