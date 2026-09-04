"""Master data API endpoints (Filialen, Klassen, Leistungsarten, Treffpunkte, Theoriekapitel)."""

from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response, status

from app.core.cache import cache
from app.core.client import FsmException, fsm_client
from app.core.config import settings
from app.schemas.stammdaten import (
    FilialeItem,
    FilialenListResponse,
    KlasseItem,
    KlassenListResponse,
    LeistungsartenListResponse,
    LeistungsartItem,
    TreffpunkteListResponse,
    TreffpunktItem,
)
from app.schemas.theorie import TheoriekapitelItem, TheoriekapitelListResponse

logger = logging.getLogger("fsm_gateway.api.stammdaten")
router = APIRouter(prefix="/stammdaten", tags=["Stammdaten"])


def _parse_german_number(val: Any) -> float | None:
    """Safely convert numeric values."""
    if val is None or val == "":
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        cleaned = re.sub(r"[^\d,\.\-]", "", val.strip())
        if not cleaned:
            return None
        if "," in cleaned and "." in cleaned:
            cleaned = cleaned.replace(".", "").replace(",", ".")
        elif "," in cleaned:
            cleaned = cleaned.replace(",", ".")
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


@router.get(
    "/filialen",
    response_model=FilialenListResponse,
    summary="Filialen / Standorte abrufen",
    description="Liefert alle Filialen der Fahrschule mit Anschrift, Kennung und Kontaktdaten.",
)
async def list_filialen(
    request: Request,
    response: Response,
    refresh: bool = Query(default=False, description="Cache überspringen"),
) -> FilialenListResponse:
    force_refresh = refresh or request.headers.get("x-refresh-cache") == "1"
    cache_key = "stammdaten:filialen"

    if not force_refresh:
        cached_res = await cache.get(cache_key)
        if cached_res is not None:
            response.headers["X-Cache-Hit"] = "1"
            return cached_res

    try:
        raw_list = await fsm_client.get_filialen(fresh=force_refresh)
        items: list[FilialeItem] = []
        for r in raw_list:
            if not isinstance(r, dict):
                continue
            fid = str(r.get("id") or "")
            if not fid:
                continue
            items.append(
                FilialeItem(
                    id=fid,
                    name=r.get("name") or "Unbenannte Filiale",
                    kennung=r.get("kennung"),
                    strasse=r.get("strasse"),
                    plz=r.get("plz"),
                    ort=r.get("ort"),
                    telefon=r.get("telefon"),
                )
            )

        result = FilialenListResponse(count=len(items), filialen=items)
        await cache.set(cache_key, result, ttl=settings.STAMMDATEN_CACHE_TTL_SECONDS)
        response.headers["X-Cache-Hit"] = "0"
        return result
    except (FsmException, HTTPException):
        raise
    except Exception as exc:
        logger.error("Fehler beim Abrufen der Filialen: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Filialenabruf fehlgeschlagen: {exc}",
        )


@router.get(
    "/klassen",
    response_model=KlassenListResponse,
    summary="Führerscheinklassen abrufen",
    description="Liefert alle angebotenen Führerscheinklassen (B, B197, A, BE etc.).",
)
async def list_klassen(
    request: Request,
    response: Response,
    refresh: bool = Query(default=False, description="Cache überspringen"),
) -> KlassenListResponse:
    force_refresh = refresh or request.headers.get("x-refresh-cache") == "1"
    cache_key = "stammdaten:klassen"

    if not force_refresh:
        cached_res = await cache.get(cache_key)
        if cached_res is not None:
            response.headers["X-Cache-Hit"] = "1"
            return cached_res

    try:
        raw_list = await fsm_client.get_klassen(fresh=force_refresh)
        items: list[KlasseItem] = []
        for r in raw_list:
            if not isinstance(r, dict):
                continue
            kid = str(r.get("id") or "")
            if not kid:
                continue
            items.append(
                KlasseItem(
                    id=kid,
                    bezeichnung=r.get("bezeichnung") or r.get("name") or kid,
                    kuerzel=r.get("kuerzel"),
                    fahrzeugart=r.get("fahrzeugart"),
                )
            )

        result = KlassenListResponse(count=len(items), klassen=items)
        await cache.set(cache_key, result, ttl=settings.STAMMDATEN_CACHE_TTL_SECONDS)
        response.headers["X-Cache-Hit"] = "0"
        return result
    except (FsmException, HTTPException):
        raise
    except Exception as exc:
        logger.error("Fehler beim Abrufen der Klassen: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Klassenabruf fehlgeschlagen: {exc}",
        )


@router.get(
    "/leistungsarten",
    response_model=LeistungsartenListResponse,
    summary="Leistungsarten-Katalog abrufen",
    description="Liefert alle abrechenbaren Positionen (Grundgebühr, Übungsstunde, Sonderfahrten, Prüfungen).",
)
async def list_leistungsarten(
    request: Request,
    response: Response,
    refresh: bool = Query(default=False, description="Cache überspringen"),
) -> LeistungsartenListResponse:
    force_refresh = refresh or request.headers.get("x-refresh-cache") == "1"
    cache_key = "stammdaten:leistungsarten"

    if not force_refresh:
        cached_res = await cache.get(cache_key)
        if cached_res is not None:
            response.headers["X-Cache-Hit"] = "1"
            return cached_res

    try:
        raw_list = await fsm_client.get_leistungsarten(fresh=force_refresh)
        items: list[LeistungsartItem] = []
        for r in raw_list:
            if not isinstance(r, dict):
                continue
            lid = str(r.get("id") or "")
            if not lid:
                continue
            items.append(
                LeistungsartItem(
                    id=lid,
                    bezeichnung=r.get("bezeichnung") or r.get("name") or "Unbenannte Leistung",
                    kuerzel=r.get("kuerzel"),
                    preis=_parse_german_number(r.get("preis")),
                    dauer_minuten=_parse_german_number(r.get("dauer") or r.get("dauer_minuten")),
                )
            )

        result = LeistungsartenListResponse(count=len(items), leistungsarten=items)
        await cache.set(cache_key, result, ttl=settings.STAMMDATEN_CACHE_TTL_SECONDS)
        response.headers["X-Cache-Hit"] = "0"
        return result
    except (FsmException, HTTPException):
        raise
    except Exception as exc:
        logger.error("Fehler beim Abrufen der Leistungsarten: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Leistungsartenabruf fehlgeschlagen: {exc}",
        )


@router.get(
    "/theoriekapitel",
    response_model=TheoriekapitelListResponse,
    summary="Theoriekapitel / Themenkatalog abrufen",
    description="Liefert alle Theoriekapitel (Grundstoff, Zusatzstoff B etc.).",
)
async def list_theoriekapitel(
    request: Request,
    response: Response,
    refresh: bool = Query(default=False, description="Cache überspringen"),
) -> TheoriekapitelListResponse:
    force_refresh = refresh or request.headers.get("x-refresh-cache") == "1"
    cache_key = "stammdaten:theoriekapitel"

    if not force_refresh:
        cached_res = await cache.get(cache_key)
        if cached_res is not None:
            response.headers["X-Cache-Hit"] = "1"
            return cached_res

    try:
        raw_list = await fsm_client.get_theoriekapitel(fresh=force_refresh)
        items: list[TheoriekapitelItem] = []
        for r in raw_list:
            if not isinstance(r, dict):
                continue
            tid = str(r.get("id") or "")
            if not tid:
                continue
            items.append(
                TheoriekapitelItem(
                    id=tid,
                    bezeichnung=r.get("bezeichnung") or r.get("thema") or "Theoriekapitel",
                    systemtheoriegruppe=r.get("fidsystemtheoriegruppe") or r.get("systemtheoriegruppe"),
                )
            )

        result = TheoriekapitelListResponse(count=len(items), kapitel=items)
        await cache.set(cache_key, result, ttl=settings.STAMMDATEN_CACHE_TTL_SECONDS)
        response.headers["X-Cache-Hit"] = "0"
        return result
    except (FsmException, HTTPException):
        raise
    except Exception as exc:
        logger.error("Fehler beim Abrufen der Theoriekapitel: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Theoriekapitelabruf fehlgeschlagen: {exc}",
        )


@router.get(
    "/treffpunkte",
    response_model=TreffpunkteListResponse,
    summary="Treffpunkte für Fahrstunden abrufen",
    description="Liefert alle hinterlegten Start- und Treffpunkte für Fahrstunden.",
)
async def list_treffpunkte(
    request: Request,
    response: Response,
    refresh: bool = Query(default=False, description="Cache überspringen"),
) -> TreffpunkteListResponse:
    force_refresh = refresh or request.headers.get("x-refresh-cache") == "1"
    cache_key = "stammdaten:treffpunkte"

    if not force_refresh:
        cached_res = await cache.get(cache_key)
        if cached_res is not None:
            response.headers["X-Cache-Hit"] = "1"
            return cached_res

    try:
        raw_list = await fsm_client.get_treffpunkte(fresh=force_refresh)
        items: list[TreffpunktItem] = []
        for r in raw_list:
            if not isinstance(r, dict):
                continue
            tid = str(r.get("id") or "")
            if not tid:
                continue
            items.append(
                TreffpunktItem(
                    id=tid,
                    treffpunkt=r.get("treffpunkt") or r.get("bezeichnung") or "Treffpunkt",
                    strasse=r.get("strasse"),
                    plz=r.get("plz"),
                    ort=r.get("ort"),
                )
            )

        result = TreffpunkteListResponse(count=len(items), treffpunkte=items)
        await cache.set(cache_key, result, ttl=settings.STAMMDATEN_CACHE_TTL_SECONDS)
        response.headers["X-Cache-Hit"] = "0"
        return result
    except (FsmException, HTTPException):
        raise
    except Exception as exc:
        logger.error("Fehler beim Abrufen der Treffpunkte: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Treffpunkteabruf fehlgeschlagen: {exc}",
        )
