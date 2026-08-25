"""Pydantic schemas for master data (Filialen, Klassen, Leistungsarten, Treffpunkte)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class FilialeItem(BaseModel):
    """Driving school branch / location."""

    id: str = Field(..., description="UUID der Filiale")
    name: str = Field(..., description="Name / Bezeichnung der Filiale")
    kennung: str | None = Field(None, description="Kurzkennung (z.B. 'HS')")
    strasse: str | None = Field(None, description="Straße und Hausnummer")
    plz: str | None = Field(None, description="Postleitzahl")
    ort: str | None = Field(None, description="Ort / Stadt")
    telefon: str | None = Field(None, description="Telefonnummer")


class FilialenListResponse(BaseModel):
    """List of driving school branches."""

    count: int = Field(..., description="Anzahl Filialen")
    filialen: list[FilialeItem] = Field(default_factory=list, description="Liste der Filialen")


class KlasseItem(BaseModel):
    """Driving license category (Klasse)."""

    id: str = Field(..., description="UUID der Führerscheinklasse")
    bezeichnung: str = Field(..., description="Bezeichnung der Klasse (z.B. 'B', 'B197', 'A')")
    kuerzel: str | None = Field(None, description="Kürzel der Klasse")
    fahrzeugart: str | None = Field(None, description="Fahrzeugart")


class KlassenListResponse(BaseModel):
    """List of driving license classes."""

    count: int = Field(..., description="Anzahl Klassen")
    klassen: list[KlasseItem] = Field(default_factory=list, description="Liste der Klassen")


class LeistungsartItem(BaseModel):
    """Service catalog item (Leistungsart)."""

    id: str = Field(..., description="UUID der Leistungsart")
    bezeichnung: str = Field(..., description="Bezeichnung der Leistung (z.B. 'Übungsstunde')")
    kuerzel: str | None = Field(None, description="Kürzel")
    preis: float | None = Field(None, description="Standardpreis in Euro")
    dauer_minuten: float | None = Field(None, description="Standarddauer in Minuten")


class LeistungsartenListResponse(BaseModel):
    """List of available service catalog items."""

    count: int = Field(..., description="Anzahl Leistungsarten")
    leistungsarten: list[LeistungsartItem] = Field(default_factory=list, description="Liste der Leistungsarten")


class TreffpunktItem(BaseModel):
    """Pre-configured meeting point for driving lessons."""

    id: str = Field(..., description="UUID des Treffpunkts")
    treffpunkt: str = Field(..., description="Name / Bezeichnung des Treffpunkts")
    strasse: str | None = Field(None, description="Straße")
    plz: str | None = Field(None, description="PLZ")
    ort: str | None = Field(None, description="Ort")


class TreffpunkteListResponse(BaseModel):
    """List of meeting points."""

    count: int = Field(..., description="Anzahl Treffpunkte")
    treffpunkte: list[TreffpunktItem] = Field(default_factory=list, description="Liste der Treffpunkte")
