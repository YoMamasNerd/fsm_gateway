"""Pydantic schemas for cash books and cash transactions (Kassenbuch & Buchungen)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class KassenbuchItem(BaseModel):
    """Cash book entity."""

    id: str = Field(..., description="UUID des Kassenbuchs")
    bezeichnung: str = Field(..., description="Name / Bezeichnung des Kassenbuchs")
    lehrer_name: str | None = Field(None, description="Zugeordneter Fahrlehrer (falls Fahrlehrerkasse)")
    fidLehrer: str | None = Field(None, description="UUID des Fahrlehrers")
    aktiv: bool = Field(True, description="Kassenbuch aktiv")


class KassenbuecherListResponse(BaseModel):
    """List of cash books."""

    count: int = Field(..., description="Anzahl Kassenbücher")
    kassenbuecher: list[KassenbuchItem] = Field(default_factory=list, description="Liste der Kassenbücher")


class KassenbuchungItem(BaseModel):
    """Individual transaction in a cash book."""

    id: str = Field(..., description="UUID der Buchung")
    datum: str | None = Field(None, description="Belegdatum (ISO)")
    text: str | None = Field(None, description="Buchungstext / Verwendungszweck")
    einnahme: float = Field(0.0, description="Einnahmebetrag in Euro")
    ausgabe: float = Field(0.0, description="Ausgabebetrag in Euro")
    saldo: float = Field(0.0, description="Kassenbestand nach Buchung in Euro")
    belegnummer: str | None = Field(None, description="Belegnummer")


class KassenbuchungenResponse(BaseModel):
    """List of transactions in a cash book."""

    kassenbuch_id: str = Field(..., description="UUID des Kassenbuchs")
    jahr: int = Field(..., description="Jahr")
    monat: int | None = Field(None, description="Monat")
    count: int = Field(..., description="Anzahl Buchungen")
    buchungen: list[KassenbuchungItem] = Field(default_factory=list, description="Liste der Buchungen")
