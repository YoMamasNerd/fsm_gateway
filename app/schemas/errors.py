"""Schemas for error reporting and explanations."""

from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field


class ErrorEntry(BaseModel):
    id: int | None = Field(default=None, description="Eindeutige ID des Fehlers")
    timestamp: str = Field(description="ISO-8601 Zeitstempel")
    time: str = Field(description="Uhrzeit (HH:MM:SS)")
    date: str = Field(description="Datum (DD.MM.YYYY)")
    method: str = Field(description="HTTP-Methode (GET, POST, etc.)")
    path: str = Field(description="Aufgerufener Pfad")
    status_code: int = Field(description="HTTP-Statuscode")
    error_type: str = Field(description="Fehlertyp (z.B. FsmApiError, HTTPException)")
    message: str = Field(description="Fehlermeldung")
    begruendung: str = Field(description="Begründung / Ursache des Fehlers")
    details: Any = Field(default=None, description="Zusätzliche Details oder FSM-Antwort")
    client_ip: str | None = Field(default=None, description="IP-Adresse des Aufrufers")


class ErrorsResponse(BaseModel):
    has_errors: bool = Field(description="True wenn Fehler vorliegen")
    count: int = Field(description="Anzahl der zurückgegebenen Fehler")
    message: str = Field(description="Zusammenfassung oder Statusmeldung")
    last_error: ErrorEntry | None = Field(default=None, description="Letzter aufgetretener Fehler")
    errors: list[ErrorEntry] = Field(default_factory=list, description="Liste der Fehler")


class LastErrorResponse(BaseModel):
    has_error: bool = Field(description="True wenn mindestens ein Fehler vorliegt")
    message: str = Field(description="Statusmeldung oder Begründung")
    error: ErrorEntry | None = Field(default=None, description="Letzter Fehler")


class ClearErrorsResponse(BaseModel):
    deleted_count: int = Field(description="Anzahl gelöschter Fehlereinträge")
    message: str = Field(description="Bestätigungsmeldung")
