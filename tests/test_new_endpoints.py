"""Tests for new domain endpoints: Ausbildung, Karteikarte, Theorie, Fuhrpark, Stammdaten, Statistiken, Kassenbuch."""

import json

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient

from app.core.cache import cache
from app.core.client import fsm_client
from app.main import app

CLIENT_IP_HEADER = {"X-API-Key": "test-gateway-key"}


@pytest.fixture(autouse=True)
async def setup_test_token():
    await cache.clear()
    await fsm_client.set_auth_token("fake-jwt-token-123", ttl=3600)
    yield
    await cache.clear()


@pytest.mark.asyncio
async def test_schueler_ausbildung_endpoint():
    transport = ASGITransport(app=app, client=("172.18.0.5", 1234))
    async with AsyncClient(transport=transport, base_url="http://test", headers=CLIENT_IP_HEADER) as client:
        sample_ausbildung = [
            {
                "id": "stu-123",
                "fidklasse": "kl-b",
                "klasse": "B",
                "lfdnr": 1,
                "uebungsfahrten": 12.0,
                "ueberlandfahrten": 5.0,
                "autobahnfahrten": 4.0,
                "nachtfahrten": 3.0,
                "unterweisungen": 1.0,
                "theoriestunden": 14.0,
                "pflicht_theoriestunden": 14.0,
                "bestanden_theorie": True,
            }
        ]

        with respx.mock(assert_all_called=True) as respx_mock:
            respx_mock.get("https://api.fahrschulmanager.de/v1/ausbildungen/kunde/stu-123").respond(
                status_code=200, json=sample_ausbildung
            )

            # 1. First call -> Cache Miss
            res1 = await client.get("/v1/schueler/stu-123/ausbildung")
            assert res1.status_code == 200
            assert res1.headers.get("X-Cache-Hit") == "0"
            data1 = res1.json()
            assert data1["count"] == 1
            assert data1["student_uuid"] == "stu-123"
            assert data1["ausbildungen"][0]["ueberlandfahrten"] == 5.0
            assert data1["ausbildungen"][0]["autobahnfahrten"] == 4.0
            assert data1["ausbildungen"][0]["gesamt_fahrstunden"] == 25.0
            assert data1["ausbildungen"][0]["bestanden_theorie"] is True

        # 2. Second call -> Cache Hit (without calling FSM API)
        res2 = await client.get("/v1/schueler/stu-123/ausbildung")
        assert res2.status_code == 200
        assert res2.headers.get("X-Cache-Hit") == "1"
        assert res2.json()["count"] == 1


@pytest.mark.asyncio
async def test_schueler_karteikarte_endpoint():
    transport = ASGITransport(app=app, client=("172.18.0.5", 1234))
    async with AsyncClient(transport=transport, base_url="http://test", headers=CLIENT_IP_HEADER) as client:
        sample_kartei = {
            "fidFahrlehrer1": "fl-1",
            "fahrlehrer1": "Marten Hampel",
            "fidFahrlehrer2": None,
            "fahrlehrer2": None,
            "pflichttheoriestunden": 14,
            "theoriestunden": 14.0,
            "ruecklauf_datum": "2026-08-01T00:00:00",
            "ruecklaufnummer": "DEKRA-998822",
            "fidsystempruefungssprache": "DEU",
            "ausbildungen": [],
        }

        with respx.mock(assert_all_called=True) as respx_mock:
            respx_mock.get("https://api.fahrschulmanager.de/v1/schueler/kartei/stu-123").respond(
                status_code=200, json=sample_kartei
            )

            res = await client.get("/v1/schueler/stu-123/kartei")
            assert res.status_code == 200
            data = res.json()
            assert data["student_uuid"] == "stu-123"
            assert data["fahrlehrer1"] == "Marten Hampel"
            assert data["ruecklaufnummer"] == "DEKRA-998822"


@pytest.mark.asyncio
async def test_schueler_theorie_endpoint():
    transport = ASGITransport(app=app, client=("172.18.0.5", 1234))
    async with AsyncClient(transport=transport, base_url="http://test", headers=CLIENT_IP_HEADER) as client:
        sample_theorie_table = {
            "tableId": "TheoriestundenTable",
            "rows": [
                {
                    "data": {
                        "id": "th-1",
                        "datum": "2026-08-10T18:00:00",
                        "thema": "4 Schaltstelle Fahrer",
                        "lehrer": "Stefan Richter",
                        "filiale": "Chemnitzer Str.",
                        "dauer": 90.0,
                    }
                }
            ],
        }

        with respx.mock(assert_all_called=True) as respx_mock:
            respx_mock.get("https://api.fahrschulmanager.de/v2/theoriestunden/kunde/stu-123?skipDeleted=true&pagination.pageSize=100").respond(
                status_code=200, json=sample_theorie_table
            )

            res = await client.get("/v1/schueler/stu-123/theorie")
            assert res.status_code == 200
            data = res.json()
            assert data["count"] == 1
            assert data["theoriestunden"][0]["thema"] == "4 Schaltstelle Fahrer"
            assert data["theoriestunden"][0]["lehrer_name"] == "Stefan Richter"


@pytest.mark.asyncio
async def test_schueler_theorie_endpoint_real_fsm_field_names():
    """FSM's actual v2/theoriestunden/kunde/{id} response splits the teacher name into
    fahrlehrerVorname/fahrlehrerNachname (verified via live traffic capture against a
    real account) - not the 'lehrer' field the rest of this test suite mocks."""
    transport = ASGITransport(app=app, client=("172.18.0.5", 1234))
    async with AsyncClient(transport=transport, base_url="http://test", headers=CLIENT_IP_HEADER) as client:
        sample_theorie_table = {
            "tableId": "TheoriestundenTable",
            "rows": [
                {
                    "data": {
                        "id": "0d02cd87-3cc7-4f26-9092-914243c7d8c2",
                        "datum": "2026-09-05T00:00:00+02:00",
                        "kapitel": "1 Persönliche Voraussetzungen",
                        "fahrlehrerVorname": "Jonas",
                        "fahrlehrerNachname": "Eisele",
                        "filiale": "Chemnitzer Str.213   12621 Bln",
                        "minuten": 90.0,
                    }
                }
            ],
        }

        with respx.mock(assert_all_called=True) as respx_mock:
            respx_mock.get(
                "https://api.fahrschulmanager.de/v2/theoriestunden/kunde/stu-real?skipDeleted=true&pagination.pageSize=100"
            ).respond(status_code=200, json=sample_theorie_table)

            res = await client.get("/v1/schueler/stu-real/theorie")
            assert res.status_code == 200
            data = res.json()
            assert data["theoriestunden"][0]["lehrer_name"] == "Jonas Eisele"


@pytest.mark.asyncio
async def test_fuhrpark_endpoint():
    transport = ASGITransport(app=app, client=("172.18.0.5", 1234))
    async with AsyncClient(transport=transport, base_url="http://test", headers=CLIENT_IP_HEADER) as client:
        sample_vehicles = [
            {
                "id": "fz-1",
                "bezeichnung": "Cupra Leon",
                "kennung": "019",
                "kennzeichen": "B SW7187",
                "automatik": True,
                "simulator": False,
                "aktiv": True,
                "klassen": "B",
                "fidFahrlehrer": ["fl-1"],
            }
        ]

        with respx.mock(assert_all_called=True) as respx_mock:
            respx_mock.get("https://api.fahrschulmanager.de/v1/fahrzeug?onlyActive=true").respond(
                status_code=200, json=sample_vehicles
            )

            res = await client.get("/v1/fuhrpark")
            assert res.status_code == 200
            data = res.json()
            assert data["count"] == 1
            assert data["fahrzeuge"][0]["bezeichnung"] == "Cupra Leon"
            assert data["fahrzeuge"][0]["automatik"] is True
            assert data["fahrzeuge"][0]["kennzeichen"] == "B SW7187"


@pytest.mark.asyncio
async def test_stammdaten_endpoints():
    transport = ASGITransport(app=app, client=("172.18.0.5", 1234))
    async with AsyncClient(transport=transport, base_url="http://test", headers=CLIENT_IP_HEADER) as client:
        # 1. Filialen
        sample_filialen = [{"id": "fil-1", "name": "Chemnitzer Str.", "kennung": "HS", "plz": "12621", "ort": "Berlin"}]
        # 2. Klassen
        sample_klassen = [{"id": "kl-1", "bezeichnung": "B", "kuerzel": "B", "fahrzeugart": "PKW"}]
        # 3. Leistungsarten
        sample_leistungen = [{"id": "la-1", "bezeichnung": "Übungsstunde", "kuerzel": "UEB", "preis": "65,00", "dauer": 45.0}]
        # 4. Theoriekapitel
        sample_kapitel = {"rows": [{"data": {"id": "tk-1", "bezeichnung": "4 Schaltstelle Fahrer"}}]}
        # 5. Treffpunkte
        sample_treffpunkte = {"rows": [{"data": {"id": "tp-1", "treffpunkt": "FS Schaltwerk", "ort": "Berlin"}}]}

        with respx.mock(assert_all_called=True) as respx_mock:
            respx_mock.get("https://api.fahrschulmanager.de/v1/filialen").respond(status_code=200, json=sample_filialen)
            respx_mock.get("https://api.fahrschulmanager.de/v1/klassen").respond(status_code=200, json=sample_klassen)
            respx_mock.get("https://api.fahrschulmanager.de/v1/leistungen/leistungsarten").respond(status_code=200, json=sample_leistungen)
            respx_mock.get("https://api.fahrschulmanager.de/v2/theoriekapitel").respond(status_code=200, json=sample_kapitel)
            respx_mock.get("https://api.fahrschulmanager.de/v2/treffpunkte").respond(status_code=200, json=sample_treffpunkte)

            res_fil = await client.get("/v1/stammdaten/filialen")
            assert res_fil.status_code == 200
            assert res_fil.json()["count"] == 1

            res_kl = await client.get("/v1/stammdaten/klassen")
            assert res_kl.status_code == 200
            assert res_kl.json()["count"] == 1

            res_la = await client.get("/v1/stammdaten/leistungsarten")
            assert res_la.status_code == 200
            assert res_la.json()["leistungsarten"][0]["preis"] == 65.0

            res_tk = await client.get("/v1/stammdaten/theoriekapitel")
            assert res_tk.status_code == 200
            assert res_tk.json()["count"] == 1

            res_tp = await client.get("/v1/stammdaten/treffpunkte")
            assert res_tp.status_code == 200
            assert res_tp.json()["count"] == 1


@pytest.mark.asyncio
async def test_statistiken_endpoints():
    transport = ASGITransport(app=app, client=("172.18.0.5", 1234))
    async with AsyncClient(transport=transport, base_url="http://test", headers=CLIENT_IP_HEADER) as client:
        sample_stat_table = {
            "tableId": "StatistikPruefungenTable",
            "rows": [
                {
                    "data": {
                        "values": {
                            "Lehrer": {"value": "Hampel Marten"},
                            "Praxis Anmeldungen": {"value": "52"},
                            "Praxis Bestanden": {"value": "37"},
                            "Praxis Erfolgsquote": {"value": "71.15%"},
                        }
                    }
                }
            ],
        }

        with respx.mock(assert_all_called=True) as respx_mock:
            respx_mock.get("https://api.fahrschulmanager.de/v2/statistiken/pruefungen/lehrer?jahr=2026&zeitraum=1&quartal=0").respond(
                status_code=200, json=sample_stat_table
            )

            res = await client.get("/v1/statistiken/pruefungen/lehrer?jahr=2026&zeitraum=1")
            assert res.status_code == 200
            data = res.json()
            assert data["count"] == 1
            assert data["statistiken"][0]["name"] == "Hampel Marten"
            assert data["statistiken"][0]["anmeldungen"] == 52
            assert data["statistiken"][0]["bestanden"] == 37
            assert data["statistiken"][0]["durchgefallen"] == 15
            assert data["statistiken"][0]["erfolgsquote_pct"] == 71.15


@pytest.mark.asyncio
async def test_kassenbuch_endpoints():
    transport = ASGITransport(app=app, client=("172.18.0.5", 1234))
    async with AsyncClient(transport=transport, base_url="http://test", headers=CLIENT_IP_HEADER) as client:
        sample_kassen = [{"id": "kb-1", "bezeichnung": "Hauptkasse Büro", "aktiv": True}]
        sample_buchungen = [
            {
                "id": "b-1",
                "datum": "2026-08-25T14:00:00",
                "text": "Barzahlung Fahrschüler",
                "einnahme": "150,00",
                "ausgabe": "0,00",
                "saldo": "150,00",
            }
        ]

        with respx.mock(assert_all_called=True) as respx_mock:
            respx_mock.get("https://api.fahrschulmanager.de/v1/kassenbuecher").respond(
                status_code=200, json=sample_kassen
            )
            respx_mock.get("https://api.fahrschulmanager.de/v1/kassenbuecher/kassenbuchungen?fidKassenbuch=kb-1&jahr=2026").respond(
                status_code=200, json=sample_buchungen
            )

            res_kb = await client.get("/v1/kassenbuecher")
            assert res_kb.status_code == 200
            assert res_kb.json()["count"] == 1

            res_b = await client.get("/v1/kassenbuecher/kb-1/buchungen?jahr=2026")
            assert res_b.status_code == 200
            data_b = res_b.json()
            assert data_b["count"] == 1
            assert data_b["buchungen"][0]["einnahme"] == 150.0


@pytest.mark.asyncio
async def test_preislisten_endpoints():
    transport = ASGITransport(app=app, client=("172.18.0.5", 1234))
    async with AsyncClient(transport=transport, base_url="http://test", headers=CLIENT_IP_HEADER) as client:
        sample_pl = [{"id": "pl-1", "bezeichnung": "04/2026", "kennung": "000008", "schuelerpreisliste": False}]
        sample_pos = [
            {
                "id": "pos-1",
                "fidPreisliste": "pl-1",
                "bezeichnung": "Übungsstunde Klasse B",
                "betrag": 75.0,
                "klasse": "B",
                "theorie": False,
                "praxis": True,
            }
        ]

        with respx.mock(assert_all_called=True) as respx_mock:
            respx_mock.get("https://api.fahrschulmanager.de/v1/preislisten").respond(
                status_code=200, json=sample_pl
            )
            respx_mock.get("https://api.fahrschulmanager.de/v1/preislisten/preispositionen/pl-1").respond(
                status_code=200, json=sample_pos
            )
            respx_mock.get("https://api.fahrschulmanager.de/v1/preislisten/schueler/stu-123").respond(
                status_code=200, json=sample_pos
            )

            res_pl = await client.get("/v1/preislisten")
            assert res_pl.status_code == 200
            assert res_pl.json()["count"] == 1

            res_pos = await client.get("/v1/preislisten/pl-1/positionen")
            assert res_pos.status_code == 200
            assert res_pos.json()["count"] == 1
            assert res_pos.json()["preispositionen"][0]["betrag"] == 75.0

            res_stu_pl = await client.get("/v1/schueler/stu-123/preise")
            assert res_stu_pl.status_code == 200
            assert res_stu_pl.json()["count"] == 1


@pytest.mark.asyncio
async def test_theoriestunde_creation_and_vorlage():
    transport = ASGITransport(app=app, client=("172.18.0.5", 1234))
    async with AsyncClient(transport=transport, base_url="http://test", headers=CLIENT_IP_HEADER) as client:
        sample_vorlage = {
            "fidKunde": "stu-123",
            "kunde": "Felix Ackermann",
            "fidfiliale": "fil-1",
            "filiale": "Chemnitzer Str.",
            "fidFahrlehrer": "fl-1",
            "fahrlehrer": "Jonas Eisele",
            "fidSystemtheoriegruppe": "*",
            "von": "2026-08-25T18:00:00+02:00",
            "bis": "2026-08-25T19:30:00+02:00",
            "minuten": 90.0,
            "datum": "2026-08-25T00:00:00+02:00",
        }
        sample_schueler = {"id": "stu-123", "vorname": "Felix", "nachname": "Ackermann"}
        sample_created = {"id": "th-new-1", "fidKunde": "stu-123", "kapitel": "1 Persönliche Voraussetzungen"}

        with respx.mock(assert_all_called=True) as respx_mock:
            respx_mock.get("https://api.fahrschulmanager.de/v1/theoriestunden/vorlage?fidkunde=stu-123").respond(
                status_code=200, json=sample_vorlage
            )
            respx_mock.get("https://api.fahrschulmanager.de/v1/schueler/kartei/stu-123").respond(
                status_code=200, json=sample_schueler
            )
            respx_mock.post("https://api.fahrschulmanager.de/v1/theoriestunden").respond(
                status_code=201, json={"viewModel": [sample_created]}
            )

            # 1. Test Vorlage
            res_vorl = await client.get("/v1/schueler/stu-123/theorie/vorlage")
            assert res_vorl.status_code == 200
            assert res_vorl.json()["kunde"] == "Felix Ackermann"

            # 2. Test Create Theoriestunde
            payload = {
                "fidfiliale": "fil-1",
                "filiale": "Chemnitzer Str.",
                "fidFahrlehrer": "fl-1",
                "fahrlehrer": "Jonas Eisele",
                "fidSystemtheoriegruppe": "*",
                "kapitel": "1 Persönliche Voraussetzungen",
                "datum": "2026-08-25T00:00:00",
                "von": "2026-08-25T18:00:00",
                "bis": "2026-08-25T19:30:00",
                "minuten": 90,
            }
            res_create = await client.post("/v1/schueler/stu-123/theorie", json=payload)
            assert res_create.status_code == 200
            assert res_create.json()["success"] is True


@pytest.mark.asyncio
async def test_theoriestunde_anmeldedatum_wird_automatisch_korrigiert_und_neu_gebucht():
    """FSM lehnt Theoriestunden vor dem Anmeldedatum des Schülers ab (Live-Fund
    09/2026: Schüler nach Kursbeginn in FSM angelegt). Der Gateway soll das
    Anmeldedatum dann automatisch auf den ersten Kurstag vorverlegen und die
    Buchung einmal wiederholen, statt den Fehler nur durchzureichen."""
    transport = ASGITransport(app=app, client=("172.18.0.5", 1234))
    async with AsyncClient(transport=transport, base_url="http://test", headers=CLIENT_IP_HEADER) as client:
        sample_schueler = {"id": "stu-spaet", "vorname": "Neu", "nachname": "Angemeldet"}
        # Voller Datensatz von GET v1/schueler/{id} - nicht die schlankere
        # v1/schueler/kartei/{id} - wird als Quelle fuer den PUT-Body gebraucht
        # (Live-Fund: die schlankere Variante laesst FSMs PUT-Validierung scheitern).
        sample_voller_datensatz = {
            "id": "stu-spaet",
            "anmeldedatum": "2026-08-20T00:00:00+02:00",
            "fidAbrechnungsart": 1,
            "bankverbindung": {"iban": None},
            "skipDuplicateCheck": True,
        }

        with respx.mock(assert_all_called=True) as respx_mock:
            respx_mock.get("https://api.fahrschulmanager.de/v1/schueler/kartei/stu-spaet").respond(
                status_code=200, json=sample_schueler
            )
            respx_mock.get("https://api.fahrschulmanager.de/v1/schueler/stu-spaet").respond(
                status_code=200, json=sample_voller_datensatz
            )
            respx_mock.post("https://api.fahrschulmanager.de/v1/theoriestunden").mock(
                side_effect=[
                    httpx.Response(
                        400,
                        json={
                            "responses": [
                                {
                                    "errorMessage": (
                                        "Das Datum der Theoriestunde darf nicht vor dem "
                                        "Anmeldedatum des Schülers liegen.\n"
                                        "Soll das Anmeldedatum auf den 01.08.2026 geändert werden?"
                                    )
                                }
                            ]
                        },
                    ),
                    httpx.Response(
                        201, json={"viewModel": [{"id": "th-neu-1", "fidKunde": "stu-spaet"}]}
                    ),
                ]
            )
            respx_mock.get("https://api.fahrschulmanager.de/v1/preislisten/schueler/stu-spaet").respond(
                status_code=200, json=[]
            )
            put_route = respx_mock.put("https://api.fahrschulmanager.de/v1/schueler").respond(
                status_code=200, json={"viewModel": sample_voller_datensatz}
            )

            payload = {
                "fidfiliale": "fil-1",
                "filiale": "Chemnitzer Str.",
                "fidFahrlehrer": "fl-1",
                "fahrlehrer": "Jonas Eisele",
                "fidSystemtheoriegruppe": "*",
                "kapitel": "1 Persönliche Voraussetzungen",
                "datum": "2026-08-01T00:00:00",
                "von": "2026-08-01T18:00:00",
                "bis": "2026-08-01T19:30:00",
                "minuten": 90,
                "kurs_start_datum": "2026-08-01",
            }
            res = await client.post("/v1/schueler/stu-spaet/theorie", json=payload)

            assert res.status_code == 200
            assert res.json()["success"] is True

            # Anmeldedatum wurde auf den uebergebenen Kursstart (nicht das FSM-Vorschlagsdatum) gesetzt
            sent_kartei = json.loads(put_route.calls.last.request.content)["viewModel"]
            assert sent_kartei["anmeldedatum"].startswith("2026-08-01")


@pytest.mark.asyncio
async def test_theoriestunde_anderer_400_fehler_wird_nicht_verschluckt():
    """Ein 400-Fehler, der nichts mit dem Anmeldedatum zu tun hat, darf nicht
    stillschweigend als Anmeldedatum-Problem behandelt werden."""
    transport = ASGITransport(app=app, client=("172.18.0.5", 1234))
    async with AsyncClient(transport=transport, base_url="http://test", headers=CLIENT_IP_HEADER) as client:
        with respx.mock(assert_all_called=True) as respx_mock:
            respx_mock.get("https://api.fahrschulmanager.de/v1/schueler/kartei/stu-x").respond(
                status_code=200, json={"id": "stu-x", "vorname": "Test", "nachname": "Fall"}
            )
            respx_mock.post("https://api.fahrschulmanager.de/v1/theoriestunden").respond(
                status_code=400, json={"responses": [{"errorMessage": "Kapitel ist ein Pflichtfeld."}]}
            )

            payload = {
                "fidfiliale": "fil-1",
                "filiale": "Chemnitzer Str.",
                "fidFahrlehrer": "fl-1",
                "fahrlehrer": "Jonas Eisele",
                "fidSystemtheoriegruppe": "*",
                "kapitel": "",
                "datum": "2026-08-01T00:00:00",
                "von": "2026-08-01T18:00:00",
                "bis": "2026-08-01T19:30:00",
                "minuten": 90,
            }
            res = await client.post("/v1/schueler/stu-x/theorie", json=payload)
            assert res.status_code >= 400


@pytest.mark.asyncio
async def test_tagesbelegung_endpoint():
    transport = ASGITransport(app=app, client=("172.18.0.5", 1234))
    async with AsyncClient(transport=transport, base_url="http://test", headers=CLIENT_IP_HEADER) as client:
        sample_belegung = {"gesamt": 14, "praxis": 10}
        sample_filialen = [{"id": "fil-1", "name": "Chemnitzer Str."}]

        with respx.mock(assert_all_called=True) as respx_mock:
            respx_mock.get("https://api.fahrschulmanager.de/v1/filialen").respond(
                status_code=200, json=sample_filialen
            )
            respx_mock.get("https://api.fahrschulmanager.de/v1/termine/tagesbelegung/fil-1?date=2026-08-25T00:00:00.000Z").respond(
                status_code=200, json=sample_belegung
            )

            res = await client.get("/v1/termine/tagesbelegung?datum=2026-08-25")
            assert res.status_code == 200
            data = res.json()
            assert data["datum"] == "2026-08-25"
            assert data["gesamt"] == 14
            assert data["praxis"] == 10
