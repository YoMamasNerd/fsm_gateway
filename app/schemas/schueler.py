"""Student (Schüler) schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SchuelerSucheRequest(BaseModel):
    """Payload for student search."""

    query: str | None = Field(default=None, description="General search term (matches first name, surname, card number)")
    vorname: str | None = Field(default=None, description="Filter specifically by first name")
    nachname: str | None = Field(default=None, description="Filter specifically by surname / last name")
    karteiNr: str | None = Field(default=None, description="Filter specifically by card number")
    only_active: bool = Field(default=True, description="Filter only active students")
    count: int = Field(default=5000, description="Max results to fetch")
    index: int = Field(default=0, description="Pagination index")


class SchuelerKurzItem(BaseModel):
    """Student search result item."""

    id: str = Field(..., description="FSM Student UUID")
    vorname: str = Field(default="", description="First name")
    nachname: str = Field(default="", description="Last name")
    voller_name: str = Field(default="", description="Full display name")
    karteiNr: str | int | None = Field(default=None, description="Card/Student number")
    klassen: Any | None = Field(default=None, description="Driver license categories (e.g. B, BE, A)")
    saldo: float | None = Field(default=None, description="Current account balance / saldo")
    gesperrt: bool | None = Field(default=False, description="Blocked status")
    raw_data: dict[str, Any] | None = Field(default=None, description="Raw FSM record if needed")


class SchuelerSucheResponse(BaseModel):
    """Student search results response."""

    count: int = Field(..., description="Number of results found")
    schueler: list[SchuelerKurzItem] = Field(..., description="List of matching students")


class SchuelerDetails(BaseModel):
    """Detailed student master record and card (Kartei)."""

    id: str = Field(..., description="Student UUID")
    vorname: str | None = Field(default="", description="First name")
    nachname: str | None = Field(default="", description="Last name")
    voller_name: str = Field(..., description="Full display name")
    anrede: str | None = Field(default=None, description="Salutation (Herr/Frau)")
    titel: str | None = Field(default=None, description="Title (e.g. Dr.)")
    geburtsdatum: str | None = Field(default=None, description="Date of birth")
    geburtsort: str | None = Field(default=None, description="Place of birth")
    strasse: str | None = Field(default=None, description="Street address")
    plz: str | None = Field(default=None, description="Postal code")
    ort: str | None = Field(default=None, description="City")
    telefon: str | None = Field(default=None, description="Phone number")
    handy: str | None = Field(default=None, description="Mobile number")
    email: str | None = Field(default=None, description="Email address")
    karteiNr: str | int | None = Field(default=None, description="Card number")
    saldo: float | None = Field(default=None, description="Current account balance")
    klassen: Any | None = Field(default=None, description="Driving license classes")
    gesperrt: bool | None = Field(default=False, description="Whether student is locked")
    raw_data: dict[str, Any] = Field(default_factory=dict, description="Complete raw FSM dictionary")


class UpdateStudentKlasseRequest(BaseModel):
    """Payload to update student's driving license class / addenda in FSM."""

    b197: bool = Field(default=True, description="Whether B197 (Schlüsselzahl 197) is active")
    klasse: str | None = Field(default=None, description="Optional target class string (e.g. 'B197' or 'B')")


class UpdateStudentKlasseResponse(BaseModel):
    """Response after updating student's license class."""

    success: bool = Field(..., description="Whether update succeeded")
    student_uuid: str = Field(..., description="Student UUID")
    klasse: str = Field(..., description="Updated class string in FSM (e.g. 'B(197)')")
    b197: bool = Field(..., description="Whether B197 is active")
    message: str = Field(..., description="Status message")


class UpdateStudentFahrlehrerRequest(BaseModel):
    """Payload to update student's assigned driving instructors (Fahrlehrer 1 & 2) in FSM."""

    fidFahrlehrer1: str | None = Field(default=None, description="UUID des Hauptfahrlehrers (Fahrlehrer 1)")
    fahrlehrer1: str | None = Field(default=None, description="Name des Hauptfahrlehrers")
    fidFahrlehrer2: str | None = Field(default=None, description="UUID des Zweitfahrlehrers (Fahrlehrer 2)")
    fahrlehrer2: str | None = Field(default=None, description="Name des Zweitfahrlehrers")


class UpdateStudentFahrlehrerResponse(BaseModel):
    """Response after updating student's assigned driving instructors."""

    success: bool = Field(..., description="Whether update succeeded")
    student_uuid: str = Field(..., description="Student UUID")
    fidFahrlehrer1: str | None = Field(default=None, description="UUID Fahrlehrer 1")
    fahrlehrer1: str | None = Field(default=None, description="Name Fahrlehrer 1")
    fidFahrlehrer2: str | None = Field(default=None, description="UUID Fahrlehrer 2")
    fahrlehrer2: str | None = Field(default=None, description="Name Fahrlehrer 2")
    message: str = Field(..., description="Status message")
