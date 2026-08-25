"""Financial endpoints: Driving lessons, services (Leistungen) and payments."""

from __future__ import annotations

import datetime as dt
import logging
import re
from typing import Any
from fastapi import APIRouter, HTTPException, Path, Query, Request, Response, status

from app.core.cache import cache
from app.core.config import settings
from app.core.client import FsmException, fsm_client
from app.schemas.finanzen import (
    FahrstundeItem,
    FahrstundenResponse,
    LeistungItem,
    LeistungenResponse,
    ZahlungCreateRequest,
    ZahlungResponse,
)

logger = logging.getLogger("fsm_gateway.api.finanzen")
router = APIRouter(tags=["Finanzen & Leistungen"])


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


def _parse_date_time_desc(desc: str | None) -> tuple[str | None, str | None]:
    """Helper to extract date and time from a description string if present."""
    if not desc:
        return None, None
    date_match = re.search(r"am\s+(\d{2}\.\d{2}\.\d{4})", desc)
    time_match = re.search(r"um\s+(\d{2}:\d{2})", desc)
    date_str = date_match.group(1) if date_match else None
    time_str = time_match.group(1) if time_match else None
    return date_str, time_str


@router.get(
    "/schueler/{student_uuid}/fahrstunden",
    response_model=FahrstundenResponse,
    summary="Fahrstunden eines Schülers abrufen",
    description="Liefert alle gefahrenen Stunden, Einheiten und Bezahlstatus für einen Schüler.",
)
async def get_fahrstunden(
    request: Request,
    response: Response,
    student_uuid: str = Path(..., description="FSM Schüler-UUID"),
    skip_deleted: bool = Query(default=True, description="Gelöschte Einträge ausblenden"),
    page: int = Query(default=1, ge=1, description="Seitennummer"),
    page_size: int = Query(default=100, ge=1, le=500, description="Einträge pro Seite"),
    refresh: bool = Query(default=False, description="Erzwingt Live-Abruf"),
) -> FahrstundenResponse:
    clean_uuid = student_uuid.strip()
    cache_key = f"schueler:fahrstunden:{clean_uuid}:{skip_deleted}:{page}:{page_size}"
    force_refresh = refresh or request.headers.get("x-refresh-cache") == "1"

    if not force_refresh:
        cached_res = await cache.get(cache_key)
        if cached_res is not None:
            response.headers["X-Cache-Hit"] = "1"
            return cached_res

    try:
        raw_res = await fsm_client.get_schueler_fahrstunden(
            student_uuid=student_uuid,
            skip_deleted=skip_deleted,
            page=page,
            page_size=page_size,
        )

        rows = raw_res.get("rows", []) if isinstance(raw_res, dict) else []
        lessons: list[FahrstundeItem] = []
        total_mins = 0.0

        for r in rows:
            if not isinstance(r, dict):
                continue
            data = r.get("data", r)
            if not isinstance(data, dict):
                continue

            desc = str(data.get("beschreibung") or data.get("text") or "")
            parsed_date, parsed_time = _parse_date_time_desc(desc)

            mins = _parse_german_number(data.get("minuten") or data.get("dauer")) or 45.0
            total_mins += mins

            price_val = data.get("betrag") or data.get("kosten")
            price_float = _parse_german_number(price_val)

            # Date fallback
            raw_datum = data.get("datum")
            final_date = parsed_date or (str(raw_datum).split("T")[0] if raw_datum else None)

            lessons.append(
                FahrstundeItem(
                    id=str(data.get("id") or ""),
                    datum=final_date,
                    zeit=parsed_time or data.get("uhrzeit"),
                    minuten=mins,
                    fahrlehrer=data.get("fahrlehrer") or data.get("lehrer"),
                    fahrstundenart=data.get("fahrstundenart") or data.get("art"),
                    beschreibung=desc or None,
                    kfz=data.get("kfz") or data.get("fahrzeug"),
                    bezahlt=bool(data.get("bezahlt", False)),
                    betrag=price_float,
                    klasse=data.get("klasse"),
                    raw_data=data,
                )
            )

        result = FahrstundenResponse(
            student_uuid=clean_uuid,
            count=len(lessons),
            total_minutes=total_mins,
            fahrstunden=lessons,
        )
        await cache.set(cache_key, result, ttl=settings.FAHRSTUNDEN_CACHE_TTL_SECONDS)
        response.headers["X-Cache-Hit"] = "0"
        return result

    except (FsmException, HTTPException):
        raise
    except Exception as exc:
        logger.error("Fehler beim Abrufen der Fahrstunden für %s: %s", clean_uuid, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Fahrstunden-Abruf fehlgeschlagen: {exc}",
        )


@router.get(
    "/schueler/{student_uuid}/leistungen",
    response_model=LeistungenResponse,
    summary="Leistungskonto & Gebühren eines Schülers abrufen",
    description="Liefert alle gebuchten Leistungen, Grundbeträge, Prüfungen und Zahlungen.",
)
async def get_leistungen(
    request: Request,
    response: Response,
    student_uuid: str = Path(..., description="FSM Schüler-UUID"),
    skip_deleted: bool = Query(default=True, description="Gelöschte Einträge ignorieren"),
    page: int = Query(default=1, ge=1, description="Seitennummer"),
    page_size: int = Query(default=500, ge=1, le=1000, description="Einträge pro Seite"),
    refresh: bool = Query(default=False, description="Erzwingt Live-Abruf"),
) -> LeistungenResponse:
    clean_uuid = student_uuid.strip()
    cache_key = f"schueler:leistungen:{clean_uuid}:{skip_deleted}:{page}:{page_size}"
    force_refresh = refresh or request.headers.get("x-refresh-cache") == "1"

    if not force_refresh:
        cached_res = await cache.get(cache_key)
        if cached_res is not None:
            response.headers["X-Cache-Hit"] = "1"
            return cached_res

    try:
        raw_res = await fsm_client.get_schueler_leistungen(
            student_uuid=clean_uuid,
            skip_deleted=skip_deleted,
            page=page,
            page_size=page_size,
        )

        rows = raw_res.get("rows", []) if isinstance(raw_res, dict) else []
        items: list[LeistungItem] = []
        total_kosten = 0.0
        total_zahlungen = 0.0

        for r in rows:
            if not isinstance(r, dict):
                continue
            data = r.get("data", r)
            if not isinstance(data, dict):
                continue

            kosten_f = _parse_german_number(data.get("kosten"))
            zahlung_f = _parse_german_number(data.get("zahlung"))
            saldo_f = _parse_german_number(data.get("saldo"))

            if kosten_f:
                total_kosten += kosten_f
            if zahlung_f:
                total_zahlungen += zahlung_f

            items.append(
                LeistungItem(
                    id=str(data.get("id") or ""),
                    leistungsart=str(data.get("leistungsart") or data.get("art") or ""),
                    datum=data.get("datum"),
                    text=data.get("text") or data.get("beschreibung"),
                    kosten=kosten_f,
                    zahlung=zahlung_f,
                    saldo=saldo_f,
                    klasse=data.get("klasse"),
                    zahlungsart=data.get("zahlungsart"),
                    belegnummer=data.get("belegnummer") or data.get("rechnungsnummer"),
                    fahrlehrer=data.get("fahrlehrer") or data.get("lehrer"),
                    raw_data=data,
                )
            )

        result = LeistungenResponse(
            student_uuid=clean_uuid,
            count=len(items),
            total_kosten=round(total_kosten, 2),
            total_zahlungen=round(total_zahlungen, 2),
            leistungen=items,
        )
        await cache.set(cache_key, result, ttl=settings.LEISTUNGEN_CACHE_TTL_SECONDS)
        response.headers["X-Cache-Hit"] = "0"
        return result

    except (FsmException, HTTPException):
        raise
    except Exception as exc:
        logger.error("Fehler beim Abrufen der Leistungen für %s: %s", clean_uuid, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Leistungen-Abruf fehlgeschlagen: {exc}",
        )


@router.post(
    "/schueler/{student_uuid}/zahlung",
    response_model=ZahlungResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Zahlung für Schüler in FSM einbuchen",
    description="Erfasst eine Kartenzahlung (SumUp), Barzahlung oder Überweisung im FSM-Leistungskonto.",
)
async def create_zahlung(
    student_uuid: str = Path(..., description="FSM Schüler-UUID"),
    payload: ZahlungCreateRequest = ...,
) -> ZahlungResponse:
    clean_uuid = student_uuid.strip()
    booking_date = payload.datum or dt.date.today().isoformat()

    try:
        res = await fsm_client.create_zahlung(
            student_uuid=clean_uuid,
            betrag=payload.betrag,
            datum=booking_date,
            zahlungsart=payload.zahlungsart,
            text=payload.text,
            belegnummer=payload.belegnummer,
        )

        # Invalidate student's account balance, services, and lesson cache
        await cache.delete_prefix(f"schueler:leistungen:{clean_uuid}")
        await cache.delete_prefix(f"schueler:details:{clean_uuid}")
        await cache.delete_prefix(f"schueler:fahrstunden:{clean_uuid}")
        await cache.delete_prefix(f"fsm:schueler:{clean_uuid}")
        await cache.delete_prefix(f"fsm:leistungen:{clean_uuid}")
        await cache.delete_prefix(f"fsm:fahrstunden:{clean_uuid}")

        return ZahlungResponse(
            success=True,
            student_uuid=clean_uuid,
            betrag=payload.betrag,
            zahlungsart=payload.zahlungsart,
            belegnummer=payload.belegnummer,
            message=f"Zahlung von {payload.betrag:.2f} € erfolgreich in FSM eingebucht.",
        )

    except (FsmException, HTTPException):
        raise
    except Exception as exc:
        logger.error("Fehler beim Einbuchen der Zahlung für %s: %s", clean_uuid, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Zahlungseinbuchung fehlgeschlagen: {exc}",
        )
