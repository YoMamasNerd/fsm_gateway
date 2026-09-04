"""Performance and exam statistics API endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Request, Response, status

from app.core.cache import cache
from app.core.client import FsmException, fsm_client
from app.core.config import settings
from app.schemas.statistiken import PruefungsstatistikItem, PruefungsstatistikResponse

logger = logging.getLogger("fsm_gateway.api.statistiken")
router = APIRouter(prefix="/statistiken", tags=["Statistiken"])


@router.get(
    "/pruefungen/lehrer",
    response_model=PruefungsstatistikResponse,
    summary="Prüfungsstatistiken nach Fahrlehrern",
    description="Liefert Anmeldezahlen, bestandene Prüfungen und Erfolgsquoten pro Fahrlehrer.",
)
async def get_pruefungsstatistik_lehrer(
    request: Request,
    response: Response,
    jahr: int = Query(default=2026, description="Jahr der Auswertung"),
    zeitraum: int = Query(default=1, description="Zeitraum-Modus (1=Jahr, 3=Gesamt)"),
    quartal: int = Query(default=0, description="Quartal (0=Alle)"),
    refresh: bool = Query(default=False, description="Cache überspringen"),
) -> PruefungsstatistikResponse:
    force_refresh = refresh or request.headers.get("x-refresh-cache") == "1"
    cache_key = f"statistiken:pruefungen:lehrer:{jahr}:{zeitraum}:{quartal}"

    if not force_refresh:
        cached_res = await cache.get(cache_key)
        if cached_res is not None:
            response.headers["X-Cache-Hit"] = "1"
            return cached_res

    try:
        raw_list = await fsm_client.get_pruefungsstatistik(
            jahr=jahr,
            zeitraum=zeitraum,
            quartal=quartal,
            fresh=force_refresh,
        )
        items: list[PruefungsstatistikItem] = []
        for r in raw_list:
            if isinstance(r, dict) and r.get("name"):
                items.append(
                    PruefungsstatistikItem(
                        name=r["name"],
                        anmeldungen=r.get("anmeldungen", 0),
                        bestanden=r.get("bestanden", 0),
                        durchgefallen=r.get("durchgefallen", 0),
                        erfolgsquote_pct=r.get("erfolgsquote_pct", 0.0),
                    )
                )

        result = PruefungsstatistikResponse(
            jahr=jahr,
            zeitraum=zeitraum,
            count=len(items),
            statistiken=items,
        )
        await cache.set(cache_key, result, ttl=settings.STATISTIKEN_CACHE_TTL_SECONDS)
        response.headers["X-Cache-Hit"] = "0"
        return result
    except (FsmException, HTTPException):
        raise
    except Exception as exc:
        logger.error("Fehler beim Abrufen der Fahrlehrer-Prüfungsstatistik: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Statistikabruf fehlgeschlagen: {exc}",
        )


@router.get(
    "/pruefungen/klassen",
    response_model=PruefungsstatistikResponse,
    summary="Prüfungsstatistiken nach Führerscheinklassen",
    description="Liefert Anmeldezahlen, bestandene Prüfungen und Erfolgsquoten pro Führerscheinklasse.",
)
async def get_pruefungsstatistik_klassen(
    request: Request,
    response: Response,
    jahr: int = Query(default=2026, description="Jahr der Auswertung"),
    zeitraum: int = Query(default=1, description="Zeitraum-Modus (1=Jahr, 3=Gesamt)"),
    quartal: int = Query(default=0, description="Quartal (0=Alle)"),
    refresh: bool = Query(default=False, description="Cache überspringen"),
) -> PruefungsstatistikResponse:
    force_refresh = refresh or request.headers.get("x-refresh-cache") == "1"
    cache_key = f"statistiken:pruefungen:klassen:{jahr}:{zeitraum}:{quartal}"

    if not force_refresh:
        cached_res = await cache.get(cache_key)
        if cached_res is not None:
            response.headers["X-Cache-Hit"] = "1"
            return cached_res

    try:
        raw_list = await fsm_client.get_pruefungsstatistik_klassen(
            jahr=jahr,
            zeitraum=zeitraum,
            quartal=quartal,
            fresh=force_refresh,
        )
        items: list[PruefungsstatistikItem] = []
        for r in raw_list:
            if isinstance(r, dict) and r.get("name"):
                items.append(
                    PruefungsstatistikItem(
                        name=r["name"],
                        anmeldungen=r.get("anmeldungen", 0),
                        bestanden=r.get("bestanden", 0),
                        durchgefallen=r.get("durchgefallen", 0),
                        erfolgsquote_pct=r.get("erfolgsquote_pct", 0.0),
                    )
                )

        result = PruefungsstatistikResponse(
            jahr=jahr,
            zeitraum=zeitraum,
            count=len(items),
            statistiken=items,
        )
        await cache.set(cache_key, result, ttl=settings.STATISTIKEN_CACHE_TTL_SECONDS)
        response.headers["X-Cache-Hit"] = "0"
        return result
    except (FsmException, HTTPException):
        raise
    except Exception as exc:
        logger.error("Fehler beim Abrufen der Klassen-Prüfungsstatistik: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Statistikabruf fehlgeschlagen: {exc}",
        )
