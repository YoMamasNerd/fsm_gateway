"""Instructor (Fahrlehrer) schemas."""

from pydantic import BaseModel, Field


class FahrlehrerItem(BaseModel):
    """Normalized driving instructor object."""

    id: str = Field(..., description="FSM UUID of the instructor")
    vorname: str | None = Field(default="", description="First name")
    nachname: str | None = Field(default="", description="Last name")
    voller_name: str = Field(..., description="Full combined display name")
    name: str = Field(..., description="Standardized name")
    istAktiv: bool | None = Field(default=True, description="Active status")
    kuerzel: str | None = Field(default=None, description="Instructor abbreviation")
    email: str | None = Field(default=None, description="Email address")
    telefon: str | None = Field(default=None, description="Phone number")


class FahrlehrerListResponse(BaseModel):
    """List of instructors response."""

    count: int = Field(..., description="Number of instructors")
    fahrlehrer: list[FahrlehrerItem] = Field(..., description="List of instructors")
