"""Pydantic schemas for price lists and price positions (Preislisten & Gebührenkatalog)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PreislisteItem(BaseModel):
    """Driving school price list."""

    id: str = Field(..., description="UUID der Preisliste")
    bezeichnung: str = Field(..., description="Name / Gültigkeit (z.B. '04/2026', '01/2020')")
    kennung: str | None = Field(None, description="Kennung der Preisliste")
    schuelerpreisliste: bool = Field(False, description="Individuelle Schülerpreisliste")
    ausblenden: bool = Field(False, description="Ausgeblendet / Archiviert")


class PreislistenResponse(BaseModel):
    """List of all price lists."""

    count: int = Field(..., description="Anzahl Preislisten")
    preislisten: list[PreislisteItem] = Field(default_factory=list, description="Liste der Preislisten")


class PreispositionItem(BaseModel):
    """Individual fee item / service position in a price list."""

    id: str = Field(..., description="UUID der Preisposition")
    fidPreisliste: str | None = Field(None, description="UUID der zugehörigen Preisliste")
    bezeichnung: str = Field(..., description="Bezeichnung der Gebühr (z.B. 'Übungsstunde Klasse B')")
    betrag: float = Field(0.0, description="Nettobetrag / Bruttopreis in Euro")
    klasse: str | None = Field(None, description="Führerscheinklasse (z.B. 'B', 'A2', 'BE')")
    theorie: bool = Field(False, description="Theorie-Leistung")
    praxis: bool = Field(False, description="Praxis-Leistung")
    fidleistungsart: str | None = Field(None, description="UUID der Leistungsart")
    artikel: str | None = Field(None, description="Artikelnummer / Kürzel")


class PreispositionenResponse(BaseModel):
    """List of fee items in a price list."""

    count: int = Field(..., description="Anzahl Positionen")
    preisliste_id: str = Field(..., description="UUID der Preisliste")
    preispositionen: list[PreispositionItem] = Field(default_factory=list, description="Liste der Positionen")
