# 📘 FSM API Katalog & Dokumentation (Fahrschulmanager Cloud)

Dieses Dokument dient als zentrale Referenz für alle bekannten und erfassten Schnittstellen der **Fahrschulmanager (FSM) API** (`https://api.fahrschulmanager.de`).

---

## 📌 Quellen & Herkunft der API-Routen

1. **Quelle 1: Live-Traffic Capture (Playwright Monitor)**
   * **Skript:** `fsm_live_monitor.py`
   * **Erfasste Daten:** `captured_traffic.json`
   * **Methode:** Vollständiger HTTP-Netzwerkmitschnitt bei echter Interaktion im Webportal (`portal.fahrschulmanager.de`). Liefert reale Request- und Response-JSON-Strukturen, Header und Query-Parameter.

2. **Quelle 2: Static Analysis des Webportal-Bundles**
   * **Quelle:** `https://portal.fahrschulmanager.de/main.8ec66026204cb9fa.js`
   * **Methode:** Statische Code-Analyse der kompilierten Angular-HTTP-Services (`HttpClient`) zur Rekonstruktion aller in FSM implementierten Endpunkte, Service-Modelle und Aktionen.

---

## 🗺️ Vollständige Zuordnung der FSM-API-Routen zu Gateway-Endpunkten

| Bereich | Upstream FSM API Route | HTTP | Gateway Route (FSM-Gateway) | Status & Cache |
| :--- | :--- | :--- | :--- | :--- |
| **Auth** | `/v1/auth/sso?hardwareId=...` | `POST` | `POST /v1/auth/login` | ✅ Live (PKCE / Token) |
| **Fahrlehrer** | `/v1/lehrer/fahrlehrer?onlyActive=...` | `GET` | `GET /v1/fahrlehrer` | ✅ Live (12h Cache) |
| **Kalender** | `/v1/termine/lehrer/{fl_id}` | `GET` | `GET /v1/kalender/{fl_id}` | ✅ Live (SWR, 12h-24h) |
| **Termine (Create)** | `/v1/termine` | `POST` | `POST /v1/termine` | ✅ Live (Chunking >600m) |
| **Termine (Update)** | `/v1/termine/{termin_id}` | `PUT` | `PUT /v1/termine/{termin_id}` | ✅ Live (Write-Through) |
| **Termine (Delete)** | `/v1/termine/{termin_id}` | `DELETE` | `DELETE /v1/termine/{termin_id}`| ✅ Live (Write-Through) |
| **Schülersuche** | `/v3/schueler/suche` | `GET/POST` | `GET/POST /v1/schueler/suche` | ✅ Live (Kein Cache) |
| **Schülerstammdaten** | `/v1/schueler/kartei/{id}` | `GET` | `GET /v1/schueler/{id}` | ✅ Live (6h Cache) |
| **Ausbildungsstand** | `/v1/ausbildungen/kunde/{id}` | `GET` | `GET /v1/schueler/{id}/ausbildung`| ✅ Live (1h Cache) |
| **Digitale Karteikarte**| `/v1/schueler/kartei/{id}` | `GET` | `GET /v1/schueler/{id}/kartei` | ✅ Live (1h Cache) |
| **Theoriestunden** | `/v2/theoriestunden/kunde/{id}`| `GET` | `GET /v1/schueler/{id}/theorie` | ✅ Live (1h Cache) |
| **Fahrstunden-Historie**| `/v2/fahrstunden/kunde/{id}` | `GET` | `GET /v1/schueler/{id}/fahrstunden`| ✅ Live (5m Cache) |
| **Leistungen & Saldo**| `/v2/leistungen/{id}` | `GET` | `GET /v1/schueler/{id}/leistungen` | ✅ Live (1m Cache) |
| **Zahlung einbuchen** | `/v1/zahlungen` | `POST` | `POST /v1/schueler/{id}/zahlungen` | ✅ Live (Write-Through) |
| **Fuhrpark** | `/v1/fahrzeug?onlyActive=...` | `GET` | `GET /v1/fuhrpark` | ✅ Live (12h Cache) |
| **Filialen** | `/v1/filialen` | `GET` | `GET /v1/stammdaten/filialen` | ✅ Live (24h Cache) |
| **Klassen** | `/v1/klassen` | `GET` | `GET /v1/stammdaten/klassen` | ✅ Live (24h Cache) |
| **Leistungsarten** | `/v1/leistungen/leistungsarten` | `GET` | `GET /v1/stammdaten/leistungsarten`| ✅ Live (24h Cache) |
| **Theoriekapitel** | `/v2/theoriekapitel` | `GET` | `GET /v1/stammdaten/theoriekapitel`| ✅ Live (24h Cache) |
| **Treffpunkte** | `/v2/treffpunkte` | `GET` | `GET /v1/stammdaten/treffpunkte` | ✅ Live (24h Cache) |
| **Prüfungsstatistik (FL)**| `/v2/statistiken/pruefungen/lehrer`| `GET` | `GET /v1/statistiken/pruefungen/lehrer`| ✅ Live (30m Cache) |
| **Prüfungsstatistik (Kl)**| `/v2/statistiken/pruefungen/klassen`| `GET`| `GET /v1/statistiken/pruefungen/klassen`| ✅ Live (30m Cache) |
| **Kassenbücher** | `/v1/kassenbuecher` | `GET` | `GET /v1/kassenbuecher` | ✅ Live (5m Cache) |
| **Kassenbuchungen** | `/v1/kassenbuecher/kassenbuchungen`| `GET` | `GET /v1/kassenbuecher/{id}/buchungen`| ✅ Live (5m Cache) |
| **Webhooks** | `/v1/webhooks/sumup` (extern) | `POST` | `POST /v1/webhooks/sumup` | ✅ Live (Idempotenz) |

---

## 🔮 Zukünftige FSM-Routen (noch nicht im Gateway integriert)

### 1. Rechnungen & Mahnwesen
* `GET /v1/rechnungen/kunde/{student_uuid}` &rarr; Rechnungsübersicht
* `GET /v1/rechnungen/kunde/{student_uuid}/zahlungsziel` &rarr; Fälligkeitsdatum & Zahlungsziel
* `GET /v1/rechnungen/kunde/rechnungsdruckmodel/{student_uuid}` &rarr; Druckvorlage / PDF-Generierung
* `POST /v1/rechnungen/storno` &rarr; Rechnung stornieren

### 2. Preislisten & Preispositionen
* `GET /v1/preislisten` &rarr; Alle aktiven & archivierten Preislisten
* `GET /v1/preislisten/schueler/{student_uuid}` &rarr; Schüler-spezifische Sonderpreise
* `GET /v1/preislisten/preispositionen/{preisliste_id}` &rarr; Detaillierte Gebührenpositionen
* `POST /v1/preislisten/erhoehen` &rarr; Massen-Preiserhöhungen

### 3. SMS-Gateway
* `POST /v1/sms/send/single` &rarr; Einzelne SMS an Schüler
* `POST /v1/sms/send/multiple` &rarr; Massen-SMS
* `GET /v1/sms/kunde/{student_uuid}` &rarr; SMS-Historie
* `GET /v1/sms/available` &rarr; Guthabenstand SMS

### 4. Kurse (z.B. Erste Hilfe, ASF, FES, Theoriekurse)
* `GET /v1/kurse` &rarr; Liste aller Kurse
* `GET /v2/kurse?active=true` &rarr; Liste aktiver Kurse (vom Web-Client verwendet)
* `GET /v1/kurse/{kurs_id}` &rarr; Kursdetails
* `GET /v1/kurse/{kurs_id}/termine` &rarr; Termine eines Kurses
* `POST /v1/kurse` &rarr; Kurs anlegen. Body: `{viewModel: {id: "00000000-0000-0000-0000-000000000000", kennung, bezeichnung, beginn, ende, uhrzeitVon, uhrzeitBis, maximalteilnehmer, ueberbuchungMoeglich, fahrschule123, buchbarBeiOnlineanmeldung, theoriegruppen: [...], filialen: [...], fidFiliale}}`. Antwort (201) liefert die generierte Kurs-UUID in `viewModel.id`. Verifiziert per Live-Traffic-Capture (fsm_live_monitor.py), jetzt auch im Gateway unter `POST /v1/kurse` verfügbar.
* `DELETE /v1/kurse` &rarr; Kurs löschen. **Kein Pfad-Parameter** - UUID steht im Body: `{viewModel: {id: kurs_id}}`. Im Gateway als `DELETE /v1/kurse/{kurs_id}` gekapselt.
* `GET /v1/kurse/anwesenheitsliste` &rarr; Digitale Anwesenheitsliste
* `GET /v1/kursteilnehmer/{kurs_id}` &rarr; Teilnehmerliste eines Kurses
* `POST /v1/kursteilnehmer` &rarr; Teilnehmer zu Kurs hinzufügen. Body: `{viewModel: {teilnehmer: [schueler_uuid, ...], kursId}}`. (Noch nicht im Gateway verdrahtet.)

### 5. Online-Anmeldungen
* `GET /v1/onlineanmeldung/{id}` &rarr; Eingehende Online-Anmeldung
* `GET /v1/onlineanmeldung/token` &rarr; Generierung des Anmelde-Tokens
