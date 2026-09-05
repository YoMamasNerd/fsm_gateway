"""Course-level theory schedule endpoints (Theorietermine - the day/topic plan
of a course, as opposed to a student's individual attendance record which is
handled under /schueler/{id}/theorie)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Path, status

from app.core.client import FsmException, fsm_client
from app.schemas.theorietermin import (
    TheorieterminActionResponse,
    TheorieterminBulkCreateRequest,
    TheorieterminBulkCreateResponse,
    TheorieterminItem,
    TheorieterminListResponse,
    TheorieterminUpdateRequest,
)

logger = logging.getLogger("fsm_gateway.api.theorietermine")
router = APIRouter(tags=["Theorietermine"])


def _map_termin(data: dict) -> TheorieterminItem:
    return TheorieterminItem(
        id=str(data["id"]),
        kurs_id=data.get("fidKurs"),
        von=data.get("von"),
        bis=data.get("bis"),
        kapitel=data.get("kapitel"),
        fahrlehrer_ids=data.get("fidFahrlehrer") or [],
        systemtheoriegruppe=data.get("fidSystemtheoriegruppe"),
    )


@router.get(
    "/kurse/{kurs_id}/theorietermine",
    response_model=TheorieterminListResponse,
    summary="Tagesplan eines Kurses abrufen",
    description="Liefert die geplanten Theorietermine (Datum, Uhrzeit, Kapitel) eines Kurses.",
)
async def list_theorietermine(
    kurs_id: str = Path(..., description="FSM UUID des Kurses"),
) -> TheorieterminListResponse:
    clean_id = kurs_id.strip()
    try:
        rows = await fsm_client.get_kurs_termine(clean_id)
        termine = [_map_termin(r) for r in rows if isinstance(r, dict) and r.get("id")]
        return TheorieterminListResponse(kurs_id=clean_id, count=len(termine), termine=termine)
    except (FsmException, HTTPException):
        raise
    except Exception as exc:
        logger.error("Fehler beim Abrufen des Tagesplans für Kurs %s: %s", clean_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Tagesplan-Abruf fehlgeschlagen: {exc}",
        )


@router.post(
    "/kurse/{kurs_id}/theorietermine",
    response_model=TheorieterminBulkCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Tagesplan eines Kurses anlegen",
    description="Legt einen oder mehrere Theorietermine (Tag + Thema) für einen Kurs in FSM an.",
)
async def create_theorietermine(
    payload: TheorieterminBulkCreateRequest,
    kurs_id: str = Path(..., description="FSM UUID des Kurses"),
) -> TheorieterminBulkCreateResponse:
    clean_id = kurs_id.strip()
    for item in payload.termine:
        if item.bis <= item.von:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Termin '{item.kapitel}': 'bis' muss nach 'von' liegen.",
            )

    try:
        rows = await fsm_client.create_theorietermine_bulk(
            kurs_id=clean_id,
            termine=[item.model_dump() for item in payload.termine],
        )
        termine = [_map_termin(r) for r in rows if isinstance(r, dict) and r.get("id")]
        return TheorieterminBulkCreateResponse(
            success=True, kurs_id=clean_id, created_count=len(termine), termine=termine
        )
    except (FsmException, HTTPException):
        raise
    except Exception as exc:
        logger.error("Fehler beim Anlegen des Tagesplans für Kurs %s: %s", clean_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Tagesplan-Erstellung fehlgeschlagen: {exc}",
        )


@router.put(
    "/theorietermine/{termin_id}",
    response_model=TheorieterminActionResponse,
    summary="Theorietermin aktualisieren",
    description="Aktualisiert Datum, Uhrzeit, Kapitel oder Fahrlehrer eines einzelnen Theorietermins.",
)
async def update_theorietermin(
    payload: TheorieterminUpdateRequest,
    termin_id: str = Path(..., description="FSM UUID des Theorietermins"),
) -> TheorieterminActionResponse:
    clean_id = termin_id.strip()
    try:
        await fsm_client.update_theorietermin(
            termin_id=clean_id,
            von=payload.von,
            bis=payload.bis,
            kapitel=payload.kapitel,
            fahrlehrer_id=payload.fahrlehrer_id,
        )
        return TheorieterminActionResponse(success=True, termin_id=clean_id)
    except (FsmException, HTTPException):
        raise
    except Exception as exc:
        logger.error("Fehler beim Aktualisieren von Theorietermin %s: %s", clean_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Theorietermin-Aktualisierung fehlgeschlagen: {exc}",
        )


@router.delete(
    "/theorietermine/{termin_id}",
    response_model=TheorieterminActionResponse,
    summary="Theorietermin löschen",
    description="Löscht einen einzelnen Theorietermin anhand seiner FSM-UUID.",
)
async def delete_theorietermin(
    termin_id: str = Path(..., description="FSM UUID des zu löschenden Theorietermins"),
) -> TheorieterminActionResponse:
    clean_id = termin_id.strip()
    try:
        success = await fsm_client.delete_theorietermin(termin_id=clean_id)
        return TheorieterminActionResponse(success=success, deleted_id=clean_id)
    except (FsmException, HTTPException):
        raise
    except Exception as exc:
        logger.error("Fehler beim Löschen von Theorietermin %s: %s", clean_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Theorietermin-Löschung fehlgeschlagen: {exc}",
        )
