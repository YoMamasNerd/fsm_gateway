"""Kontrolle (Leistungen je Fahrlehrer/Tag) API schemas."""

from pydantic import BaseModel, ConfigDict


class KontrolleLeistung(BaseModel):
    """Einzelne verbuchte Leistung aus der FSM-Kontrolle-Ansicht."""

    model_config = ConfigDict(extra="allow")

    id: str = ""
    datum: str | None = None
    fidKunde: str | None = None
    leistungsart: str | None = None
    displayname: str | None = None
    vorname: str | None = None
    nachname: str | None = None
    minuten: float | None = None
    text: str | None = None
    kosten: float | None = None
    kunde: str | None = None
    geloescht: bool = False
    theoriestunde: dict | None = None
    fahrstunde: dict | None = None
    sonstigeTaetigkeit: dict | None = None


class ArbeitszeitSummary(BaseModel):
    """FSMs eigene Tages-Arbeitszeit-Summe eines Fahrlehrers."""

    model_config = ConfigDict(extra="allow")

    lehrer: str | None = None
    praxis: float = 0
    sonstiges: float = 0
    total: float = 0


class KontrolleResponse(BaseModel):
    """Kontrolle-Ansicht eines Fahrlehrers für einen Tag."""

    fahrlehrer_id: str
    datum: str
    leistungen: list[KontrolleLeistung] = []
    count: int = 0
    minuten_praktisch: float = 0
    minuten_sonstige: float = 0
    minuten_total: float = 0
    arbeitszeit: ArbeitszeitSummary | None = None
