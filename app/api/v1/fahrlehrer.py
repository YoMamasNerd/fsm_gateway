"""Driving instructors (Fahrlehrer) API endpoints."""

import logging
from fastapi import APIRouter, HTTPException, Query, status

from app.core.cache import cache
from app.core.client import FsmApiError, fsm_client
from app.schemas.fahrlehrer import FahrlehrerItem, FahrlehrerListResponse

logger = logging.getLogger("fsm_gateway.api.fahrlehrer")
router = APIRouter(prefix="/fahrlehrer", tags=["Fahrlehrer"])


@router.get(
    "",
    response_model=FahrlehrerListResponse,
    summary="Fahrlehrer-Liste abrufen",
    description="Liefert alle aktiven Fahrlehrer aus FSM. Die Liste wird 5 Minuten im Speicher gecached.",
)
async def list_fahrlehrer(
    only_active: bool = Query(default=True, description="Nur aktive Fahrlehrer abrufen"),
) -> FahrlehrerListResponse:
    try:
        raw_list = await fsm_client.get_fahrlehrer(only_active=only_active)
        items: list[FahrlehrerItem] = []
        for r in raw_list:
            items.append(
                FahrlehrerItem(
                    id=str(r.get("id")),
                    vorname=r.get("vorname"),
                    nachname=r.get("nachname"),
                    voller_name=r.get("voller_name", ""),
                    name=r.get("name", ""),
                    istAktiv=r.get("istAktiv", True),
                    kuerzel=r.get("kuerzel"),
                    email=r.get("email"),
                    telefon=r.get("telefon") or r.get("mobil"),
                )
            )
        return FahrlehrerListResponse(count=len(items), fahrlehrer=items)
    except FsmApiError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    except Exception as exc:
        logger.error("Fehler beim Abrufen der Fahrlehrer: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Fahrlehrer-Abruf fehlgeschlagen: {exc}",
        )


@router.post(
    "/refresh-cache",
    response_model=FahrlehrerListResponse,
    summary="Fahrlehrer-Cache invalidieren und neu laden",
    description="Löscht den internen Fahrlehrer-Cache und lädt die aktuellen Daten direkt von FSM.",
)
async def refresh_fahrlehrer_cache() -> FahrlehrerListResponse:
    try:
        await cache.delete("fsm:fahrlehrer:active:True")
        await cache.delete("fsm:fahrlehrer:active:False")
        return await list_fahrlehrer(only_active=True)
    except Exception as exc:
        logger.error("Fehler beim Cache-Refresh der Fahrlehrer: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Cache-Aktualisierung fehlgeschlagen: {exc}",
        )
