"""Financial schemas: Driving lessons, services/fees (Leistungen) and payments."""

from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel, Field


class FahrstundeItem(BaseModel):
    """Single driving lesson item."""

    id: str = Field(..., description="Driving lesson UUID")
    datum: str | None = Field(default=None, description="Date of lesson")
    zeit: str | None = Field(default=None, description="Time of lesson")
    minuten: float = Field(default=45.0, description="Duration in minutes")
    fahrlehrer: str | None = Field(default=None, description="Instructor name")
    fahrstundenart: str | None = Field(default=None, description="Type code (e.g. UW, ÜB, SF, PF)")
    beschreibung: str | None = Field(default=None, description="Full descriptive text")
    kfz: str | None = Field(default=None, description="Vehicle ID / plate")
    bezahlt: bool = Field(default=False, description="Whether lesson is marked as paid")
    betrag: float | None = Field(default=None, description="Price / amount of lesson")
    klasse: str | None = Field(default=None, description="License class")
    raw_data: dict[str, Any] | None = Field(default=None, description="Raw FSM lesson data")


class FahrstundenResponse(BaseModel):
    """List of driving lessons for a student."""

    student_uuid: str = Field(..., description="Student UUID")
    count: int = Field(..., description="Total count of lessons")
    total_minutes: float = Field(..., description="Total minutes driven")
    fahrstunden: list[FahrstundeItem] = Field(..., description="List of lessons")


class LeistungItem(BaseModel):
    """Account fee / payment / service record (Leistungskonto)."""

    id: str = Field(..., description="Service entry UUID")
    leistungsart: str = Field(..., description="Service category (GG, LM, SG, PR, ZG, etc.)")
    datum: str | None = Field(default=None, description="Entry date")
    text: str | None = Field(default=None, description="Description text")
    kosten: float | None = Field(default=None, description="Cost amount (charged to student)")
    zahlung: float | None = Field(default=None, description="Payment amount (credit/deposit)")
    saldo: float | None = Field(default=None, description="Resulting balance after entry")
    klasse: str | None = Field(default=None, description="License category")
    zahlungsart: str | None = Field(default=None, description="Payment method if applicable")
    belegnummer: str | None = Field(default=None, description="Receipt / invoice reference")
    fahrlehrer: str | None = Field(default=None, description="Associated instructor name or ID")
    raw_data: dict[str, Any] | None = Field(default=None, description="Raw FSM record")


class LeistungenResponse(BaseModel):
    """Account statement of services and payments."""

    student_uuid: str = Field(..., description="Student UUID")
    count: int = Field(..., description="Total items")
    total_kosten: float = Field(..., description="Total charges")
    total_zahlungen: float = Field(..., description="Total payments")
    leistungen: list[LeistungItem] = Field(..., description="Service items")


class ZahlungCreateRequest(BaseModel):
    """Payload to book a payment into FSM for a student."""

    betrag: float = Field(..., gt=0, description="Payment amount in EUR")
    datum: dt.date | dt.datetime | str | None = Field(
        default=None,
        description="Booking date (defaults to current date if omitted)",
    )
    zahlungsart: str = Field(
        default="Kartenzahlung",
        description="Payment method (e.g. Kartenzahlung, SumUp, Bar, Überweisung)",
    )
    text: str = Field(
        default="SumUp Kartenzahlung",
        description="Booking reference / description note",
    )
    belegnummer: str | None = Field(
        default=None,
        description="Optional transaction ID / receipt number",
    )


class ZahlungResponse(BaseModel):
    """Payment booking confirmation."""

    success: bool = Field(default=True, description="Payment status")
    student_uuid: str = Field(..., description="Student UUID")
    betrag: float = Field(..., description="Booked amount")
    zahlungsart: str = Field(..., description="Payment method")
    belegnummer: str | None = Field(default=None, description="Transaction ID")
    message: str = Field(..., description="Status summary")
