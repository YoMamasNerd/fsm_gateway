"""Calendar and appointment management endpoints."""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any
from fastapi import APIRouter, HTTPException, Path, Query, status

from app.core.client import FsmApiError, fsm_client
from app.schemas.kalender import (
    KalenderEvent,
    KalenderResponse,
    TerminActionResponse,
    TerminCreateRequest,
    TerminCreateResponse,
    TerminUpdateRequest,
)

logger = logging.getLogger("fsm_gateway.api.kalender")
router = APIRouter(tags=["Kalender & Termine"])


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


@router.get(
    "/kalender/{fahrlehrer_id}",
    response_model=KalenderResponse,
    summary="Kalender-Events eines Fahrlehrers abrufen",
    description="Liefert alle Fahrstunden, Theorie-Einheiten und Blocker für einen Zeitraum normalisiert zurück.",
)
async def get_kalender(
    fahrlehrer_id: str = Path(..., description="FSM UUID des Fahrlehrers"),
    start: str | None = Query(default=None, alias="von", description="Startdatum (z.B. 2026-08-16 oder ISO)"),
    end: str | None = Query(default=None, alias="bis", description="Enddatum (z.B. 2026-08-23 oder ISO)"),
    start_datum: str | None = Query(default=None, alias="startDatum", description="Kompatibilitätsalias"),
    end_datum: str | None = Query(default=None, alias="endDatum", description="Kompatibilitätsalias"),
    only_buchbar: bool = Query(default=False, description="Nur buchbare Slots"),
    skip_deleted: bool = Query(default=True, description="Gelöschte Termine ausblenden"),
) -> KalenderResponse:
    # Resolve parameters
    effective_start = start or start_datum or dt.date.today().isoformat()
    effective_end = end or end_datum or (dt.date.today() + dt.timedelta(days=7)).isoformat()

    try:
        raw_events = await fsm_client.get_kalender(
            fahrlehrer_id=fahrlehrer_id,
            start_date=effective_start,
            end_date=effective_end,
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

            # Extract student ID / vehicle ID if present
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

    except FsmApiError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    except Exception as exc:
        logger.error("Fehler beim Abrufen des Kalenders für %s: %s", fahrlehrer_id, exc)
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

    try:
        created_ids = await fsm_client.create_termin(
            fahrlehrer_id=payload.fahrlehrer_id,
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

        return TerminCreateResponse(
            success=True,
            created_ids=created_ids,
            count=len(created_ids),
        )

    except FsmApiError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
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
    try:
        success = await fsm_client.update_termin(
            termin_id=termin_id,
            fahrlehrer_id=payload.fahrlehrer_id,
            von=payload.von,
            bis=payload.bis,
            titel=payload.titel,
            leistungsart_id=payload.leistungsart_id,
            terminart=payload.terminart,
            schueler_id=payload.schueler_id,
            fahrzeug_id=payload.fahrzeug_id,
            gebucht=payload.gebucht,
        )
        return TerminActionResponse(success=success, termin_id=termin_id)
    except FsmApiError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    except Exception as exc:
        logger.error("Fehler beim Aktualisieren des Termins %s: %s", termin_id, exc)
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
    try:
        success = await fsm_client.delete_termin(termin_id=termin_id)
        return TerminActionResponse(success=success, deleted_id=termin_id)
    except FsmApiError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    except Exception as exc:
        logger.error("Fehler beim Löschen des Termins %s: %s", termin_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Terminlöschung fehlgeschlagen: {exc}",
        )
