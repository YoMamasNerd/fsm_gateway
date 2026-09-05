"""Schemas for FSM course container management (e.g. theory courses)."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field


class KursCreateRequest(BaseModel):
    """Payload to create a new course container in FSM."""

    kennung: str = Field(..., description="Kurzbezeichnung / Kennung (z.B. '10/26')")
    bezeichnung: str = Field(..., description="Anzeigename des Kurses")
    beginn: dt.datetime = Field(..., description="Kursbeginn (ISO-8601)")
    ende: dt.datetime = Field(..., description="Kursende (ISO-8601)")
    uhrzeit_von: dt.datetime = Field(
        ..., description="Tägliche Startuhrzeit (ISO-8601 - FSM wertet hier nur die Uhrzeit aus)"
    )
    uhrzeit_bis: dt.datetime = Field(
        ..., description="Tägliche Enduhrzeit (ISO-8601 - FSM wertet hier nur die Uhrzeit aus)"
    )
    theoriegruppen: list[str] = Field(
        default_factory=lambda: ["*"],
        description="Theoriegruppen-Codes, z.B. ['*'] für Grundstoff oder ['B'] für Klasse-B-Zusatzstoff",
    )
    filiale_id: str | None = Field(
        default=None, description="Filialen-UUID; fällt auf FSM_DEFAULT_FILIALE_ID zurück"
    )
    maximalteilnehmer: int | None = Field(default=None, description="Maximale Teilnehmerzahl (leer = unbegrenzt)")
    ueberbuchung_moeglich: bool = Field(default=True, description="Überbuchung erlauben")
    buchbar_bei_onlineanmeldung: bool = Field(default=True, description="Online buchbar")
    fahrschule123: bool = Field(default=True, description="Sichtbar im Fahrschule123-Buchungsportal")


class KursResponse(BaseModel):
    """Normalized representation of an FSM course."""

    id: str = Field(..., description="FSM Kurs-UUID")
    kennung: str | None = Field(default=None)
    bezeichnung: str | None = Field(default=None)
    beginn: dt.datetime | None = Field(default=None)
    ende: dt.datetime | None = Field(default=None)
    theoriegruppen: list[str] = Field(default_factory=list)
    filiale_id: str | None = Field(default=None)
    anzahl_teilnehmer: int = Field(default=0)
    maximalteilnehmer: int | None = Field(default=None)


class KursListResponse(BaseModel):
    """List of FSM courses."""

    count: int = Field(..., description="Anzahl Kurse")
    kurse: list[KursResponse] = Field(default_factory=list, description="Liste der Kurse")


class KursActionResponse(BaseModel):
    """Generic confirmation for course deletion."""

    success: bool = Field(default=True, description="Erfolgsstatus")
    deleted_id: str | None = Field(default=None, description="Gelöschte Kurs-UUID")


class KursteilnehmerAddRequest(BaseModel):
    """Payload to add one or more students to a course."""

    schueler_ids: list[str] = Field(
        ..., min_length=1, description="Liste der FSM Schüler-UUIDs, die dem Kurs hinzugefügt werden sollen"
    )


class KursteilnehmerAddResponse(BaseModel):
    """Confirmation of students added to a course."""

    success: bool = Field(default=True, description="Erfolgsstatus")
    kurs_id: str = Field(..., description="FSM Kurs-UUID")
    schueler_ids: list[str] = Field(default_factory=list, description="Tatsächlich hinzugefügte Schüler-UUIDs")
    added_count: int = Field(default=0, description="Anzahl hinzugefügter Schüler")


class KursteilnehmerItem(BaseModel):
    """One student enrolled in a course."""

    id: str = Field(..., description="FSM Schüler-UUID")
    vorname: str | None = Field(default=None)
    nachname: str | None = Field(default=None)
    klassen: list[str] = Field(default_factory=list, description="Führerscheinklassen des Schülers")


class KursteilnehmerListResponse(BaseModel):
    """List of students enrolled in a course."""

    kurs_id: str = Field(..., description="FSM Kurs-UUID")
    count: int = Field(..., description="Anzahl Teilnehmer")
    teilnehmer: list[KursteilnehmerItem] = Field(default_factory=list)
