"""Tests für den Kontrolle-Endpoint (Leistungen je Fahrlehrer/Tag)."""

import pytest
import respx
from httpx import ASGITransport, AsyncClient

from app.core.cache import cache
from app.core.client import fsm_client
from app.main import app

CLIENT_IP_HEADER = {"X-API-Key": "test-gateway-key"}

FL_ID = "38b85e04-b6b2-40bc-be4f-bd1787c262a0"

KONTROLLE_ROWS = [
    {
        "id": "l-1",
        "datum": "2026-07-30T00:00:00+02:00",
        "fidKunde": "stu-1",
        "leistungsart": "ST",
        "displayname": "Börner, Constantin",
        "minuten": 70.0,
        "text": "Pruefungsfahrt Kl.A1",
        "kosten": None,
        "geloescht": False,
        "theoriestunde": None,
    },
    {
        "id": "l-2",
        "datum": "2026-07-30T00:00:00+02:00",
        "fidKunde": "stu-2",
        "leistungsart": "TH",
        "displayname": "Morandi, Angela",
        "minuten": 90.0,
        "text": "1  Fahrer/Beifahrer, Fahrzeug",
        "kosten": None,
        "geloescht": False,
        "theoriestunde": {"fahrlehrerkuerzel": "MH", "von": "18:00", "text": "1  Fahrer/Beifahrer"},
    },
]

ARBEITSZEIT_ROW = {"lehrer": "Hampel Marten", "praxis": 275, "sonstiges": 270}


@pytest.fixture(autouse=True)
async def setup_test_token():
    await cache.clear()
    await fsm_client.set_auth_token("fake-jwt-token-123", ttl=3600)
    yield
    await cache.clear()


@pytest.mark.asyncio
async def test_kontrolle_endpoint_cache_and_sums():
    transport = ASGITransport(app=app, client=("172.18.0.5", 1234))
    async with AsyncClient(transport=transport, base_url="http://test", headers=CLIENT_IP_HEADER) as client:
        with respx.mock(assert_all_called=False) as respx_mock:
            respx_mock.get(
                "https://api.fahrschulmanager.de/v1/leistungen/kontrolle/2026/7/30"
            ).respond(status_code=200, json=KONTROLLE_ROWS)
            respx_mock.get(
                f"https://api.fahrschulmanager.de/v1/lehrer/arbeitszeit/{FL_ID}"
            ).respond(status_code=200, json=ARBEITSZEIT_ROW)

            # 1. Aufruf: Cache Miss
            res1 = await client.get(f"/v1/kontrolle/{FL_ID}", params={"datum": "2026-07-30"})
            assert res1.status_code == 200
            assert res1.headers.get("X-Cache-Hit") == "0"
            data1 = res1.json()
            assert data1["fahrlehrer_id"] == FL_ID
            assert data1["count"] == 2
            # ST=praktisch (70), TH=sonstige (90)
            assert data1["minuten_praktisch"] == 70.0
            assert data1["minuten_sonstige"] == 90.0
            assert data1["minuten_total"] == 160.0
            assert data1["arbeitszeit"]["praxis"] == 275
            assert data1["arbeitszeit"]["sonstiges"] == 270

            # 2. Aufruf: Cache Hit, FSM wird nicht erneut gefragt
            res2 = await client.get(f"/v1/kontrolle/{FL_ID}", params={"datum": "2026-07-30"})
            assert res2.status_code == 200
            assert res2.headers.get("X-Cache-Hit") == "1"
            assert res2.json() == data1

            # 3. refresh=1 umgeht den Cache
            res3 = await client.get(f"/v1/kontrolle/{FL_ID}", params={"datum": "2026-07-30", "refresh": "1"})
            assert res3.headers.get("X-Cache-Hit") == "0"


@pytest.mark.asyncio
async def test_kontrolle_endpoint_arbeitszeit_ausfall_toleriert():
    """Wenn der Arbeitszeit-Abruf scheitert, liefert der Endpoint trotzdem die Leistungen."""
    transport = ASGITransport(app=app, client=("172.18.0.5", 1234))
    async with AsyncClient(transport=transport, base_url="http://test", headers=CLIENT_IP_HEADER) as client:
        with respx.mock(assert_all_called=True) as respx_mock:
            respx_mock.get(
                "https://api.fahrschulmanager.de/v1/leistungen/kontrolle/2026/7/30"
            ).respond(status_code=200, json=KONTROLLE_ROWS)
            respx_mock.get(
                f"https://api.fahrschulmanager.de/v1/lehrer/arbeitszeit/{FL_ID}"
            ).respond(status_code=500, json={"error": "boom"})

            res = await client.get(f"/v1/kontrolle/{FL_ID}", params={"datum": "2026-07-30"})
            assert res.status_code == 200
            data = res.json()
            assert data["count"] == 2
            assert data["arbeitszeit"] is None
