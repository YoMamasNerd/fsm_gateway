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
| **Theoriestunde buchen** | `/v1/theoriestunden` | `POST` | `POST /v1/schueler/{id}/theorie` | ✅ Live (Write-Through, Anmeldedatum-Autokorrektur siehe unten) |
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

## 🩹 Selbstheilung: Anmeldedatum-Validierung

**Fund (Live-Betrieb, 09/2026):** FSM lehnt sowohl Zahlungen (`POST /v1/zahlungen`) als
auch Theoriestunden-Buchungen (`POST /v1/theoriestunden`) mit `400` ab, wenn deren
Datum vor dem `anmeldedatum` des Schülers liegt - typischerweise weil ein Schüler
erst nach Kursbeginn in FSM angelegt wurde, aber schon vorher am Unterricht
teilgenommen hat. FSM selbst schlägt in der Fehlermeldung ein Korrekturdatum vor
(z.B. *"Das Datum der Theoriestunde darf nicht vor dem Anmeldedatum des Schülers
liegen. Soll das Anmeldedatum auf den 08.08.2026 geändert werden?"*).

Beide Buchungspfade (`record_zahlung`, `create_theoriestunde` in `app/core/client.py`)
fangen diesen `400`-Fehler ab, verlegen das Anmeldedatum per `PUT v1/schueler`
automatisch vor und wiederholen die Buchung einmal:
- Bei Zahlungen auf das Zahlungsdatum selbst.
- Bei Theoriestunden auf das optionale `kurs_start_datum` im Request-Body (fällt
  auf das Stundendatum zurück, falls nicht mitgeschickt) - der aufrufende Client
  (z.B. `schalti_theorie`) sollte hier den ersten Kurstag übergeben, nicht das
  Datum der einzelnen Lektion, damit spätere Lektionen desselben Schülers nicht
  erneut denselben Fehler auslösen.

**Falle beim Schreiben (Live-Fund via `fsm_live_monitor`, 09/2026):** Der PUT-Body
für `update_schueler_anmeldedatum` muss von `GET v1/schueler/{id}` stammen, **nicht**
von der schlankeren `GET v1/schueler/kartei/{id}` (die z.B. für Lesezugriffe wie
`get_schueler_details` genutzt wird). Die Kartei-Variante lässt Felder wie
`fidAbrechnungsart`, `bankverbindung` oder `skipDuplicateCheck` weg - fehlen die im
PUT-Body, lehnt FSM mit einem irreführenden `500 "An error occurred during
authorization"` ab (kein echtes Auth-Problem, sondern eine serverseitige
Validierung des vollständigen Datensatzes). Live im echten FSM-Webportal
nachgestellt: identischer PUT-Request, aber mit `GET v1/schueler/{id}` als Quelle,
funktioniert (`200`).

**Zweite Falle - `kundenpreise` nicht überschreiben:** `GET v1/preislisten/schueler/{id}`
klingt nach "Preisliste des Schülers", liefert aber tatsächlich den **globalen
Preislisten-Katalog** des ganzen Kontos (alle je angelegten Preislisten,
Felder `bezeichnung`/`kennung`/...). Die echte Zuordnung des Schülers
(`fidkunde`/`fidpreisliste`/`lfdnr`) steht schon im `kundenpreise`-Feld der
`GET v1/schueler/{id}`-Antwort selbst - dieses Feld unverändert im PUT-Body
mitschicken, niemals mit dem Katalog überschreiben. Live-Fund: bei einem
Schüler quittierte FSM das überschriebene Feld mit `400 "Bitte geben Sie eine
gültige 1. Preisliste an"`, bei einem anderen wurde es augenscheinlich
stillschweigend ignoriert (Datensatz blieb laut Nachprüfung intakt) - so oder
so ist Überschreiben unnötig und riskant.

**Dritte Falle - dieselbe Verwechslung nochmal, diesmal in `get_schueler_details`:**
Der interne Client (`FSMClient.get_schueler_details`, hinter dem öffentlichen
Endpunkt `GET /v1/schueler/{id}` UND innerhalb von `record_zahlung` und
`create_theoriestunde` genutzt) rief bis 09/2026 ebenfalls die schlankere
`v1/schueler/kartei/{id}` auf, obwohl der Endpunktname und die Docstring "volle
Stammdaten" versprechen. Folge: jede Auswertung von `vorhandeneKlassen`
(Mehrfach-FEK) über diesen Weg lieferte immer nur die eine bereits bekannte
Klasse zurück, egal wie oft man es erneut versuchte - der Cache-Refresh half
nicht, weil der zugrunde liegende Request von Anfang an die falsche Route traf.
Jetzt korrekt auf `v1/schueler/{id}` umgestellt.

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
* `GET /v1/kurse/{kurs_id}` &rarr; Kursdetails (flaches JSON, **nicht** Table-Format - anders als die Liste!). Im Gateway: `GET /v1/kurse/{kurs_id}`.
* `POST /v1/kurse` &rarr; Kurs anlegen. Body: `{viewModel: {id: "00000000-0000-0000-0000-000000000000", kennung, bezeichnung, beginn, ende, uhrzeitVon, uhrzeitBis, maximalteilnehmer, ueberbuchungMoeglich, fahrschule123, buchbarBeiOnlineanmeldung, theoriegruppen: [...], filialen: [...], fidFiliale}}`. Antwort (201) liefert die generierte Kurs-UUID in `viewModel.id`. **Wichtig: `kennung` ist auf maximal 10 Zeichen begrenzt** ("Kennung darf maximal 10 Zeichen lang sein."), live verifiziert. Im Gateway: `POST /v1/kurse`.
* `DELETE /v1/kurse` &rarr; Kurs löschen. **Kein Pfad-Parameter** - UUID steht im Body: `{viewModel: {id: kurs_id}}`. Im Gateway als `DELETE /v1/kurse/{kurs_id}` gekapselt.
* `GET /v1/kurse/anwesenheitsliste` &rarr; Digitale Anwesenheitsliste
* `GET /v1/kursteilnehmer/{kurs_id}` &rarr; Teilnehmerliste eines Kurses (Table-Format). Im Gateway normalisiert verfügbar als `GET /v1/kurse/{kurs_id}/teilnehmer`.
* `POST /v1/kursteilnehmer` &rarr; Teilnehmer zu Kurs hinzufügen. Body: `{viewModel: {teilnehmer: [schueler_uuid, ...], kursId}}`, Antwort echot `{kursId, teilnehmer}` zurück. Im Gateway verfügbar als `POST /v1/kurse/{kurs_id}/teilnehmer`.

#### 4a. Theorietermine (Kurs-Tagesplan - eigenständiges Konzept, siehe unten)

**Wichtiger Fund (Live-Traffic-Capture, 2026-09-05):** FSM hat einen eigenständigen
Tagesplan-Mechanismus pro Kurs, komplett getrennt von der Schüler-Einzelbuchung unter
Punkt 6 (Theoriestunden). Ein *Theorietermin* gehört zum **Kurs** (`fidKurs`) und
beschreibt, wann welches Thema stattfindet; eine *Theoriestunde* gehört zum
**Schüler** (`fidKunde`) und beschreibt, ob er teilgenommen hat. Die meisten realen
Kurse in diesem Account hatten bei der ersten Untersuchung **keinen** Tagesplan
hinterlegt (0 Zeilen) - das Feature wird also nicht durchgehend genutzt, existiert
aber und wurde live erfolgreich durchgespielt (anlegen/lesen/ändern/löschen).

* `GET /v1/kurse/{kurs_id}/termine` &rarr; Tagesplan eines Kurses (Table-Format: `Datum`, `Uhrzeit`, `Minuten`, `Kapitel`). Im Gateway: `GET /v1/kurse/{kurs_id}/theorietermine`.
* `POST /v1/termine/theorietermin/bulk` &rarr; Legt einen oder mehrere Termine auf einmal an. Body: `{viewModel: {termine: [{von, bis, fidKurs, fidTerminart: "PT", gebucht: false, fidFahrlehrer: [...], fidSystemtheoriegruppe, fidFiliale, kapitel}, ...]}}`. Antwort (201) liefert `viewModel` als Liste der vollen erzeugten Objekte (inkl. generierter `id`, auto-gebautem `texte` = `"TH-Grundstoff\n{kapitel}"`, berechnetem `minuten`). Im Gateway: `POST /v1/kurse/{kurs_id}/theorietermine`.
* `GET /v1/termine/theorietermin/{id}` &rarr; Einzelnen Theorietermin abrufen (volles Objekt, nicht Table-Format) - wird für Update benötigt.
* `PUT /v1/termine/theorietermin` &rarr; Termin aktualisieren. **Kein Pfad-Parameter, kein partielles Update** - FSM erwartet das komplette Objekt inkl. `id` im Body, unveränderte Felder müssen mitgeschickt werden. Im Gateway: `PUT /v1/theorietermine/{termin_id}` (holt den aktuellen Stand selbst und merged).
* `DELETE /v1/termine/theorietermin` &rarr; Termin löschen. Body: `{viewModel: {id: termin_id}}`. Im Gateway: `DELETE /v1/theorietermine/{termin_id}`.

### 5. Online-Anmeldungen
* `GET /v1/onlineanmeldung/{id}` &rarr; Eingehende Online-Anmeldung
* `GET /v1/onlineanmeldung/token` &rarr; Generierung des Anmelde-Tokens
