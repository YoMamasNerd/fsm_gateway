"""Course container management endpoints (e.g. theory courses)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Path, status

from app.core.client import FsmException, fsm_client
from app.schemas.kurse import (
    KursActionResponse,
    KursCreateRequest,
    KursListResponse,
    KursResponse,
    KursteilnehmerAddRequest,
    KursteilnehmerAddResponse,
    KursteilnehmerItem,
    KursteilnehmerListResponse,
)

logger = logging.getLogger("fsm_gateway.api.kurse")
router = APIRouter(tags=["Kurse"])


def _map_kurs(data: dict) -> KursResponse:
    """Normalizes FSM's raw course fields (camelCase) into KursResponse."""
    return KursResponse(
        id=str(data["id"]),
        kennung=data.get("kennung"),
        bezeichnung=data.get("bezeichnung"),
        beginn=data.get("beginn"),
        ende=data.get("ende"),
        theoriegruppen=data.get("theoriegruppen") or [],
        filiale_id=data.get("fidFiliale"),
        anzahl_teilnehmer=int(data.get("anzahlTeilnehmer") or 0),
        maximalteilnehmer=data.get("maximalteilnehmer"),
    )


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

        return _map_kurs(vm)

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


@router.post(
    "/kurse/{kurs_id}/teilnehmer",
    response_model=KursteilnehmerAddResponse,
    summary="Kursteilnehmer hinzufügen",
    description="Fügt einen oder mehrere Schüler zu einem bestehenden Kurs in FSM hinzu.",
)
async def add_kursteilnehmer(
    payload: KursteilnehmerAddRequest,
    kurs_id: str = Path(..., description="FSM UUID des Kurses"),
) -> KursteilnehmerAddResponse:
    clean_kurs_id = kurs_id.strip()
    try:
        added = await fsm_client.add_kursteilnehmer(
            kurs_id=clean_kurs_id, schueler_ids=payload.schueler_ids
        )
        return KursteilnehmerAddResponse(
            success=True,
            kurs_id=clean_kurs_id,
            schueler_ids=added,
            added_count=len(added),
        )
    except (FsmException, HTTPException):
        raise
    except Exception as exc:
        logger.error("Fehler beim Hinzufügen von Teilnehmern zu Kurs %s: %s", clean_kurs_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Hinzufügen der Kursteilnehmer fehlgeschlagen: {exc}",
        )


@router.get(
    "/kurse",
    response_model=KursListResponse,
    summary="Kurse auflisten",
    description="Listet Kurs-Container aus FSM (Stammdaten, kein Tagesplan - siehe /kurse/{kurs_id}/theorietermine dafür).",
)
async def list_kurse(active: bool = True) -> KursListResponse:
    try:
        rows = await fsm_client.list_kurse(active=active)
        kurse = [_map_kurs(r) for r in rows if isinstance(r, dict) and r.get("id")]
        return KursListResponse(count=len(kurse), kurse=kurse)
    except (FsmException, HTTPException):
        raise
    except Exception as exc:
        logger.error("Fehler beim Auflisten der Kurse: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Kursliste fehlgeschlagen: {exc}",
        )


@router.get(
    "/kurse/{kurs_id}",
    response_model=KursResponse,
    summary="Kursdetails abrufen",
    description="Liefert die Stammdaten eines einzelnen Kurses.",
)
async def get_kurs(
    kurs_id: str = Path(..., description="FSM UUID des Kurses"),
) -> KursResponse:
    clean_id = kurs_id.strip()
    try:
        data = await fsm_client.get_kurs(clean_id)
        if not data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Kurs {clean_id} nicht gefunden.")
        return _map_kurs(data)
    except (FsmException, HTTPException):
        raise
    except Exception as exc:
        logger.error("Fehler beim Abrufen von Kurs %s: %s", clean_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Kursabruf fehlgeschlagen: {exc}",
        )


@router.get(
    "/kurse/{kurs_id}/teilnehmer",
    response_model=KursteilnehmerListResponse,
    summary="Kursteilnehmer auflisten",
    description="Listet die in FSM für einen Kurs eingetragenen Schüler.",
)
async def list_kursteilnehmer(
    kurs_id: str = Path(..., description="FSM UUID des Kurses"),
) -> KursteilnehmerListResponse:
    clean_id = kurs_id.strip()
    try:
        rows = await fsm_client.get_kurs_teilnehmer(clean_id)
        teilnehmer = [
            KursteilnehmerItem(
                id=str(r["id"]),
                vorname=r.get("vorname"),
                nachname=r.get("nachname"),
                klassen=r.get("klassen") or [],
            )
            for r in rows
            if isinstance(r, dict) and r.get("id")
        ]
        return KursteilnehmerListResponse(kurs_id=clean_id, count=len(teilnehmer), teilnehmer=teilnehmer)
    except (FsmException, HTTPException):
        raise
    except Exception as exc:
        logger.error("Fehler beim Abrufen der Kursteilnehmer für %s: %s", clean_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Kursteilnehmer-Abruf fehlgeschlagen: {exc}",
        )
