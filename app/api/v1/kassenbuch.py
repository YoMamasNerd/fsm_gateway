"""Cash book and cash transactions API endpoints."""

from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, HTTPException, Path, Query, Request, Response, status

from app.core.cache import cache
from app.core.client import FsmException, fsm_client
from app.core.config import settings
from app.schemas.kassenbuch import (
    KassenbuchItem,
    KassenbuchungenResponse,
    KassenbuchungItem,
    KassenbuecherListResponse,
)

logger = logging.getLogger("fsm_gateway.api.kassenbuch")
router = APIRouter(prefix="/kassenbuecher", tags=["Kassenbuch & Barzahlungen"])


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
    "",
    response_model=KassenbuecherListResponse,
    summary="Kassenbücher abrufen",
    description="Liefert alle Kassenbücher der Fahrschule (Bürokasse, Fahrlehrerkassen).",
)
async def list_kassenbuecher(
    request: Request,
    response: Response,
    refresh: bool = Query(default=False, description="Cache überspringen"),
) -> KassenbuecherListResponse:
    force_refresh = refresh or request.headers.get("x-refresh-cache") == "1"
    cache_key = "kassenbuecher:all"

    if not force_refresh:
        cached_res = await cache.get(cache_key)
        if cached_res is not None:
            response.headers["X-Cache-Hit"] = "1"
            return cached_res

    try:
        raw_list = await fsm_client.get_kassenbuecher(fresh=force_refresh)
        items: list[KassenbuchItem] = []
        for r in raw_list:
            if not isinstance(r, dict):
                continue
            kid = str(r.get("id") or "")
            if not kid:
                continue
            items.append(
                KassenbuchItem(
                    id=kid,
                    bezeichnung=r.get("bezeichnung") or r.get("name") or "Kassenbuch",
                    lehrer_name=r.get("lehrer") or r.get("lehrer_Name"),
                    fidLehrer=r.get("fidLehrer"),
                    aktiv=bool(r.get("aktiv", True)),
                )
            )

        result = KassenbuecherListResponse(count=len(items), kassenbuecher=items)
        await cache.set(cache_key, result, ttl=settings.KASSENBUCH_CACHE_TTL_SECONDS)
        response.headers["X-Cache-Hit"] = "0"
        return result
    except (FsmException, HTTPException):
        raise
    except Exception as exc:
        logger.error("Fehler beim Abrufen der Kassenbücher: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Kassenbuchabruf fehlgeschlagen: {exc}",
        )


@router.get(
    "/{kassenbuch_id}/buchungen",
    response_model=KassenbuchungenResponse,
    summary="Kassenbuchungen abrufen",
    description="Liefert die Buchungspositionen eines Kassenbuchs für ein Jahr und optionalen Monat.",
)
async def list_kassenbuchungen(
    request: Request,
    response: Response,
    kassenbuch_id: str = Path(..., description="UUID des Kassenbuchs"),
    jahr: int = Query(default=2026, description="Jahr"),
    monat: int | None = Query(default=None, description="Monat (1-12, optional)"),
    refresh: bool = Query(default=False, description="Cache überspringen"),
) -> KassenbuchungenResponse:
    clean_id = kassenbuch_id.strip()
    force_refresh = refresh or request.headers.get("x-refresh-cache") == "1"
    cache_key = f"kassenbuch:buchungen:{clean_id}:{jahr}:{monat}"

    if not force_refresh:
        cached_res = await cache.get(cache_key)
        if cached_res is not None:
            response.headers["X-Cache-Hit"] = "1"
            return cached_res

    try:
        raw_list = await fsm_client.get_kassenbuchungen(
            kassenbuch_id=clean_id,
            jahr=jahr,
            monat=monat,
            fresh=force_refresh,
        )
        items: list[KassenbuchungItem] = []
        for r in raw_list:
            if not isinstance(r, dict):
                continue
            bid = str(r.get("id") or "")
            if not bid:
                continue
            items.append(
                KassenbuchungItem(
                    id=bid,
                    datum=r.get("datum") or r.get("belegdatum"),
                    text=r.get("text") or r.get("beschreibung"),
                    einnahme=_parse_german_number(r.get("einnahme")) or 0.0,
                    ausgabe=_parse_german_number(r.get("ausgabe")) or 0.0,
                    saldo=_parse_german_number(r.get("saldo")) or 0.0,
                    belegnummer=r.get("belegnummer") or r.get("beleg"),
                )
            )

        result = KassenbuchungenResponse(
            kassenbuch_id=clean_id,
            jahr=jahr,
            monat=monat,
            count=len(items),
            buchungen=items,
        )
        await cache.set(cache_key, result, ttl=settings.KASSENBUCH_CACHE_TTL_SECONDS)
        response.headers["X-Cache-Hit"] = "0"
        return result
    except (FsmException, HTTPException):
        raise
    except Exception as exc:
        logger.error("Fehler beim Abrufen der Kassenbuchungen für %s: %s", clean_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Kassenbuchungen-Abruf fehlgeschlagen: {exc}",
        )
