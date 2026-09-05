"""Schemas for FSM course-level theory schedule entries (Theorietermine).

Distinct from apps/schemas/theorie.py's Theoriestunde: a Theorietermin belongs
to a *course* (fidKurs) and represents the shared day/topic schedule, while a
Theoriestunde belongs to a *student* (fidKunde) and represents one student's
attendance record.
"""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field


class TheorieterminCreateItem(BaseModel):
    """One theory-schedule slot to create for a course."""

    von: dt.datetime = Field(..., description="Start (ISO-8601)")
    bis: dt.datetime = Field(..., description="Ende (ISO-8601)")
    kapitel: str = Field(..., description="Thema/Kapitel-Bezeichnung")
    fahrlehrer_id: str = Field(..., description="FSM Fahrlehrer-UUID")
    filiale_id: str | None = Field(
        default=None, description="Filialen-UUID; fällt auf FSM_DEFAULT_FILIALE_ID zurück"
    )
    systemtheoriegruppe: str = Field(
        default="*", description="Theoriegruppen-Code ('*' für Grundstoff, 'B', 'A', ...)"
    )


class TheorieterminBulkCreateRequest(BaseModel):
    """Payload to create the full day/topic schedule for a course in one call."""

    termine: list[TheorieterminCreateItem] = Field(..., min_length=1)


class TheorieterminItem(BaseModel):
    """One scheduled theory slot for a course."""

    id: str = Field(..., description="FSM Theorietermin-UUID")
    kurs_id: str | None = Field(default=None)
    von: dt.datetime | None = Field(default=None)
    bis: dt.datetime | None = Field(default=None)
    kapitel: str | None = Field(default=None)
    fahrlehrer_ids: list[str] = Field(default_factory=list)
    systemtheoriegruppe: str | None = Field(default=None)


class TheorieterminListResponse(BaseModel):
    """The scheduled day/topic plan of a course."""

    kurs_id: str = Field(..., description="FSM Kurs-UUID")
    count: int = Field(..., description="Anzahl geplanter Termine")
    termine: list[TheorieterminItem] = Field(default_factory=list)


class TheorieterminBulkCreateResponse(BaseModel):
    """Confirmation of the created schedule slots."""

    success: bool = Field(default=True)
    kurs_id: str = Field(..., description="FSM Kurs-UUID")
    created_count: int = Field(default=0)
    termine: list[TheorieterminItem] = Field(default_factory=list)


class TheorieterminUpdateRequest(BaseModel):
    """Partial update for one theory-schedule slot (only sent fields change)."""

    von: dt.datetime | None = Field(default=None)
    bis: dt.datetime | None = Field(default=None)
    kapitel: str | None = Field(default=None)
    fahrlehrer_id: str | None = Field(default=None)


class TheorieterminActionResponse(BaseModel):
    """Generic confirmation for update/delete."""

    success: bool = Field(default=True)
    termin_id: str | None = Field(default=None)
    deleted_id: str | None = Field(default=None)
