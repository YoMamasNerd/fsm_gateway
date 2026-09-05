"""Course container management endpoints (e.g. theory courses)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Path, status

from app.core.client import FsmException, fsm_client
from app.schemas.kurse import KursActionResponse, KursCreateRequest, KursResponse

logger = logging.getLogger("fsm_gateway.api.kurse")
router = APIRouter(tags=["Kurse"])


@router.post(
    "/kurse",
    response_model=KursResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Kurs anlegen",
    description="Erstellt einen neuen Kurs-Container (z.B. Theoriekurs) in FSM.",
)
async def create_kurs(payload: KursCreateRequest) -> KursResponse:
    if payload.ende <= payload.beginn:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Das Kursende ('ende') muss nach dem Kursbeginn ('beginn') liegen.",
        )

    try:
        vm = await fsm_client.create_kurs(
            kennung=payload.kennung,
            bezeichnung=payload.bezeichnung,
            beginn=payload.beginn,
            ende=payload.ende,
            uhrzeit_von=payload.uhrzeit_von,
            uhrzeit_bis=payload.uhrzeit_bis,
            theoriegruppen=payload.theoriegruppen,
            filiale_id=payload.filiale_id,
            maximalteilnehmer=payload.maximalteilnehmer,
            ueberbuchung_moeglich=payload.ueberbuchung_moeglich,
            buchbar_bei_onlineanmeldung=payload.buchbar_bei_onlineanmeldung,
            fahrschule123=payload.fahrschule123,
        )

        if not vm.get("id"):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="FSM hat keine Kurs-ID zurückgegeben.",
            )

        return KursResponse(
            id=str(vm["id"]),
            kennung=vm.get("kennung"),
            bezeichnung=vm.get("bezeichnung"),
            beginn=vm.get("beginn"),
            ende=vm.get("ende"),
            theoriegruppen=vm.get("theoriegruppen") or [],
            filiale_id=vm.get("fidFiliale"),
            anzahl_teilnehmer=int(vm.get("anzahlTeilnehmer") or 0),
            maximalteilnehmer=vm.get("maximalteilnehmer"),
        )

    except (FsmException, HTTPException):
        raise
    except Exception as exc:
        logger.error("Fehler beim Erstellen des Kurses '%s': %s", payload.bezeichnung, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Kurserstellung fehlgeschlagen: {exc}",
        )


@router.delete(
    "/kurse/{kurs_id}",
    response_model=KursActionResponse,
    summary="Kurs löschen",
    description="Löscht einen Kurs-Container anhand seiner FSM-UUID.",
)
async def delete_kurs(
    kurs_id: str = Path(..., description="FSM UUID des zu löschenden Kurses"),
) -> KursActionResponse:
    clean_id = kurs_id.strip()
    try:
        success = await fsm_client.delete_kurs(kurs_id=clean_id)
        return KursActionResponse(success=success, deleted_id=clean_id)
    except (FsmException, HTTPException):
        raise
    except Exception as exc:
        logger.error("Fehler beim Löschen des Kurses %s: %s", clean_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Kurslöschung fehlgeschlagen: {exc}",
        )
