# FSM-Gateway 🚗⚡

Zentraler **FastAPI-Microservice** zur performanten und typisierten Anbindung der **Fahrschulmanager (FSM) REST-API** für das gesamte Fahrschul-Ökosystem:
* `schalti_termine` (Terminbuchung, Fahrlehrer, Kalender-Synchronisation, Blocker)
* `django_rechn` (Rechnungsstellung, unbezahlte Fahrstunden, Leistungskonto)
* `django_diacard` (Schülerkartei, Ausbildungsstatus, Klassen)
* `SumUp-Webhooks` (Automatisches Einbuchen von Kartenzahlungen)

---

## 🎯 Kernfunktionen
1. **Single Source of Truth**: Zentraler OAuth2 PKCE Login & Session-Pooling mit automatischem Re-Login bei `401 Unauthorized`.
2. **Smart In-Memory Caching**: 5-Minuten TTL-Cache für Fahrlehrer und statische Stammdaten.
3. **Pydantic v2 Typisierung**: Vollständige OpenAPI & Swagger-Dokumentation unter `http://localhost:8090/docs`.
4. **Intelligentes Chunking**: Automatische Zerlegung von Terminen/Blockern > 600 Minuten in Teilblöcke.
5. **Zero Downtime Hotfixing**: Bei API-Änderungen von FSM muss nur dieser Microservice angepasst werden.

---

## 🌐 Endpunkt-Übersicht

| Gateway-Route | Methode | Beschreibung |
|---|---|---|
| `GET /v1/auth/status` | `GET` | Prüft FSM Session, Token-Status & Gültigkeit |
| `POST /v1/auth/refresh` | `POST` | Erzwingt neuen Login-Handshake gegen FSM |
| `GET /v1/fahrlehrer` | `GET` | Liefert Fahrlehrer-Liste (5 Min. gecached) |
| `POST /v1/fahrlehrer/refresh-cache` | `POST` | Invalidiert Fahrlehrer-Cache und lädt neu |
| `GET /v1/kalender/{fahrlehrer_id}` | `GET` | Normalisierte Kalender-Events (Fahrstunden, Theorie, Blocker) |
| `POST /v1/termine` | `POST` | Erstellt Termin/Blocker in FSM (zerlegt > 600 Min.) |
| `PUT /v1/termine/{termin_id}` | `PUT` | Aktualisiert Termin in FSM |
| `DELETE /v1/termine/{termin_id}` | `DELETE` | Löscht Termin aus FSM |
| `POST /v1/schueler/suche` | `POST` | Schülersuche mit Pagination & Statusfiltern |
| `GET /v1/schueler/{student_uuid}` | `GET` | Vollständige Schülerkartei, Kontaktdaten & Klassen |
| `GET /v1/schueler/{student_uuid}/fahrstunden` | `GET` | Fahrstunden-Historie & Bezahlstatus |
| `GET /v1/schueler/{student_uuid}/leistungen` | `GET` | Leistungskonto, Grundbetrag, Gebühren & Zahlungen |
| `POST /v1/schueler/{student_uuid}/zahlung` | `POST` | Bucht Zahlung in FSM ein (Kartenzahlung/SumUp/Bar) |
| `POST /v1/webhooks/sumup` | `POST` | SumUp Webhook zur automatischen Zahlungseinbuchung |
| `GET /health` | `GET` | System-Healthcheck & Cache-Status |

---

## 🚀 Schnellstart & Lokale Ausführung

### 1. Umgebungsvariablen konfigurieren
Kopiere `.env.example` nach `.env` und trage die FSM-Zugangsdaten ein:
```bash
cp .env.example .env
```

### 2. Virtuelle Umgebung & Abhängigkeiten
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Server starten
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8090 --reload
```
Swagger Doku ist erreichbar unter: [http://localhost:8090/docs](http://localhost:8090/docs)

### 4. Tests ausführen
```bash
pytest -v
```

---

## 🐳 Docker Deployment

### Docker Compose
```bash
docker compose up -d --build
```
Der Container lauscht auf Port `8090` und verfügt über einen integrierten Healthcheck.
