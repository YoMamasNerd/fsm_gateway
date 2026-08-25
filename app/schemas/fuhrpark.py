"""Pydantic schemas for fleet and vehicles (Fuhrpark & Fahrzeuge)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class FahrzeugItem(BaseModel):
    """Driving school vehicle."""

    id: str = Field(..., description="UUID des Fahrzeugs")
    bezeichnung: str = Field(..., description="Bezeichnung / Modell (z.B. 'Cupra Leon')")
    kennung: str | None = Field(None, description="Interne Kennung / Wagennummer (z.B. '019')")
    kennzeichen: str | None = Field(None, description="Amtliches Kennzeichen (z.B. 'B SW7187')")
    automatik: bool = Field(False, description="Automatikgetriebe (True) oder Schaltwagen (False)")
    simulator: bool = Field(False, description="Fahrsimulator-Flag")
    aktiv: bool = Field(True, description="Fahrzeug aktiv")
    klassen: str | None = Field(None, description="Führerscheinklassen (z.B. 'B')")
    fidFahrlehrer: list[str] = Field(default_factory=list, description="Zugewiesene Fahrlehrer-UUIDs")


class FahrzeugListResponse(BaseModel):
    """List of fleet vehicles."""

    count: int = Field(..., description="Anzahl Fahrzeuge")
    fahrzeuge: list[FahrzeugItem] = Field(default_factory=list, description="Liste der Fahrzeuge")
