"""Price lists and fee catalog API endpoints (read-only)."""

from __future__ import annotations

import logging
from typing import Any
from fastapi import APIRouter, HTTPException, Path, Query, Request, Response, status

from app.core.cache import cache
from app.core.client import FsmException, fsm_client
from app.core.config import settings
from app.schemas.preislisten import (
    PreislisteItem,
    PreislistenResponse,
    PreispositionItem,
    PreispositionenResponse,
)

logger = logging.getLogger("fsm_gateway.api.preislisten")
router = APIRouter(prefix="/preislisten", tags=["Preislisten & Gebühren"])


@router.get(
    "",
    response_model=PreislistenResponse,
    summary="Alle Preislisten abrufen (Read-Only)",
    description="Liefert alle hinterlegten Preislisten der Fahrschule (inkl. Kennung und Archivierungsstatus).",
)
async def list_preislisten(
    request: Request,
    response: Response,
    refresh: bool = Query(default=False, description="Cache überspringen"),
) -> PreislistenResponse:
    force_refresh = refresh or request.headers.get("x-refresh-cache") == "1"
    cache_key = "preislisten:all"

    if not force_refresh:
        cached_res = await cache.get(cache_key)
        if cached_res is not None:
            response.headers["X-Cache-Hit"] = "1"
            return cached_res

    try:
        raw_list = await fsm_client.get_preislisten(fresh=force_refresh)
        items: list[PreislisteItem] = []
        for r in raw_list:
            if not isinstance(r, dict):
                continue
            pid = str(r.get("id") or "")
            if not pid:
                continue
            items.append(
                PreislisteItem(
                    id=pid,
                    bezeichnung=r.get("bezeichnung") or "Preisliste",
                    kennung=r.get("kennung"),
                    schuelerpreisliste=bool(r.get("schuelerpreisliste", False)),
                    ausblenden=bool(r.get("ausblenden", False)),
                )
            )

        result = PreislistenResponse(count=len(items), preislisten=items)
        await cache.set(cache_key, result, ttl=settings.STAMMDATEN_CACHE_TTL_SECONDS)
        response.headers["X-Cache-Hit"] = "0"
        return result
    except (FsmException, HTTPException):
        raise
    except Exception as exc:
        logger.error("Fehler beim Abrufen der Preislisten: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Preislistenabruf fehlgeschlagen: {exc}",
        )


@router.get(
    "/{preisliste_id}/positionen",
    response_model=PreispositionenResponse,
    summary="Positionen einer Preisliste abrufen (Read-Only)",
    description="Liefert alle Gebühren- und Leistungspositionen (Beträge, Klassen, Theorie/Praxis) einer Preisliste.",
)
async def get_preispositionen(
    request: Request,
    response: Response,
    preisliste_id: str = Path(..., description="UUID der Preisliste"),
    refresh: bool = Query(default=False, description="Cache überspringen"),
) -> PreispositionenResponse:
    clean_id = preisliste_id.strip()
    force_refresh = refresh or request.headers.get("x-refresh-cache") == "1"
    cache_key = f"preislisten:positionen:{clean_id}"

    if not force_refresh:
        cached_res = await cache.get(cache_key)
        if cached_res is not None:
            response.headers["X-Cache-Hit"] = "1"
            return cached_res

    try:
        raw_list = await fsm_client.get_preispositionen(preisliste_id=clean_id, fresh=force_refresh)
        items: list[PreispositionItem] = []
        for r in raw_list:
            if not isinstance(r, dict):
                continue
            pos_id = str(r.get("id") or "")
            if not pos_id:
                continue
            betrag_val = r.get("betrag")
            betrag_float = float(betrag_val) if isinstance(betrag_val, (int, float)) else 0.0

            items.append(
                PreispositionItem(
                    id=pos_id,
                    fidPreisliste=r.get("fidPreisliste") or clean_id,
                    bezeichnung=r.get("bezeichnung") or "Gebührenposition",
                    betrag=betrag_float,
                    klasse=r.get("klasse"),
                    theorie=bool(r.get("theorie", False)),
                    praxis=bool(r.get("praxis", False)),
                    fidleistungsart=r.get("fidleistungsart"),
                    artikel=r.get("artikel"),
                )
            )

        result = PreispositionenResponse(count=len(items), preisliste_id=clean_id, preispositionen=items)
        await cache.set(cache_key, result, ttl=settings.STAMMDATEN_CACHE_TTL_SECONDS)
        response.headers["X-Cache-Hit"] = "0"
        return result
    except (FsmException, HTTPException):
        raise
    except Exception as exc:
        logger.error("Fehler beim Abrufen der Preispositionen für %s: %s", clean_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Preispositionen-Abruf fehlgeschlagen: {exc}",
        )


@router.get(
    "/schueler/{student_uuid}",
    response_model=PreispositionenResponse,
    summary="Individuelle Schülerpreisliste abrufen (Read-Only)",
    description="Liefert die für den Schüler aktuell gültigen Preispositionen und Sondervereinbarungen.",
)
async def get_schueler_preisliste(
    request: Request,
    response: Response,
    student_uuid: str = Path(..., description="UUID des Schülers"),
    refresh: bool = Query(default=False, description="Cache überspringen"),
) -> PreispositionenResponse:
    clean_uuid = student_uuid.strip()
    force_refresh = refresh or request.headers.get("x-refresh-cache") == "1"
    cache_key = f"preislisten:schueler:{clean_uuid}"

    if not force_refresh:
        cached_res = await cache.get(cache_key)
        if cached_res is not None:
            response.headers["X-Cache-Hit"] = "1"
            return cached_res

    try:
        raw_list = await fsm_client.get_schueler_preisliste(student_uuid=clean_uuid, fresh=force_refresh)
        items: list[PreispositionItem] = []
        for r in raw_list:
            if not isinstance(r, dict):
                continue
            pos_id = str(r.get("id") or "")
            if not pos_id:
                continue
            betrag_val = r.get("betrag")
            betrag_float = float(betrag_val) if isinstance(betrag_val, (int, float)) else 0.0

            items.append(
                PreispositionItem(
                    id=pos_id,
                    fidPreisliste=r.get("fidPreisliste"),
                    bezeichnung=r.get("bezeichnung") or "Schülerpreis",
                    betrag=betrag_float,
                    klasse=r.get("klasse"),
                    theorie=bool(r.get("theorie", False)),
                    praxis=bool(r.get("praxis", False)),
                    fidleistungsart=r.get("fidleistungsart"),
                    artikel=r.get("artikel"),
                )
            )

        result = PreispositionenResponse(count=len(items), preisliste_id=clean_uuid, preispositionen=items)
        await cache.set(cache_key, result, ttl=settings.STAMMDATEN_CACHE_TTL_SECONDS)
        response.headers["X-Cache-Hit"] = "0"
        return result
    except (FsmException, HTTPException):
        raise
    except Exception as exc:
        logger.error("Fehler beim Abrufen der Schüler-Preise für %s: %s", clean_uuid, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Schülerpreise-Abruf fehlgeschlagen: {exc}",
        )
