"""Pydantic schemas for student training status (Ausbildung) and digital index card (Karteikarte)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AusbildungItem(BaseModel):
    """Training progress and special drive counters for a specific driving license class."""

    id: str = Field(..., description="ID der Ausbildung / Schueler-Klasse")
    fidklasse: str | None = Field(None, description="UUID der Führerscheinklasse")
    klasse_name: str | None = Field(None, description="Name der Klasse (z.B. B, B197, A2)")
    fidschueler: str | None = Field(None, description="UUID des Schülers")
    lfdnr: int | None = Field(1, description="Laufende Nummer der Ausbildung")

    # Fahrstunden-Zähler
    uebungsfahrten: float = Field(0.0, description="Anzahl normaler Übungsfahrten")
    ueberlandfahrten: float = Field(0.0, description="Anzahl Überlandfahrten (Sonderfahrt)")
    autobahnfahrten: float = Field(0.0, description="Anzahl Autobahnfahrten (Sonderfahrt)")
    nachtfahrten: float = Field(0.0, description="Anzahl Nacht-/Dämmerungsfahrten (Sonderfahrt)")
    unterweisungen: float = Field(0.0, description="Anzahl Sicherheitsunterweisungen / Abfahrkontrollen")
    sonstige_stunden: float = Field(0.0, description="Sonstige Fahrstunden")
    gesamt_fahrstunden: float = Field(0.0, description="Gesamtzahl aller Fahrstunden")

    # Theorie-Zähler
    theoriestunden: float = Field(0.0, description="Absolvierte Theoriestunden")
    pflicht_theoriestunden: float | None = Field(None, description="Vorgeschriebene Pflicht-Theoriestunden")

    # Prüfungsdaten
    theoriepruefungen: int | None = Field(None, description="Anzahl Theorieprüfungen")
    praxispruefungen: int | None = Field(None, description="Anzahl Praxisprüfungen")
    datum_theoriepruefung: str | None = Field(None, description="Datum der letzten Theorieprüfung (ISO)")
    datum_praxispruefung: str | None = Field(None, description="Datum der letzten Praxisprüfung (ISO)")
    fidergebnis_theorie: str | None = Field(None, description="Ergebnis Theorieprüfung")
    fidergebnis_praxis: str | None = Field(None, description="Ergebnis Praxisprüfung")
    bestanden_theorie: bool = Field(False, description="Theorieprüfung bestanden")
    bestanden_praxis: bool = Field(False, description="Praxisprüfung bestanden")


class AusbildungListResponse(BaseModel):
    """Response containing all training classes and counters for a student."""

    count: int = Field(..., description="Anzahl der Ausbildungsklassen")
    student_uuid: str = Field(..., description="UUID des Schülers")
    ausbildungen: list[AusbildungItem] = Field(default_factory=list, description="Liste der Ausbildungen")


class KarteikarteResponse(BaseModel):
    """Digital index card (Karteikarte) with assigned instructors and TÜV/DEKRA order status."""

    student_uuid: str = Field(..., description="UUID des Schülers")
    fidFahrlehrer1: str | None = Field(None, description="UUID des Hauptfahrlehrer")
    fahrlehrer1: str | None = Field(None, description="Name des Hauptfahrlehrers")
    fidFahrlehrer2: str | None = Field(None, description="UUID des Zweitfahrlehrers")
    fahrlehrer2: str | None = Field(None, description="Name des Zweitfahrlehrers")
    pflichttheoriestunden: int = Field(0, description="Pflichtstunden Theorie")
    theoriestunden: float = Field(0.0, description="Absolvierte Theoriestunden")
    ruecklauf_datum: str | None = Field(None, description="Prüfauftrag-Rücklaufdatum vom TÜV/DEKRA")
    ruecklaufnummer: str | None = Field(None, description="Prüfauftragsnummer / Aktenzeichen")
    pruefungssprache: str | None = Field("DEU", description="Prüfungssprache")
    ausbildungen: list[AusbildungItem] = Field(default_factory=list, description="Verknüpfte Ausbildungsklassen")
