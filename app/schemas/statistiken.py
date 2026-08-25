"""Pydantic schemas for exams and instructor performance statistics (Prüfungsstatistiken)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PruefungsstatistikItem(BaseModel):
    """Exam statistics per instructor or license class."""

    name: str = Field(..., description="Name des Fahrlehrers oder der Klasse")
    anmeldungen: int = Field(0, description="Anzahl Praxis-Prüfungsanmeldungen")
    bestanden: int = Field(0, description="Anzahl bestandener Prüfungen")
    durchgefallen: int = Field(0, description="Anzahl nicht bestandener Prüfungen")
    erfolgsquote_pct: float = Field(0.0, description="Erfolgsquote in Prozent (z.B. 75.0)")


class PruefungsstatistikResponse(BaseModel):
    """Aggregated driving exam statistics."""

    jahr: int = Field(..., description="Auswertungsjahr")
    zeitraum: int = Field(..., description="Zeitraum-Typ (z.B. 1=Jahr, 3=Gesamt)")
    count: int = Field(..., description="Anzahl Datensätze")
    statistiken: list[PruefungsstatistikItem] = Field(default_factory=list, description="Statistiken pro Lehrer/Klasse")
