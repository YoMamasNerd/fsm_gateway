"""Fleet and vehicle API endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Request, Response, status

from app.core.cache import cache
from app.core.client import FsmException, fsm_client
from app.core.config import settings
from app.schemas.fuhrpark import FahrzeugItem, FahrzeugListResponse

logger = logging.getLogger("fsm_gateway.api.fuhrpark")
router = APIRouter(prefix="/fuhrpark", tags=["Fuhrpark & Fahrzeuge"])


@router.get(
    "",
    response_model=FahrzeugListResponse,
    summary="Fuhrpark / Fahrzeuge abrufen",
    description="Liefert alle Fahrzeuge der Fahrschule inkl. Kennzeichen, Schaltung/Automatik und zugewiesenen Fahrlehrern.",
)
async def list_fahrzeuge(
    request: Request,
    response: Response,
    only_active: bool = Query(default=True, description="Nur aktive Fahrzeuge abrufen"),
    refresh: bool = Query(default=False, description="Cache-Eintrag überspringen und frisch von FSM abrufen"),
) -> FahrzeugListResponse:
    force_refresh = refresh or request.headers.get("x-refresh-cache") == "1"
    cache_key = f"fuhrpark:fahrzeuge:active:{only_active}"

    if not force_refresh:
        cached_res = await cache.get(cache_key)
        if cached_res is not None:
            response.headers["X-Cache-Hit"] = "1"
            return cached_res

    try:
        raw_list = await fsm_client.get_fahrzeuge(only_active=only_active, fresh=force_refresh)
        items: list[FahrzeugItem] = []
        for r in raw_list:
            if not isinstance(r, dict):
                continue
            fid = str(r.get("id") or "")
            if not fid:
                continue
            items.append(
                FahrzeugItem(
                    id=fid,
                    bezeichnung=r.get("bezeichnung") or r.get("name") or "Unbekanntes Fahrzeug",
                    kennung=r.get("kennung"),
                    kennzeichen=r.get("kennzeichen"),
                    automatik=bool(r.get("automatik", False)),
                    simulator=bool(r.get("simulator", False)),
                    aktiv=bool(r.get("aktiv", True)),
                    klassen=r.get("klassen"),
                    fidFahrlehrer=[str(x) for x in r.get("fidFahrlehrer", []) if x],
                )
            )

        result = FahrzeugListResponse(count=len(items), fahrzeuge=items)
        await cache.set(cache_key, result, ttl=settings.FUHRPARK_CACHE_TTL_SECONDS)
        response.headers["X-Cache-Hit"] = "0"
        return result

    except (FsmException, HTTPException):
        raise
    except Exception as exc:
        logger.error("Fehler beim Abrufen des Fuhrparks: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Fuhrpark-Abruf fehlgeschlagen: {exc}",
        )
