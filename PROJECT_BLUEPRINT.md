# 🚀 FSM-Gateway (FastAPI Microservice) – Project Blueprint

> **Wichtiger Hinweis für jede neue Agenten-Session:**
> Dieses Projekt ist das zentrale API-Gateway für alle Interaktionen mit dem **Fahrschulmanager (FSM)**.
> Es wird von drei Django-Diensten auf demselben Host (`DocMan`, `78.47.122.27`) konsumiert:
> 1. `schalti_termine` (Terminbuchung, Fahrlehrer, Kalender, Blocker)
> 2. `django_rechn` (Rechnungen, unbezahlte Fahrstunden, Leistungen)
> 3. `django_diacard` (Schülerkartei, Ausbildungsstatus, Prüfungen)
> 4. *(Zukunft)* `SumUp-Webhooks` (Automatisches Einbuchen von Kartenzahlungen)

---

## 🎯 1. Zweck & Leitprinzipien
1. **Single Source of Truth**: Sämtliche FSM-Kommunikation (Login, Bearer Tokens, Error-Handling, Auto-Refresh bei `401`, Rate Limiting) findet **ausschließlich hier** statt.
2. **Zero Downtime / Instant Hotfixing**: Ändert sich ein Schema im FSM-Portal, wird nur dieser Container gepatcht – keine der 3 Fach-Apps muss neu gebaut oder deployed werden.
3. **Pydantic v2 Typisierung & OpenAPI**: Alle Requests und Responses sind streng typisiert und live unter `http://fsm-gateway:8000/docs` dokumentiert.
4. **Smart Caching**: Listen wie Fahrlehrer oder Stammdaten werden per In-Memory-Cache (TTL) gepuffert, um FSM zu entlasten.

---

## 🏗️ 2. Technische Architektur & Stack
* **Python**: 3.12+
* **Framework**: FastAPI + Uvicorn (asynchron)
* **HTTP-Client**: `httpx.AsyncClient` mit Verbindungs-Pooling
* **Validierung**: Pydantic v2 (`pydantic-settings` für Config)
* **Testing**: `pytest`, `pytest-asyncio`, `respx` (HTTPX Mocking)
* **Deployment**: Docker & Docker Compose auf DocMan (`78.47.122.27`), Port `8090` / internes Netzwerk.

---

## 📂 3. Geplante Verzeichnisstruktur
```
fsm_gateway/
├── app/
│   ├── __init__.py
│   ├── main.py                  # FastAPI App, CORS, Lifespan, Swagger, Exception Handler
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py            # Settings (FSM_EMAIL, FSM_PASSWORD, FSM_BASE_URL, GATEWAY_API_KEY)
│   │   ├── client.py            # Zentraler FSMClient (Session-Pool, Auto-Login, Token-Refresh)
│   │   └── cache.py             # Asynchroner TTL-Cache
│   ├── schemas/
│   │   ├── __init__.py
│   │   ├── auth.py              # Token, Status, Credentials
│   │   ├── fahrlehrer.py        # Fahrlehrer-Models (Name, UUID, Aktiv)
│   │   ├── kalender.py          # Kalender-Events, Fahrstunden, Blocker, Theorie
│   │   ├── schueler.py          # Schülersuche, Kartei-Details, Kontaktdaten
│   │   └── finanzen.py          # Leistungen, unbezahlte Fahrstunden, Zahlungen
│   └── api/
│       ├── __init__.py
│       ├── router.py            # Haupt-Router
│       └── v1/
│           ├── __init__.py
│           ├── auth.py          # GET /v1/auth/status, POST /v1/auth/refresh
│           ├── fahrlehrer.py    # GET /v1/fahrlehrer
│           ├── kalender.py      # GET /v1/kalender/{id}, POST /v1/termine, DELETE /v1/termine/{id}
│           ├── schueler.py      # POST /v1/schueler/suche, GET /v1/schueler/{id}
│           ├── finanzen.py      # GET /v1/schueler/{id}/fahrstunden, GET /v1/schueler/{id}/leistungen, POST /v1/schueler/{id}/zahlung
│           └── webhooks.py      # POST /v1/webhooks/sumup (optional)
├── tests/
│   ├── conftest.py              # Test-Fixtures & FSM Mock Client
│   ├── test_client.py           # Unit-Tests für FSMClient & Auto-Re-Login
│   └── test_api.py              # API-Endpunkt-Tests
├── .env.example
├── .gitignore
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── requirements.txt
├── README.md
└── TODO.md
```

---

## 🌐 4. FSM-Endpunkt-Mapping

| Gateway-Route | FSM-Ziel-Endpunkt | Beschreibung |
|---|---|---|
| `GET /v1/auth/status` | `POST /v1/auth/login` | Prüft Session, liefert Token-Status |
| `GET /v1/fahrlehrer` | `GET /v1/lehrer/fahrlehrer?onlyActive=true` | Liefert Fahrlehrer-Liste (gecached, 5 Min) |
| `GET /v1/kalender/{fahrlehrer_id}` | `GET /v1/termine/kalender/woche/{id}` | Normalisierte Kalender-Events (Fahrstunden, Theorie, Blocker) |
| `POST /v1/termine` | `POST /v1/termine/termin` | Erstellt Blocker (`ST` / `PP`); zerlegt Blöcke > 600 Min |
| `DELETE /v1/termine/{termin_id}` | `DELETE /v1/termine/termin/{id}` | Löscht Termin aus FSM-Kalender |
| `POST /v1/schueler/suche` | `POST /v2/schueler/suche` | Schülersuche mit Volltext, Status & Pagination |
| `GET /v1/schueler/{uuid}` | `GET /v1/schueler/kartei/{uuid}` | Schüler-Stammdaten, Kontaktdaten, FEK |
| `GET /v1/schueler/{uuid}/fahrstunden` | `GET /v2/fahrstunden/kunde/{uuid}` | Liste aller Fahrstunden & Bezahlstatus |
| `GET /v1/schueler/{uuid}/leistungen` | `GET /v2/leistungen/{uuid}` | Leistungskonto, Grundbetrag, Zahlungen |
| `POST /v1/schueler/{uuid}/zahlung` | `POST /v2/leistungen/zahlung` | Bucht Zahlung in FSM ein (SumUp/Bar/Überweisung) |
| `POST /v1/webhooks/sumup` | Interne Logik + `POST Zahlung` | Verarbeitet SumUp Webhook & bucht automatisch ein |

---

## 🚀 5. Startanweisung für die nächste Session
1. Wechsle in `/home/jonas/Workspace/fsm_gateway`.
2. Lies dieses `PROJECT_BLUEPRINT.md` und `TODO.md`.
3. Erstelle die Projektdateien (`requirements.txt`, `app/`, `tests/`, `Dockerfile`, `docker-compose.yml`).
4. Führe die Tests aus und bereite das Deployment auf DocMan vor.
