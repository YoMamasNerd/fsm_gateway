"""Pydantic schemas for theory lessons (Theoriestunden) and chapters (Theoriekapitel)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class TheoriestundeItem(BaseModel):
    """A completed or booked theory lesson for a student."""

    id: str = Field(..., description="ID der Theoriestunde")
    datum: str | None = Field(None, description="Datum der Theoriestunde (ISO)")
    thema: str | None = Field(None, description="Thema / Kapitel der Stunde")
    lehrer_name: str | None = Field(None, description="Name des unterrichtenden Fahrlehrers")
    filiale: str | None = Field(None, description="Filiale / Unterrichtsort")
    dauer_minuten: float | None = Field(90.0, description="Dauer in Minuten")
    storno: bool = Field(False, description="Storniert-Flag")


class TheoriestundenResponse(BaseModel):
    """List of attended theory lessons for a student."""

    count: int = Field(..., description="Anzahl besuchter Theoriestunden")
    student_uuid: str = Field(..., description="UUID des Schülers")
    theoriestunden: list[TheoriestundeItem] = Field(default_factory=list, description="Liste der Theoriestunden")


class TheoriekapitelItem(BaseModel):
    """Theory syllabus chapter / topic."""

    id: str = Field(..., description="UUID des Theoriekapitels")
    bezeichnung: str = Field(..., description="Bezeichnung des Themas (z.B. '4 Schaltstelle Fahrer')")
    systemtheoriegruppe: str | None = Field(None, description="Theoriegruppe (z.B. Grundstoff, Klasse B)")


class TheoriekapitelListResponse(BaseModel):
    """List of available theory syllabus chapters."""

    count: int = Field(..., description="Anzahl Kapitel")
    kapitel: list[TheoriekapitelItem] = Field(default_factory=list, description="Liste der Theoriekapitel")


class TheoriestundeVorlageResponse(BaseModel):
    """Prefilled template data for scheduling a theory lesson."""

    fidKunde: str = Field(..., description="UUID des Schülers")
    kunde: str = Field(..., description="Name des Schülers")
    fidfiliale: str | None = Field(None, description="Standard-Filialen-UUID")
    filiale: str | None = Field(None, description="Name der Filiale")
    fidFahrlehrer: str | None = Field(None, description="Standard-Fahrlehrer-UUID")
    fahrlehrer: str | None = Field(None, description="Name des Fahrlehrers")
    fidSystemtheoriegruppe: str | None = Field("*", description="Systemtheoriegruppe (z.B. '*', 'B')")
    von: str | None = Field(None, description="Standard Beginnzeit (ISO)")
    bis: str | None = Field(None, description="Standard Endzeit (ISO)")
    minuten: float = Field(90.0, description="Dauer in Minuten")
    datum: str | None = Field(None, description="Datum (ISO)")


class TheoriestundeCreateRequest(BaseModel):
    """Request payload to assign/record a theory lesson for a student."""

    fidfiliale: str = Field(..., description="UUID der Filiale")
    filiale: str | None = Field(None, description="Name der Filiale")
    fidFahrlehrer: str = Field(..., description="UUID des unterrichtenden Fahrlehrers")
    fahrlehrer: str | None = Field(None, description="Name des Fahrlehrers")
    fidSystemtheoriegruppe: str = Field("*", description="Systemtheoriegruppe (z.B. '*', 'B', '96')")
    kapitel: str = Field(..., description="Bezeichnung des Themas/Kapitels (z.B. '1  Persönliche Voraussetzungen')")
    datum: str = Field(..., description="Unterrichtsdatum im ISO-Format (z.B. '2026-08-25T00:00:00')")
    von: str = Field(..., description="Startzeitpunkt im ISO-Format (z.B. '2026-08-25T18:00:00')")
    bis: str = Field(..., description="Endzeitpunkt im ISO-Format (z.B. '2026-08-25T19:30:00')")
    minuten: int = Field(90, description="Dauer in Minuten (Standard: 90)")

