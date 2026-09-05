"""Error log and explanation endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query

from app.core.metrics import metrics_collector
from app.schemas.errors import (
    ClearErrorsResponse,
    ErrorEntry,
    ErrorsResponse,
    LastErrorResponse,
)

logger = logging.getLogger("fsm_gateway.api.errors")

router = APIRouter(prefix="/errors", tags=["Fehlerprotokolle & Begründungen"])
alias_router = APIRouter(prefix="/fehler", tags=["Fehlerprotokolle & Begründungen"])


@router.get(
    "",
    response_model=ErrorsResponse,
    summary="Fehlerprotokoll mit Begründungen abrufen",
    description="Liefert die zuletzt aufgetretenen Gateway- und Upstream-FSM-Fehler inklusive detaillierter Begründung und FSM-Payloads.",
)
@alias_router.get(
    "",
    response_model=ErrorsResponse,
    summary="Fehlerprotokoll mit Begründungen abrufen (Alias)",
    include_in_schema=False,
)
async def get_errors(
    limit: int = Query(50, ge=1, le=500, description="Maximale Anzahl zurückzugebender Fehler"),
    status_code: int | None = Query(None, description="Nach HTTP-Statuscode filtern (z.B. 400, 404, 500)"),
    since_minutes: int | None = Query(None, description="Nur Fehler der letzten X Minuten"),
    path: str | None = Query(None, description="Nach Pfad filtern (Teilstring)"),
) -> ErrorsResponse:
    """Holt die protokollierten Fehler inklusive Begründung und Kontext ab."""
    raw_errors = metrics_collector.get_recent_errors(
        limit=limit,
        status_code=status_code,
        since_minutes=since_minutes,
        path=path,
    )

    error_entries = [ErrorEntry(**err) for err in raw_errors]
    last_err = error_entries[0] if error_entries else None
    has_errors = len(error_entries) > 0

    if has_errors and last_err:
        msg = f"{len(error_entries)} Fehler protokolliert. Letzter: [{last_err.status_code}] {last_err.begruendung}"
    else:
        msg = "Keine Fehler aufgetreten."

    return ErrorsResponse(
        has_errors=has_errors,
        count=len(error_entries),
        message=msg,
        last_error=last_err,
        errors=error_entries,
    )


@router.get(
    "/last",
    response_model=LastErrorResponse,
    summary="Letzten aufgetretenen Fehler mit Begründung abrufen",
    description="Liefert den allerletzten Fehler oder Entwarnung falls kein Fehler protokolliert ist.",
)
@alias_router.get(
    "/last",
    response_model=LastErrorResponse,
    summary="Letzten Fehler abrufen (Alias)",
    include_in_schema=False,
)
@alias_router.get(
    "/letzter",
    response_model=LastErrorResponse,
    summary="Letzten Fehler abrufen (Deutscher Alias)",
    include_in_schema=False,
)
async def get_last_error() -> LastErrorResponse:
    """Liefert schnell den letzten aufgetretenen Fehler."""
    raw_errors = metrics_collector.get_recent_errors(limit=1)
    if not raw_errors:
        return LastErrorResponse(
            has_error=False,
            message="Kein Fehler protokolliert. Das System läuft fehlerfrei.",
            error=None,
        )

    last_err = ErrorEntry(**raw_errors[0])
    return LastErrorResponse(
        has_error=True,
        message=f"[{last_err.status_code}] {last_err.begruendung}",
        error=last_err,
    )


@router.delete(
    "",
    response_model=ClearErrorsResponse,
    summary="Fehlerprotokoll zurücksetzen",
    description="Löscht alle gespeicherten Fehlerprotokolle aus Speicher und Datenbank.",
)
@alias_router.delete(
    "",
    response_model=ClearErrorsResponse,
    summary="Fehlerprotokoll zurücksetzen (Alias)",
    include_in_schema=False,
)
async def clear_errors() -> ClearErrorsResponse:
    """Löscht alle Fehlerprotokolle."""
    deleted = metrics_collector.clear_errors()
    return ClearErrorsResponse(
        deleted_count=deleted,
        message=f"{deleted} Fehlerprotokolle wurden gelöscht.",
    )
