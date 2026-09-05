"""Comprehensive regression tests covering cache consistency, X-Cache-Hit headers,
refresh bypass, payment invalidation, and edge cases discovered during the audit.
"""

import httpx
import pytest
import respx

from app.core.cache import cache, classify_cache_key


@pytest.mark.asyncio
@respx.mock
async def test_x_cache_hit_headers_all_endpoints(async_client: httpx.AsyncClient):
    """Verifies X-Cache-Hit is reliably set across all GET/POST endpoints."""
    fl_id = "test-fl-headers-123"
    student_id = "test-student-headers-456"

    # Mock Fahrlehrer
    respx.get("https://api.fahrschulmanager.de/v1/lehrer/fahrlehrer").respond(
        status_code=200,
        json=[{"id": fl_id, "vorname": "Felix", "nachname": "Fahrlehrer", "istAktiv": True}],
    )
    # Mock Kalender
    respx.get(f"https://api.fahrschulmanager.de/v1/termine/lehrer/{fl_id}").respond(
        status_code=200,
        json=[{"id": "t-1", "von": "2026-08-25T10:00:00", "bis": "2026-08-25T11:00:00", "fidTerminart": "FS"}],
    )
    # Mock Schüler Details
    respx.get(f"https://api.fahrschulmanager.de/v1/schueler/{student_id}").respond(
        status_code=200,
        json={"id": student_id, "vorname": "Max", "nachname": "Mustermann", "saldo": "150,00"},
    )
    # Mock Fahrstunden
    respx.get(f"https://api.fahrschulmanager.de/v2/fahrstunden/kunde/{student_id}").respond(
        status_code=200,
        json={"rows": [{"data": {"id": "fs-1", "minuten": 45, "betrag": "90,00", "datum": "2026-08-25"}}]},
    )
    # Mock Leistungen
    respx.get(f"https://api.fahrschulmanager.de/v2/leistungen/{student_id}").respond(
        status_code=200,
        json={"rows": [{"data": {"id": "l-1", "kosten": "350,00", "text": "Grundbetrag"}}]},
    )
    # Mock Suche
    respx.get("https://api.fahrschulmanager.de/v2/schueler/suche").respond(
        status_code=200,
        json={"rows": [{"data": {"id": student_id, "vorname": "Max", "nachname": "Mustermann"}}]},
    )

    # 1. /v1/fahrlehrer
    r1 = await async_client.get("/v1/fahrlehrer")
    assert r1.status_code == 200
    assert r1.headers.get("x-cache-hit") == "0"
    r2 = await async_client.get("/v1/fahrlehrer")
    assert r2.status_code == 200
    assert r2.headers.get("x-cache-hit") == "1"
    r3 = await async_client.get("/v1/fahrlehrer?refresh=true")
    assert r3.status_code == 200
    assert r3.headers.get("x-cache-hit") == "0"

    # 2. /v1/kalender/{fl_id}
    r1 = await async_client.get(f"/v1/kalender/{fl_id}?von=2026-08-25&bis=2026-08-26")
    assert r1.status_code == 200
    assert r1.headers.get("x-cache-hit") == "0"
    r2 = await async_client.get(f"/v1/kalender/{fl_id}?von=2026-08-25&bis=2026-08-26")
    assert r2.status_code == 200
    assert r2.headers.get("x-cache-hit") == "1"
    r3 = await async_client.get(f"/v1/kalender/{fl_id}?von=2026-08-25&bis=2026-08-26&refresh=true")
    assert r3.status_code == 200
    assert r3.headers.get("x-cache-hit") == "0"

    # 3. /v1/schueler/{student_id}
    r1 = await async_client.get(f"/v1/schueler/{student_id}")
    assert r1.status_code == 200
    assert r1.headers.get("x-cache-hit") == "0"
    assert r1.json()["saldo"] == 150.0  # German comma parsed properly
    r2 = await async_client.get(f"/v1/schueler/{student_id}")
    assert r2.status_code == 200
    assert r2.headers.get("x-cache-hit") == "1"
    r3 = await async_client.get(f"/v1/schueler/{student_id}?refresh=true")
    assert r3.status_code == 200
    assert r3.headers.get("x-cache-hit") == "0"

    # 4. /v1/schueler/{student_id}/fahrstunden
    r1 = await async_client.get(f"/v1/schueler/{student_id}/fahrstunden")
    assert r1.status_code == 200
    assert r1.headers.get("x-cache-hit") == "0"
    r2 = await async_client.get(f"/v1/schueler/{student_id}/fahrstunden")
    assert r2.status_code == 200
    assert r2.headers.get("x-cache-hit") == "1"
    r3 = await async_client.get(f"/v1/schueler/{student_id}/fahrstunden?refresh=true")
    assert r3.status_code == 200
    assert r3.headers.get("x-cache-hit") == "0"

    # 5. /v1/schueler/{student_id}/leistungen
    r1 = await async_client.get(f"/v1/schueler/{student_id}/leistungen")
    assert r1.status_code == 200
    assert r1.headers.get("x-cache-hit") == "0"
    r2 = await async_client.get(f"/v1/schueler/{student_id}/leistungen")
    assert r2.status_code == 200
    assert r2.headers.get("x-cache-hit") == "1"
    r3 = await async_client.get(f"/v1/schueler/{student_id}/leistungen?refresh=true")
    assert r3.status_code == 200
    assert r3.headers.get("x-cache-hit") == "0"

    # 6. /v1/schueler/suche (GET and POST)
    r_get = await async_client.get("/v1/schueler/suche?q=Max")
    assert r_get.status_code == 200
    assert r_get.headers.get("x-cache-hit") == "0"

    r_post = await async_client.post("/v1/schueler/suche", json={"query": "Max"})
    assert r_post.status_code == 200
    assert r_post.headers.get("x-cache-hit") == "0"

    # 7. /v1/auth/status
    r_auth = await async_client.get("/v1/auth/status")
    assert r_auth.status_code == 200
    assert r_auth.headers.get("x-cache-hit") == "0"


@pytest.mark.asyncio
@respx.mock
async def test_double_caching_and_refresh_propagation(async_client: httpx.AsyncClient):
    """Ensures refresh=true bypasses both endpoint-level and fsm_client-level cache."""
    fl_id = "test-double-cache-fl"
    student_id = "test-double-cache-stu"

    fl_route = respx.get("https://api.fahrschulmanager.de/v1/lehrer/fahrlehrer")
    fl_route.side_effect = [
        httpx.Response(200, json=[{"id": fl_id, "vorname": "OriginalName"}]),
        httpx.Response(200, json=[{"id": fl_id, "vorname": "UpdatedName"}]),
    ]

    # First call primes cache
    resp1 = await async_client.get("/v1/fahrlehrer")
    assert resp1.json()["fahrlehrer"][0]["vorname"] == "OriginalName"
    assert fl_route.call_count == 1

    # Second call without refresh returns cached
    resp2 = await async_client.get("/v1/fahrlehrer")
    assert resp2.json()["fahrlehrer"][0]["vorname"] == "OriginalName"
    assert fl_route.call_count == 1

    # Third call WITH refresh=true must reach FSM and get UpdatedName!
    resp3 = await async_client.get("/v1/fahrlehrer?refresh=true")
    assert resp3.json()["fahrlehrer"][0]["vorname"] == "UpdatedName"
    assert fl_route.call_count == 2

    # Now test Schüler Details refresh propagation
    stu_route = respx.get(f"https://api.fahrschulmanager.de/v1/schueler/{student_id}")
    stu_route.side_effect = [
        httpx.Response(200, json={"id": student_id, "vorname": "Anna", "nachname": "Old"}),
        httpx.Response(200, json={"id": student_id, "vorname": "Anna", "nachname": "New"}),
    ]

    resp_s1 = await async_client.get(f"/v1/schueler/{student_id}")
    assert resp_s1.json()["nachname"] == "Old"
    assert stu_route.call_count == 1

    resp_s2 = await async_client.get(f"/v1/schueler/{student_id}?refresh=true")
    assert resp_s2.json()["nachname"] == "New"
    assert stu_route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_payment_and_webhook_invalidates_all_student_caches(async_client: httpx.AsyncClient):
    """Checks that booking a payment purges leistungen, details, and fahrstunden caches."""
    student_id = "test-inval-stu-99"

    # Populate caches
    await cache.set(f"schueler:details:{student_id}", {"id": student_id, "cached": True}, ttl=300)
    await cache.set(f"schueler:leistungen:{student_id}:True:1:500", {"items": []}, ttl=300)
    await cache.set(f"schueler:fahrstunden:{student_id}:True:1:100", {"items": []}, ttl=300)
    await cache.set(f"fsm:schueler:{student_id}", {"id": student_id}, ttl=300)
    await cache.set(f"fsm:leistungen:{student_id}", {"items": []}, ttl=300)
    await cache.set(f"fsm:fahrstunden:{student_id}", {"items": []}, ttl=300)

    # Mock Zahlung Vorlage & POST
    respx.get(f"https://api.fahrschulmanager.de/v1/zahlungen/vorlage?fidkunde={student_id}").respond(
        status_code=200,
        json={"fidKunde": student_id, "kunde": "Test Student"},
    )
    respx.post("https://api.fahrschulmanager.de/v1/zahlungen").respond(
        status_code=201,
        json={"viewModel": {"id": "pay-created"}},
    )

    resp_pay = await async_client.post(
        f"/v1/schueler/{student_id}/zahlung",
        json={"betrag": 50.0, "zahlungsart": "Bar", "text": "Barzahlung"},
    )
    assert resp_pay.status_code == 201

    # Verify all related keys are invalidated
    assert await cache.get(f"schueler:details:{student_id}") is None
    assert await cache.get(f"schueler:leistungen:{student_id}:True:1:500") is None
    assert await cache.get(f"schueler:fahrstunden:{student_id}:True:1:100") is None
    assert await cache.get(f"fsm:schueler:{student_id}") is None
    assert await cache.get(f"fsm:leistungen:{student_id}") is None
    assert await cache.get(f"fsm:fahrstunden:{student_id}") is None


@pytest.mark.asyncio
async def test_whitespace_and_uuid_stripping(async_client: httpx.AsyncClient):
    """Ensures leading/trailing whitespace in path UUIDs does not create duplicate cache keys."""
    fl_id = "  fl-with-spaces-123  "
    clean_fl = fl_id.strip()

    with respx.mock:
        respx.get(f"https://api.fahrschulmanager.de/v1/termine/lehrer/{clean_fl}").respond(
            status_code=200,
            json=[],
        )

        resp = await async_client.get(f"/v1/kalender/{fl_id}?von=2026-08-25&bis=2026-08-26")
        assert resp.status_code == 200

        # Cache key should be stored under cleaned UUID
        val = await cache.get(f"kalender:{clean_fl}:2026-08-25:2026-08-26:False:True")
        assert val is not None


@pytest.mark.asyncio
async def test_classify_cache_key_coverage():
    """Verifies classify_cache_key accurately maps all key patterns."""
    assert classify_cache_key("kalender:fl-1:2026-08-20:2026-08-21:False:True")[0] == "kalender"
    assert classify_cache_key("fsm:kalender:fl-1")[0] == "kalender"
    assert classify_cache_key("schueler:fahrstunden:stu-1:True:1:100")[0] == "fahrstunden"
    assert classify_cache_key("fsm:fahrstunden:stu-1")[0] == "fahrstunden"
    assert classify_cache_key("schueler:leistungen:stu-1:True:1:500")[0] == "leistungen"
    assert classify_cache_key("fsm:leistungen:stu-1")[0] == "leistungen"
    assert classify_cache_key("schueler:details:stu-1")[0] == "schueler"
    assert classify_cache_key("fsm:schueler:stu-1")[0] == "schueler"
    assert classify_cache_key("fahrlehrer:active:True")[0] == "fahrlehrer"
    assert classify_cache_key("fsm:fahrlehrer:active:True")[0] == "fahrlehrer"
    assert classify_cache_key("fsm:webhook:processed:evt-1")[0] == "webhooks"
    assert classify_cache_key("fsm:auth_token")[0] == "auth"
    assert classify_cache_key("fsm:api_key")[0] == "auth"


@pytest.mark.asyncio
@respx.mock
async def test_webhook_ambiguous_and_unmatched_student(async_client: httpx.AsyncClient):
    """Verifies that ambiguous student searches in webhooks abort gracefully without charging."""
    respx.get("https://api.fahrschulmanager.de/v2/schueler/suche").respond(
        status_code=200,
        json={
            "rows": [
                {"data": {"id": "stu-1", "vorname": "Michael", "nachname": "Bauer"}},
                {"data": {"id": "stu-2", "vorname": "Michael", "nachname": "Bauer"}},
            ]
        },
    )

    resp = await async_client.post(
        "/v1/webhooks/sumup",
        json={"id": "evt-ambiguous", "amount": 100.0, "student_name": "Michael Bauer"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is False
    assert data["action_taken"] == "ambiguous_student_match"


@pytest.mark.asyncio
@respx.mock
async def test_fsm_network_timeout_error_handling(async_client: httpx.AsyncClient):
    """Verifies that FSM connection timeouts return a structured HTTP 502."""
    respx.get("https://api.fahrschulmanager.de/v1/lehrer/fahrlehrer").mock(
        side_effect=httpx.ConnectTimeout("Connection timed out")
    )

    resp = await async_client.get("/v1/fahrlehrer?refresh=true")
    assert resp.status_code == 502
    data = resp.json()
    assert data["error_type"] == "FsmException"
    assert "Verbindungsfehler" in data["detail"]


@pytest.mark.asyncio
@respx.mock
async def test_student_not_found_404(async_client: httpx.AsyncClient):
    """Verifies that non-existent student UUID returns HTTP 404."""
    respx.get("https://api.fahrschulmanager.de/v1/schueler/non-existent-uuid").respond(
        status_code=200,
        json=None,
    )

    resp = await async_client.get("/v1/schueler/non-existent-uuid")
    assert resp.status_code == 404
    assert "nicht gefunden" in resp.json()["detail"]
