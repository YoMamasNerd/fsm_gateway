"""Calendar and appointment management endpoints."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Any
from fastapi import APIRouter, HTTPException, Path, Query, Request, Response, status

from app.core.cache import cache
from app.core.client import FsmException, fsm_client
from app.core.config import settings
from app.schemas.kalender import (
    KalenderEvent,
    KalenderResponse,
    TagesbelegungResponse,
    TerminActionResponse,
    TerminCreateRequest,
    TerminCreateResponse,
    TerminUpdateRequest,
)

logger = logging.getLogger("fsm_gateway.api.kalender")
router = APIRouter(tags=["Kalender & Termine"])

# In-flight revalidation tracker to avoid redundant concurrent requests
_revalidating_keys: set[str] = set()


def _parse_iso_datetime(val: Any) -> dt.datetime | None:
    """Safely parse various datetime formats."""
    if isinstance(val, dt.datetime):
        return val
    if isinstance(val, str):
        try:
            return dt.datetime.fromisoformat(val.replace("Z", "+00:00"))
        except ValueError:
            pass
    return None


def _normalize_date_str(val: str | None, default_date: dt.date) -> str:
    """Normalizes ISO datetime or date strings to YYYY-MM-DD for deterministic caching."""
    if not val:
        return default_date.isoformat()
    val_clean = val.strip()
    if "T" in val_clean or " " in val_clean:
        parsed = _parse_iso_datetime(val_clean)
        if parsed:
            return parsed.date().isoformat()
    try:
        return dt.date.fromisoformat(val_clean.split()[0]).isoformat()
    except Exception:
        return val_clean


async def _fetch_and_parse_calendar(
    fahrlehrer_id: str,
    effective_start: str,
    effective_end: str,
    only_buchbar: bool,
    skip_deleted: bool,
) -> KalenderResponse:
    """Fetches calendar events from FSM Cloud API and parses them into KalenderResponse."""
    raw_events = await fsm_client.get_kalender(
        fahrlehrer_id=fahrlehrer_id,
        start_date=f"{effective_start} 00:00:00" if len(effective_start) == 10 else effective_start,
        end_date=f"{effective_end} 23:59:59" if len(effective_end) == 10 else effective_end,
        only_buchbar=only_buchbar,
        skip_deleted=skip_deleted,
    )

    events: list[KalenderEvent] = []
    for row in raw_events:
        if not isinstance(row, dict):
            continue

        von_dt = _parse_iso_datetime(row.get("von"))
        bis_dt = _parse_iso_datetime(row.get("bis"))

        if not von_dt or not bis_dt:
            continue

        dauer = max(0.0, (bis_dt - von_dt).total_seconds() / 60.0)
        terminart = str(row.get("fidTerminart") or row.get("terminart") or "PX").upper()
        titel = str(row.get("texte") or row.get("titel") or row.get("beschreibung") or "")
        schueler_name = row.get("schuelername") or row.get("schueler_name")

        # Determine classification flags
        ist_theorie = terminart in ("TH", "PT") or "theorie" in titel.lower()
        ist_blocker = terminart in ("ST", "PP", "PX", "BL") or any(
            k in titel.lower() for k in ("sperr", "urlaub", "pause", "blocker", "abwesend")
        )
        ist_fahrstunde = (
            terminart in ("FS", "UW", "ÜB", "SF", "PF")
            or bool(schueler_name)
            or (not ist_theorie and not ist_blocker)
        )

        schueler_id = row.get("fidSchueler") or row.get("schueler_id")
        fahrzeug_id = row.get("fidFahrzeug") or row.get("fahrzeug_id")
        gebucht = bool(row.get("gebucht", False))

        events.append(
            KalenderEvent(
                id=str(row.get("id", "")),
                von=von_dt,
                bis=bis_dt,
                fahrlehrer_id=fahrlehrer_id,
                terminart=terminart,
                titel=titel,
                schueler_name=schueler_name,
                schueler_id=str(schueler_id) if schueler_id else None,
                fahrzeug_id=str(fahrzeug_id) if fahrzeug_id else None,
                gebucht=gebucht,
                ist_fahrstunde=ist_fahrstunde,
                ist_theorie=ist_theorie,
                ist_blocker=ist_blocker,
                dauer_minuten=dauer,
            )
        )

    return KalenderResponse(
        fahrlehrer_id=fahrlehrer_id,
        start=effective_start,
        end=effective_end,
        count=len(events),
        events=events,
    )


async def _revalidate_calendar_in_background(
    cache_key: str,
    fahrlehrer_id: str,
    effective_start: str,
    effective_end: str,
    only_buchbar: bool,
    skip_deleted: bool,
) -> None:
    """Asynchronously refreshes the calendar cache without blocking client responses."""
    try:
        result = await _fetch_and_parse_calendar(
            fahrlehrer_id=fahrlehrer_id,
            effective_start=effective_start,
            effective_end=effective_end,
            only_buchbar=only_buchbar,
            skip_deleted=skip_deleted,
        )
        await cache.set(cache_key, result, ttl=settings.CALENDAR_CACHE_TTL_SECONDS)
        logger.debug("SWR: Revalidation successful for %s", cache_key)
    except Exception as exc:
        logger.warning("SWR: Background revalidation failed for %s: %s", cache_key, exc)
    finally:
        _revalidating_keys.discard(cache_key)


@router.get(
    "/kalender/{fahrlehrer_id}",
    response_model=KalenderResponse,
    summary="Kalender-Events eines Fahrlehrers abrufen",
    description="Liefert alle Fahrstunden, Theorie-Einheiten und Blocker für einen Zeitraum mit optimistischem Caching.",
)
async def get_kalender(
    request: Request,
    response: Response,
    fahrlehrer_id: str = Path(..., description="FSM UUID des Fahrlehrers"),
    start: str | None = Query(default=None, alias="von", description="Startdatum (z.B. 2026-08-16 oder ISO)"),
    end: str | None = Query(default=None, alias="bis", description="Enddatum (z.B. 2026-08-23 oder ISO)"),
    start_datum: str | None = Query(default=None, alias="startDatum", description="Kompatibilitätsalias"),
    end_datum: str | None = Query(default=None, alias="endDatum", description="Kompatibilitätsalias"),
    only_buchbar: bool = Query(default=False, description="Nur buchbare Slots"),
    skip_deleted: bool = Query(default=True, description="Gelöschte Termine ausblenden"),
    refresh: bool = Query(default=False, description="Erzwingt Live-Abruf und aktualisiert den Cache"),
) -> KalenderResponse:
    clean_fl_id = fahrlehrer_id.strip()
    # Deterministic date normalization (prevents microsecond cache misses)
    effective_start = _normalize_date_str(start or start_datum, dt.date.today())
    effective_end = _normalize_date_str(end or end_datum, dt.date.today() + dt.timedelta(days=7))

    cache_key = f"kalender:{clean_fl_id}:{effective_start}:{effective_end}:{only_buchbar}:{skip_deleted}"
    force_refresh = refresh or request.headers.get("x-refresh-cache") == "1"

    if not force_refresh:
        cached_res, is_stale = await cache.get_or_stale(
            cache_key, stale_window=settings.CALENDAR_SWR_MAX_AGE_SECONDS
        )
        if cached_res is not None:
            if is_stale and cache_key not in _revalidating_keys:
                # Stale-While-Revalidate: Return cached response immediately and revalidate in background
                _revalidating_keys.add(cache_key)
                asyncio.create_task(
                    _revalidate_calendar_in_background(
                        cache_key=cache_key,
                        fahrlehrer_id=clean_fl_id,
                        effective_start=effective_start,
                        effective_end=effective_end,
                        only_buchbar=only_buchbar,
                        skip_deleted=skip_deleted,
                    )
                )
            response.headers["X-Cache-Hit"] = "1"
            return cached_res

    try:
        result = await _fetch_and_parse_calendar(
            fahrlehrer_id=clean_fl_id,
            effective_start=effective_start,
            effective_end=effective_end,
            only_buchbar=only_buchbar,
            skip_deleted=skip_deleted,
        )
        # Optimistic caching with configured TTL
        await cache.set(cache_key, result, ttl=settings.CALENDAR_CACHE_TTL_SECONDS)
        response.headers["X-Cache-Hit"] = "0"
        return result

    except (FsmException, HTTPException):
        raise
    except Exception as exc:
        logger.error("Fehler beim Abrufen des Kalenders für %s: %s", clean_fl_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Kalender-Abruf fehlgeschlagen: {exc}",
        )


@router.post(
    "/termine",
    response_model=TerminCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Termin oder Blocker anlegen",
    description="Erstellt einen neuen Termin/Blocker in FSM. Zeiträume > 600 Min. werden automatisch in Teilblöcke zerlegt.",
)
async def create_termin(payload: TerminCreateRequest) -> TerminCreateResponse:
    if payload.bis <= payload.von:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Das Enddatum ('bis') muss nach dem Startdatum ('von') liegen.",
        )

    clean_fl_id = payload.fahrlehrer_id.strip()
    try:
        created_ids = await fsm_client.create_termin(
            fahrlehrer_id=clean_fl_id,
            von=payload.von,
            bis=payload.bis,
            titel=payload.titel,
            leistungsart_id=payload.leistungsart_id,
            terminart=payload.terminart,
            schueler_id=payload.schueler_id,
            fahrzeug_id=payload.fahrzeug_id,
            gebucht=payload.gebucht,
        )

        if not created_ids:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="FSM hat keine Termin-ID zurückgegeben.",
            )

        # Invalidate calendar cache for this instructor immediately
        await cache.delete_prefix(f"kalender:{clean_fl_id}")
        await cache.delete_prefix(f"fsm:kalender:{clean_fl_id}")

        return TerminCreateResponse(
            success=True,
            created_ids=created_ids,
            count=len(created_ids),
        )

    except (FsmException, HTTPException):
        raise
    except Exception as exc:
        logger.error("Fehler beim Erstellen des Termins: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Terminerstellung fehlgeschlagen: {exc}",
        )


@router.put(
    "/termine/{termin_id}",
    response_model=TerminActionResponse,
    summary="Termin aktualisieren",
    description="Aktualisiert einen bestehenden Termin in FSM.",
)
async def update_termin(
    termin_id: str = Path(..., description="FSM Termin UUID"),
    payload: TerminUpdateRequest = ...,
) -> TerminActionResponse:
    clean_tid = termin_id.strip()
    clean_fl_id = payload.fahrlehrer_id.strip() if payload.fahrlehrer_id else None
    try:
        success = await fsm_client.update_termin(
            termin_id=clean_tid,
            fahrlehrer_id=clean_fl_id or "",
            von=payload.von,
            bis=payload.bis,
            titel=payload.titel,
            leistungsart_id=payload.leistungsart_id,
            terminart=payload.terminart,
            schueler_id=payload.schueler_id,
            fahrzeug_id=payload.fahrzeug_id,
            gebucht=payload.gebucht,
        )

        # Invalidate calendar cache
        if clean_fl_id:
            await cache.delete_prefix(f"kalender:{clean_fl_id}")
            await cache.delete_prefix(f"fsm:kalender:{clean_fl_id}")
        else:
            await cache.delete_prefix("kalender:")
            await cache.delete_prefix("fsm:kalender:")

        return TerminActionResponse(success=success, termin_id=clean_tid)
    except (FsmException, HTTPException):
        raise
    except Exception as exc:
        logger.error("Fehler beim Aktualisieren des Termins %s: %s", clean_tid, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Terminaktualisierung fehlgeschlagen: {exc}",
        )


@router.delete(
    "/termine/{termin_id}",
    response_model=TerminActionResponse,
    summary="Termin löschen",
    description="Löscht einen Termin anhand seiner FSM-UUID.",
)
async def delete_termin(
    termin_id: str = Path(..., description="FSM UUID des zu löschenden Termins"),
) -> TerminActionResponse:
    clean_tid = termin_id.strip()
    try:
        success = await fsm_client.delete_termin(termin_id=clean_tid)

        # Invalidate all calendar caches
        await cache.delete_prefix("kalender:")
        await cache.delete_prefix("fsm:kalender:")

        return TerminActionResponse(success=success, deleted_id=clean_tid)
    except (FsmException, HTTPException):
        raise
    except Exception as exc:
        logger.error("Fehler beim Löschen des Termins %s: %s", clean_tid, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Terminlöschung fehlgeschlagen: {exc}",
        )


@router.get(
    "/termine/tagesbelegung",
    response_model=TagesbelegungResponse,
    summary="Tagesbelegung aller Fahrlehrer abrufen",
    description="Liefert alle Termine aller Fahrlehrer für ein bestimmtes Tagesdatum.",
)
async def get_tagesbelegung(
    request: Request,
    response: Response,
    datum: str | None = Query(default=None, description="Tagesdatum im Format YYYY-MM-DD (Standard: heute)"),
    refresh: bool = Query(default=False, description="Cache überspringen"),
) -> TagesbelegungResponse:
    target_date = _normalize_date_str(datum, dt.date.today())
    force_refresh = refresh or request.headers.get("x-refresh-cache") == "1"
    cache_key = f"kalender:tagesbelegung:{target_date}"

    if not force_refresh:
        cached_res = await cache.get(cache_key)
        if cached_res is not None:
            response.headers["X-Cache-Hit"] = "1"
            return cached_res

    try:
        raw_list = await fsm_client.get_tagesbelegung(datum=target_date, fresh=force_refresh)
        events: list[KalenderEvent] = []
        for r in raw_list:
            if not isinstance(r, dict):
                continue
            tid = str(r.get("id") or "")
            if not tid:
                continue
            von_dt = _parse_iso_datetime(r.get("von") or r.get("start"))
            bis_dt = _parse_iso_datetime(r.get("bis") or r.get("end"))
            if not von_dt or not bis_dt:
                continue
            dauer = (bis_dt - von_dt).total_seconds() / 60.0

            ta = str(r.get("terminart") or "PX").upper()
            ist_fs = ta in ("FS", "FAHRSTUNDE") or bool(r.get("istFahrstunde", False))
            ist_th = ta in ("TH", "THEORIE") or bool(r.get("istTheorie", False))
            ist_block = ta in ("ST", "SPERRE", "URLAUB", "KRANK", "PAUSE") or bool(r.get("istBlocker", False))

            events.append(
                KalenderEvent(
                    id=tid,
                    von=von_dt,
                    bis=bis_dt,
                    fahrlehrer_id=str(r.get("fidFahrlehrer") or r.get("fahrlehrer_id") or ""),
                    terminart=ta,
                    titel=str(r.get("titel") or r.get("thema") or "Termin"),
                    schueler_name=r.get("kunde") or r.get("schueler_name"),
                    schueler_id=r.get("fidKunde") or r.get("schueler_id"),
                    fahrzeug_id=r.get("fidFahrzeug") or r.get("fahrzeug_id"),
                    gebucht=bool(r.get("gebucht", False)),
                    ist_fahrstunde=ist_fs,
                    ist_theorie=ist_th,
                    ist_blocker=ist_block,
                    dauer_minuten=dauer,
                )
            )

        result = TagesbelegungResponse(datum=target_date, count=len(events), termine=events)
        await cache.set(cache_key, result, ttl=settings.CACHE_TTL_SECONDS)
        response.headers["X-Cache-Hit"] = "0"
        return result
    except (FsmException, HTTPException):
        raise
    except Exception as exc:
        logger.error("Fehler beim Abrufen der Tagesbelegung für %s: %s", target_date, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Tagesbelegungs-Abruf fehlgeschlagen: {exc}",
        )

