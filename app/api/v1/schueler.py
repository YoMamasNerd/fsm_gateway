"""Student management and search API endpoints."""

from __future__ import annotations

import logging
from typing import Any
from fastapi import APIRouter, HTTPException, Path, Query, Request, Response, status

from app.core.cache import cache
from app.core.config import settings
from app.core.client import FsmException, fsm_client
from app.schemas.schueler import (
    SchuelerDetails,
    SchuelerKurzItem,
    SchuelerSucheRequest,
    SchuelerSucheResponse,
)

logger = logging.getLogger("fsm_gateway.api.schueler")
router = APIRouter(prefix="/schueler", tags=["Schüler"])


import re

def _parse_german_number(val: Any) -> float | None:
    """Safely convert int, float, or localized German numeric string to float."""
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


def _extract_student_item(raw_row: dict[str, Any]) -> SchuelerKurzItem | None:
    """Helper to extract and normalize a student record from FSM row data."""
    data = raw_row.get("data", raw_row)
    if not isinstance(data, dict):
        return None

    sid = str(data.get("id") or "")
    if not sid:
        return None

    vorname = (data.get("vorname") or "").strip()
    nachname = (data.get("nachname") or "").strip()
    voller_name = f"{vorname} {nachname}".strip()
    if not voller_name:
        voller_name = str(data.get("name") or data.get("displayName") or "Unbekannt")

    saldo_float = _parse_german_number(data.get("saldo"))

    return SchuelerKurzItem(
        id=sid,
        vorname=vorname,
        nachname=nachname,
        voller_name=voller_name,
        karteiNr=data.get("karteiNr") or data.get("displayKarteinummer"),
        klassen=data.get("klassen"),
        saldo=saldo_float,
        gesperrt=bool(data.get("gesperrt", False)),
        raw_data=data,
    )


@router.post(
    "/suche",
    response_model=SchuelerSucheResponse,
    summary="Schülersuche (POST)",
    description="Sucht Schüler anhand von Suchbegriff, Vorname, Nachname, Karteinummer, Status und Pagination.",
)
async def search_schueler_post(
    response: Response,
    payload: SchuelerSucheRequest,
) -> SchuelerSucheResponse:
    response.headers["X-Cache-Hit"] = "0"
    try:
        raw_res = await fsm_client.search_schueler(
            query=payload.query,
            vorname=payload.vorname,
            nachname=payload.nachname,
            kartei_nr=payload.karteiNr,
            only_active=payload.only_active,
            count=payload.count,
            index=payload.index,
        )

        rows = raw_res.get("rows", []) if isinstance(raw_res, dict) else []
        schueler_items: list[SchuelerKurzItem] = []

        for r in rows:
            if isinstance(r, dict):
                item = _extract_student_item(r)
                if item:
                    schueler_items.append(item)

        return SchuelerSucheResponse(count=len(schueler_items), schueler=schueler_items)
    except (FsmException, HTTPException):
        raise
    except Exception as exc:
        logger.error("Fehler bei Schülersuche: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Schülersuche fehlgeschlagen: {exc}",
        )


@router.get(
    "/suche",
    response_model=SchuelerSucheResponse,
    summary="Schülersuche (GET)",
    description="Einfache Schülersuche via Query-Parameter (unterstützt Suchbegriff, Vorname, Nachname, Karteinummer).",
)
async def search_schueler_get(
    response: Response,
    query: str | None = Query(default=None, description="Suchbegriff (Vorname, Nachname oder Volltext)"),
    q: str | None = Query(default=None, description="Kurzalias für Suchbegriff (?q=...)"),
    vorname: str | None = Query(default=None, description="Vorname"),
    nachname: str | None = Query(default=None, alias="name", description="Nachname"),
    kartei_nr: str | None = Query(default=None, alias="karteiNr", description="Karteinummer"),
    only_active: bool = Query(default=True, description="Nur aktive Schüler"),
    count: int = Query(default=5000, description="Anzahl Ergebnisse"),
    index: int = Query(default=0, description="Offset Index"),
) -> SchuelerSucheResponse:
    effective_query = q or query
    req = SchuelerSucheRequest(
        query=effective_query,
        vorname=vorname,
        nachname=nachname,
        karteiNr=kartei_nr,
        only_active=only_active,
        count=count,
        index=index,
    )
    return await search_schueler_post(response=response, payload=req)


@router.get(
    "/{student_uuid}",
    response_model=SchuelerDetails,
    summary="Schüler-Stammdaten & Kartei abrufen",
    description="Liefert alle Stammdaten, Adress-, Klassen- und Kontaktinformationen eines Schülers.",
)
async def get_schueler_details(
    request: Request,
    response: Response,
    student_uuid: str = Path(..., description="FSM Schüler-UUID"),
    refresh: bool = Query(default=False, description="Erzwingt Live-Abruf"),
) -> SchuelerDetails:
    clean_uuid = student_uuid.strip()
    cache_key = f"schueler:details:{clean_uuid}"
    force_refresh = refresh or request.headers.get("x-refresh-cache") == "1"

    if not force_refresh:
        cached_res = await cache.get(cache_key)
        if cached_res is not None:
            response.headers["X-Cache-Hit"] = "1"
            return cached_res

    try:
        raw = await fsm_client.get_schueler_details(student_uuid=clean_uuid, fresh=force_refresh)
        if not raw or not isinstance(raw, dict):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Schüler mit UUID '{clean_uuid}' nicht gefunden.",
            )

        vorname = (raw.get("vorname") or "").strip()
        nachname = (raw.get("nachname") or "").strip()
        voller_name = f"{vorname} {nachname}".strip() or str(raw.get("name") or "Unbekannt")

        saldo_float = _parse_german_number(raw.get("saldo"))

        result = SchuelerDetails(
            id=clean_uuid,
            vorname=vorname,
            nachname=nachname,
            voller_name=voller_name,
            anrede=raw.get("anrede"),
            titel=raw.get("titel"),
            geburtsdatum=raw.get("geburtsdatum"),
            geburtsort=raw.get("geburtsort"),
            strasse=raw.get("strasse"),
            plz=raw.get("plz"),
            ort=raw.get("ort"),
            telefon=raw.get("telefon"),
            handy=raw.get("handy") or raw.get("mobil"),
            email=raw.get("email"),
            karteiNr=raw.get("karteiNr") or raw.get("displayKarteinummer"),
            saldo=saldo_float,
            klassen=raw.get("klassen"),
            gesperrt=bool(raw.get("gesperrt", False)),
            raw_data=raw,
        )

        await cache.set(cache_key, result, ttl=settings.SCHUELER_CACHE_TTL_SECONDS)
        response.headers["X-Cache-Hit"] = "0"
        return result

    except (FsmException, HTTPException):
        raise
    except Exception as exc:
        logger.error("Fehler beim Abrufen der Schülerkartei %s: %s", clean_uuid, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Schülerabruf fehlgeschlagen: {exc}",
        )
