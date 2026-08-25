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
