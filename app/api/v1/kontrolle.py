"""Kontrolle API: Leistungen je Fahrlehrer/Tag (FSM-Portal 'Kontrolle'-Ansicht)."""

import logging

from fastapi import APIRouter, HTTPException, Query, Request, Response, status

from app.core.cache import cache
from app.core.client import FsmException, fsm_client
from app.core.config import settings
from app.schemas.kontrolle import (
    ArbeitszeitSummary,
    KontrolleLeistung,
    KontrolleResponse,
)

logger = logging.getLogger("fsm_gateway.api.kontrolle")
router = APIRouter(prefix="/kontrolle", tags=["Kontrolle"])

# Praktische Leistungsarten zählen zur Fahr-/Praxiszeit, alles andere zu "sonstige".
PRAKTISCHE_LEISTUNGSARTEN = {"FS", "UW", "UB", "SF", "PF", "PS", "PR", "ST", "RG", "LM", "ZG", "BM", "GG"}


@router.get(
    "/{fahrlehrer_id}",
    response_model=KontrolleResponse,
    summary="Kontrolle: verbuchte Leistungen eines Fahrlehrers pro Tag",
    description=(
        "Liefert alle an einem Tag verbuchten Leistungen (Layer 'Leistungen', nicht der Kalender) "
        "eines Fahrlehrer - identisch zur FSM-Portal-Ansicht 'Kontrolle'. Zusätzlich FSMs eigene "
        "Arbeitszeit-Summe (praxis/sonstiges) als Referenzwert, den FSM auch bei Buchungs-"
        "Validierungen (600-Min-Regel) heranzieht. Kurz gecached."
    ),
)
async def get_kontrolle(
    fahrlehrer_id: str,
    request: Request,
    response: Response,
    datum: str = Query(..., description="Datum im Format YYYY-MM-DD"),
    refresh: bool = Query(default=False, description="Cache überspringen und frisch von FSM abrufen"),
) -> KontrolleResponse:
    force_refresh = refresh or request.headers.get("x-refresh-cache") == "1"
    clean_fl_id = fahrlehrer_id.strip()
    cache_key = f"kontrolle:{clean_fl_id}:{datum}"

    if not force_refresh:
        cached_res = await cache.get(cache_key)
        if cached_res is not None:
            response.headers["X-Cache-Hit"] = "1"
            return cached_res

    try:
        raw_list = await fsm_client.get_kontrolle(clean_fl_id, datum)
        leistungen = [KontrolleLeistung.model_validate(r) for r in raw_list if isinstance(r, dict)]

        minuten_praktisch = 0.0
        minuten_sonstige = 0.0
        for leistung in leistungen:
            minuten = float(leistung.minuten or 0)
            if (leistung.leistungsart or "").upper() in PRAKTISCHE_LEISTUNGSARTEN:
                minuten_praktisch += minuten
            else:
                minuten_sonstige += minuten

        try:
            raw_az = await fsm_client.get_arbeitszeit(clean_fl_id, datum)
            arbeitszeit = ArbeitszeitSummary.model_validate(raw_az) if raw_az else None
        except Exception as exc:  # Arbeitszeit ist Bonus, darf Kontrolle nicht brechen
            logger.warning("Kontrolle %s %s: Arbeitszeit-Abruf fehlgeschlagen: %s", clean_fl_id, datum, exc)
            arbeitszeit = None

        result = KontrolleResponse(
            fahrlehrer_id=clean_fl_id,
            datum=datum,
            leistungen=leistungen,
            count=len(leistungen),
            minuten_praktisch=minuten_praktisch,
            minuten_sonstige=minuten_sonstige,
            minuten_total=minuten_praktisch + minuten_sonstige,
            arbeitszeit=arbeitszeit,
        )
        await cache.set(cache_key, result, ttl=settings.KONTROLLE_CACHE_TTL_SECONDS)
        response.headers["X-Cache-Hit"] = "0"
        return result
    except (FsmException, HTTPException):
        raise
    except Exception as exc:
        logger.error("Fehler beim Abrufen der Kontrolle für %s am %s: %s", clean_fl_id, datum, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Kontrolle-Abruf fehlgeschlagen: {exc}",
        )
