"""Calendar and appointment schemas."""

from __future__ import annotations

import datetime as dt
from pydantic import BaseModel, Field


class TerminBase(BaseModel):
    """Base fields for an appointment or calendar event."""

    fahrlehrer_id: str = Field(..., description="FSM UUID of the instructor")
    von: dt.datetime = Field(..., description="Start time (ISO-8601)")
    bis: dt.datetime = Field(..., description="End time (ISO-8601)")
    titel: str = Field(..., description="Title / notes of appointment")
    terminart: str = Field(default="PX", description="Type code (e.g. PX, ST, PP, FS, TH)")
    leistungsart_id: str | None = Field(default=None, description="Optional Leistungsart UUID")
    schueler_id: str | None = Field(default=None, description="Optional Student UUID")
    fahrzeug_id: str | None = Field(default=None, description="Optional Vehicle UUID")
    gebucht: bool = Field(default=False, description="Booking flag")


class TerminCreateRequest(TerminBase):
    """Payload to create a new appointment or blocker."""


class TerminUpdateRequest(TerminBase):
    """Payload to update an existing appointment."""


class TerminCreateResponse(BaseModel):
    """Response returned when appointment(s) are created."""

    success: bool = Field(default=True, description="Success status")
    created_ids: list[str] = Field(..., description="List of generated appointment UUIDs")
    count: int = Field(..., description="Number of created blocks")


class KalenderEvent(BaseModel):
    """Normalized calendar event representation."""

    id: str = Field(..., description="Appointment UUID")
    von: dt.datetime = Field(..., description="Start time")
    bis: dt.datetime = Field(..., description="End time")
    fahrlehrer_id: str = Field(..., description="Instructor UUID")
    terminart: str = Field(..., description="Terminart (e.g. FS, PT, TH, ST, PP, PX)")
    titel: str = Field(..., description="Title / description text")
    schueler_name: str | None = Field(default=None, description="Name of student if applicable")
    schueler_id: str | None = Field(default=None, description="UUID of student if applicable")
    fahrzeug_id: str | None = Field(default=None, description="UUID of vehicle if applicable")
    gebucht: bool = Field(default=False, description="Whether booked")
    ist_fahrstunde: bool = Field(default=False, description="Flag if event is a driving lesson")
    ist_theorie: bool = Field(default=False, description="Flag if event is theory class")
    ist_blocker: bool = Field(default=False, description="Flag if event is an internal/vacation blocker")
    dauer_minuten: float = Field(..., description="Duration in minutes")


class KalenderResponse(BaseModel):
    """Calendar response for an instructor."""

    fahrlehrer_id: str = Field(..., description="Instructor UUID")
    start: str = Field(..., description="Query start")
    end: str = Field(..., description="Query end")
    count: int = Field(..., description="Total event count")
    events: list[KalenderEvent] = Field(..., description="Normalized events list")


class TerminActionResponse(BaseModel):
    """Generic confirmation for appointment modification or deletion."""

    success: bool = Field(default=True, description="Action success flag")
    termin_id: str | None = Field(default=None, description="Updated or affected appointment ID")
    deleted_id: str | None = Field(default=None, description="Deleted appointment ID")
    message: str | None = Field(default=None, description="Informational message")
