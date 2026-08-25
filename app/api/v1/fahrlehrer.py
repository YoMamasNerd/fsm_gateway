"""Driving instructors (Fahrlehrer) API endpoints."""

import logging
from fastapi import APIRouter, HTTPException, Query, Request, Response, status

from app.core.cache import cache
from app.core.client import FsmApiError, fsm_client
from app.core.config import settings
from app.schemas.fahrlehrer import FahrlehrerItem, FahrlehrerListResponse

logger = logging.getLogger("fsm_gateway.api.fahrlehrer")
router = APIRouter(prefix="/fahrlehrer", tags=["Fahrlehrer"])


@router.get(
    "",
    response_model=FahrlehrerListResponse,
    summary="Fahrlehrer-Liste abrufen",
    description="Liefert alle aktiven Fahrlehrer aus FSM. Die Liste wird im Speicher gecached.",
)
async def list_fahrlehrer(
    request: Request,
    response: Response,
    only_active: bool = Query(default=True, description="Nur aktive Fahrlehrer abrufen"),
    refresh: bool = Query(default=False, description="Cache-Eintrag überspringen und frisch von FSM abrufen"),
) -> FahrlehrerListResponse:
    force_refresh = refresh or request.headers.get("x-refresh-cache") == "1"
    cache_key = f"endpoint:fahrlehrer:active:{only_active}"

    if not force_refresh:
        cached_res = await cache.get(cache_key)
        if cached_res is not None:
            response.headers["X-Cache-Hit"] = "1"
            return cached_res

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
        result = FahrlehrerListResponse(count=len(items), fahrlehrer=items)
        await cache.set(cache_key, result, ttl=settings.FAHRLEHRER_CACHE_TTL_SECONDS)
        return result
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
async def refresh_fahrlehrer_cache(request: Request, response: Response) -> FahrlehrerListResponse:
    try:
        await cache.delete("endpoint:fahrlehrer:active:True")
        await cache.delete("endpoint:fahrlehrer:active:False")
        await cache.delete("fsm:fahrlehrer:True")
        await cache.delete("fsm:fahrlehrer:False")
        await cache.delete("fsm:fahrlehrer:active:True")
        await cache.delete("fsm:fahrlehrer:active:False")
        return await list_fahrlehrer(request=request, response=response, only_active=True, refresh=True)
    except Exception as exc:
        logger.error("Fehler beim Cache-Refresh der Fahrlehrer: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Cache-Aktualisierung fehlgeschlagen: {exc}",
        )


@router.post(
    "/cache/clear",
    summary="Gesamten Gateway-Cache leeren",
    description="Leert alle im Speicher gecachten Fahrlehrer-, Kalender-, Schüler- und Leistungsdaten.",
)
async def clear_all_cache() -> dict[str, bool | str]:
    await cache.clear()
    return {"success": True, "message": "Gesamter Gateway-Cache wurde erfolgreich geleert."}

