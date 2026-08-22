"""Regressionstests für die Valkey-Serialisierung von Pydantic-Models.

Root Cause (Commit 52b0126): ``ValkeyCache.set`` nutzte ``json.dumps`` ohne
``default``-Handler. Kalender-, Schüler- und Finanz-Endpoints cachen aber
Pydantic-v2-Models (z.B. ``KalenderResponse``), die nicht nativ serialisierbar
sind. Der ``TypeError`` wurde still als WARNING verschluckt -> Cache blieb leer,
jede Anfrage lief live zu FSM. Diese Tests spielen den Valkey-Pfad mit echten
Models durch und hätten den Bug aufgedeckt.
"""

import datetime as dt

import pytest

from app.core.cache import ValkeyCache, _json_default
from app.schemas.kalender import KalenderEvent, KalenderResponse


class _FakeRedis:
    """Minimaler In-Memory-Fake, der die von ValkeyCache genutzten Methoden abbildet."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set(self, key: str, value: str, **kwargs) -> None:
        self.store[key] = value

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def delete(self, key: str) -> int:
        return 1 if self.store.pop(key, None) is not None else 0

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        pass


def _build_kalender_response() -> KalenderResponse:
    ev = KalenderEvent(
        id="evt-1",
        von=dt.datetime(2026, 8, 20, 8, 0, 0),
        bis=dt.datetime(2026, 8, 20, 9, 30, 0),
        fahrlehrer_id="fl-1",
        terminart="FS",
        titel="Fahrstunde Stadtfahrt",
        schueler_name="Max Mustermann",
        dauer_minuten=90.0,
    )
    return KalenderResponse(
        fahrlehrer_id="fl-1",
        start="2026-08-20",
        end="2026-08-21",
        count=1,
        events=[ev],
    )


def test_json_default_serializes_pydantic_model():
    """Ein Pydantic-v2-Model muss via _json_default serialisierbar werden."""
    import json

    resp = _build_kalender_response()
    payload = json.dumps({"v": resp, "exp": 12345}, default=_json_default)
    parsed = json.loads(payload)
    assert parsed["v"]["count"] == 1
    # Datumswerte werden als ISO-String abgelegt
    assert parsed["v"]["events"][0]["von"] == "2026-08-20T08:00:00"
    assert parsed["v"]["events"][0]["dauer_minuten"] == 90.0


def test_json_default_rejects_unknown_types():
    """Nicht-serialisierbare Typen muessen weiterhin TypeError werfen (kein Still-Schlucken)."""
    import json

    with pytest.raises(TypeError):
        json.dumps({"v": object()}, default=_json_default)


@pytest.mark.asyncio
async def test_valkey_roundtrip_with_pydantic_model():
    """ValkeyCache.set/get muss ein echtes Pydantic-Model ueberleben (Regression)."""
    fake = _FakeRedis()
    vc = ValkeyCache(url="redis://fake")
    vc._redis = fake  # type: ignore[attr-defined]
    vc.is_connected = True

    resp = _build_kalender_response()
    await vc.set("kalender:fl-1:2026-08-20:2026-08-21:False:True", resp, ttl=3600)

    cached = await vc.get("kalender:fl-1:2026-08-20:2026-08-21:False:True")
    assert cached is not None
    # Zurueck kommt das Modell als dict (FastAPI re-validiert gegen response_model)
    assert cached["count"] == 1
    assert cached["events"][0]["titel"] == "Fahrstunde Stadtfahrt"
    assert cached["events"][0]["von"] == "2026-08-20T08:00:00"

    # SWR-Pfad
    stale_val, is_stale = await vc.get_or_stale(
        "kalender:fl-1:2026-08-20:2026-08-21:False:True", stale_window=100.0
    )
    assert stale_val["events"][0]["id"] == "evt-1"
    assert is_stale is False


@pytest.mark.asyncio
async def test_valkey_roundtrip_with_nested_datetime_and_decimal():
    """Verschachtelte nicht-native Typen (datetime, decimal) muessen ueberleben."""
    import decimal

    fake = _FakeRedis()
    vc = ValkeyCache(url="redis://fake")
    vc._redis = fake  # type: ignore[attr-defined]
    vc.is_connected = True

    payload = {
        "name": "Erika",
        "saldo": decimal.Decimal("150.50"),
        "geboren": dt.date(1995, 3, 2),
        "stempel": dt.datetime(2026, 1, 1, 12, 30, 0),
    }
    await vc.set("schueler:details:stu-1", payload, ttl=60)

    cached = await vc.get("schueler:details:stu-1")
    assert cached["saldo"] == "150.50"
    assert cached["geboren"] == "1995-03-02"
    assert cached["stempel"] == "2026-01-01T12:30:00"
