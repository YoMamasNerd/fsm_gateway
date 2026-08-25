"""Student management and search API endpoints."""

from __future__ import annotations

import logging
from typing import Any
from fastapi import APIRouter, HTTPException, Path, Query, Request, Response, status

from app.core.cache import cache
from app.core.config import settings
from app.core.client import FsmException, fsm_client
from app.schemas.ausbildung import AusbildungItem, AusbildungListResponse, KarteikarteResponse
from app.schemas.schueler import (
    SchuelerDetails,
    SchuelerKurzItem,
    SchuelerSucheRequest,
    SchuelerSucheResponse,
)
from app.schemas.preislisten import PreispositionItem, PreispositionenResponse
from app.schemas.theorie import (
    TheoriestundeCreateRequest,
    TheoriestundeItem,
    TheoriestundenResponse,
    TheoriestundeVorlageResponse,
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


@router.get(
    "/{student_uuid}/ausbildung",
    response_model=AusbildungListResponse,
    summary="Ausbildungsstand & Sonderfahrten-Zähler abrufen",
    description="Liefert alle Ausbildungen/Klassen des Schülers inkl. Sonderfahrten-Zählern (Übungsfahrten, Überland, Autobahn, Nacht, Unterweisungen) und Prüfungsstatus.",
)
async def get_schueler_ausbildung(
    request: Request,
    response: Response,
    student_uuid: str = Path(..., description="FSM Schüler-UUID"),
    refresh: bool = Query(default=False, description="Erzwingt Live-Abruf"),
) -> AusbildungListResponse:
    clean_uuid = student_uuid.strip()
    cache_key = f"schueler:ausbildung:{clean_uuid}"
    force_refresh = refresh or request.headers.get("x-refresh-cache") == "1"

    if not force_refresh:
        cached_res = await cache.get(cache_key)
        if cached_res is not None:
            response.headers["X-Cache-Hit"] = "1"
            return cached_res

    try:
        raw_list = await fsm_client.get_ausbildungen(student_uuid=clean_uuid, fresh=force_refresh)
        items: list[AusbildungItem] = []
        for r in raw_list:
            if not isinstance(r, dict):
                continue
            ueb = _parse_german_number(r.get("uebungsfahrten")) or 0.0
            uel = _parse_german_number(r.get("ueberlandfahrten")) or 0.0
            ab = _parse_german_number(r.get("autobahnfahrten")) or 0.0
            nf = _parse_german_number(r.get("nachtfahrten")) or 0.0
            unt = _parse_german_number(r.get("unterweisungen")) or 0.0
            sonst = _parse_german_number(r.get("sonstige_stunden")) or 0.0
            gesamt = ueb + uel + ab + nf + unt + sonst

            items.append(
                AusbildungItem(
                    id=str(r.get("id") or clean_uuid),
                    fidklasse=r.get("fidklasse"),
                    klasse_name=r.get("klasse") or r.get("klasse_name"),
                    fidschueler=r.get("fidschueler") or clean_uuid,
                    lfdnr=r.get("lfdnr") or 1,
                    uebungsfahrten=ueb,
                    ueberlandfahrten=uel,
                    autobahnfahrten=ab,
                    nachtfahrten=nf,
                    unterweisungen=unt,
                    sonstige_stunden=sonst,
                    gesamt_fahrstunden=gesamt,
                    theoriestunden=_parse_german_number(r.get("theoriestunden")) or 0.0,
                    pflicht_theoriestunden=_parse_german_number(r.get("pflicht_theoriestunden")),
                    theoriepruefungen=r.get("theoriepruefungen"),
                    praxispruefungen=r.get("praxispruefungen"),
                    datum_theoriepruefung=r.get("datum_theoriepruefung"),
                    datum_praxispruefung=r.get("datum_praxispruefung"),
                    fidergebnis_theorie=r.get("fidergebnis_theorie"),
                    fidergebnis_praxis=r.get("fidergebnis_praxis"),
                    bestanden_theorie=bool(r.get("bestanden_theorie", False) or str(r.get("fidergebnis_theorie") or "").lower() == "bestanden"),
                    bestanden_praxis=bool(r.get("bestanden_praxis", False) or str(r.get("fidergebnis_praxis") or "").lower() == "bestanden"),
                )
            )

        result = AusbildungListResponse(count=len(items), student_uuid=clean_uuid, ausbildungen=items)
        await cache.set(cache_key, result, ttl=settings.AUSBILDUNG_CACHE_TTL_SECONDS)
        response.headers["X-Cache-Hit"] = "0"
        return result

    except (FsmException, HTTPException):
        raise
    except Exception as exc:
        logger.error("Fehler beim Abrufen der Ausbildung für %s: %s", clean_uuid, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ausbildungsabruf fehlgeschlagen: {exc}",
        )


@router.get(
    "/{student_uuid}/kartei",
    response_model=KarteikarteResponse,
    summary="Digitale Karteikarte abrufen",
    description="Liefert zugewiesene Fahrlehrer, Prüfauftrag / Rücklaufstatus vom TÜV/DEKRA und Prüfungssprache.",
)
async def get_schueler_karteikarte(
    request: Request,
    response: Response,
    student_uuid: str = Path(..., description="FSM Schüler-UUID"),
    refresh: bool = Query(default=False, description="Erzwingt Live-Abruf"),
) -> KarteikarteResponse:
    clean_uuid = student_uuid.strip()
    cache_key = f"schueler:kartei:{clean_uuid}"
    force_refresh = refresh or request.headers.get("x-refresh-cache") == "1"

    if not force_refresh:
        cached_res = await cache.get(cache_key)
        if cached_res is not None:
            response.headers["X-Cache-Hit"] = "1"
            return cached_res

    try:
        raw = await fsm_client.get_karteikarte(student_uuid=clean_uuid, fresh=force_refresh)
        ausb_raw = raw.get("ausbildungen", [])
        ausb_items: list[AusbildungItem] = []
        if isinstance(ausb_raw, list):
            for r in ausb_raw:
                if isinstance(r, dict):
                    ueb = _parse_german_number(r.get("uebungsfahrten")) or 0.0
                    uel = _parse_german_number(r.get("ueberlandfahrten")) or 0.0
                    ab = _parse_german_number(r.get("autobahnfahrten")) or 0.0
                    nf = _parse_german_number(r.get("nachtfahrten")) or 0.0
                    unt = _parse_german_number(r.get("unterweisungen")) or 0.0
                    sonst = _parse_german_number(r.get("sonstige_stunden")) or 0.0
                    ausb_items.append(
                        AusbildungItem(
                            id=str(r.get("id") or clean_uuid),
                            fidklasse=r.get("fidklasse"),
                            klasse_name=r.get("klasse") or r.get("klasse_name"),
                            fidschueler=r.get("fidschueler") or clean_uuid,
                            lfdnr=r.get("lfdnr") or 1,
                            uebungsfahrten=ueb,
                            ueberlandfahrten=uel,
                            autobahnfahrten=ab,
                            nachtfahrten=nf,
                            unterweisungen=unt,
                            sonstige_stunden=sonst,
                            gesamt_fahrstunden=ueb + uel + ab + nf + unt + sonst,
                            theoriestunden=_parse_german_number(r.get("theoriestunden")) or 0.0,
                            pflicht_theoriestunden=_parse_german_number(r.get("pflicht_theoriestunden")),
                            theoriepruefungen=r.get("theoriepruefungen"),
                            praxispruefungen=r.get("praxispruefungen"),
                            datum_theoriepruefung=r.get("datum_theoriepruefung"),
                            datum_praxispruefung=r.get("datum_praxispruefung"),
                            fidergebnis_theorie=r.get("fidergebnis_theorie"),
                            fidergebnis_praxis=r.get("fidergebnis_praxis"),
                            bestanden_theorie=bool(r.get("bestanden_theorie", False)),
                            bestanden_praxis=bool(r.get("bestanden_praxis", False)),
                        )
                    )

        result = KarteikarteResponse(
            student_uuid=clean_uuid,
            fidFahrlehrer1=raw.get("fidFahrlehrer1"),
            fahrlehrer1=raw.get("fahrlehrer1"),
            fidFahrlehrer2=raw.get("fidFahrlehrer2"),
            fahrlehrer2=raw.get("fahrlehrer2"),
            pflichttheoriestunden=int(raw.get("pflichttheoriestunden") or 0),
            theoriestunden=_parse_german_number(raw.get("theoriestunden")) or 0.0,
            ruecklauf_datum=raw.get("ruecklauf_datum"),
            ruecklaufnummer=raw.get("ruecklaufnummer"),
            pruefungssprache=raw.get("fidsystempruefungssprache") or "DEU",
            ausbildungen=ausb_items,
        )

        await cache.set(cache_key, result, ttl=settings.AUSBILDUNG_CACHE_TTL_SECONDS)
        response.headers["X-Cache-Hit"] = "0"
        return result

    except (FsmException, HTTPException):
        raise
    except Exception as exc:
        logger.error("Fehler beim Abrufen der Karteikarte für %s: %s", clean_uuid, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Karteikartenabruf fehlgeschlagen: {exc}",
        )


@router.get(
    "/{student_uuid}/theorie",
    response_model=TheoriestundenResponse,
    summary="Besuchte Theoriestunden abrufen",
    description="Liefert alle vom Schüler besuchten Theoriestunden mit Datum, Thema/Kapitel und Lehrkraft.",
)
async def get_schueler_theorie(
    request: Request,
    response: Response,
    student_uuid: str = Path(..., description="FSM Schüler-UUID"),
    refresh: bool = Query(default=False, description="Erzwingt Live-Abruf"),
) -> TheoriestundenResponse:
    clean_uuid = student_uuid.strip()
    cache_key = f"schueler:theorie:{clean_uuid}"
    force_refresh = refresh or request.headers.get("x-refresh-cache") == "1"

    if not force_refresh:
        cached_res = await cache.get(cache_key)
        if cached_res is not None:
            response.headers["X-Cache-Hit"] = "1"
            return cached_res

    try:
        raw_list = await fsm_client.get_theoriestunden(student_uuid=clean_uuid, fresh=force_refresh)
        items: list[TheoriestundeItem] = []
        for r in raw_list:
            if not isinstance(r, dict):
                continue
            tid = str(r.get("id") or r.get("fidTheoriestunde") or "")
            if not tid:
                continue
            items.append(
                TheoriestundeItem(
                    id=tid,
                    datum=r.get("datum") or r.get("Datum"),
                    thema=r.get("thema") or r.get("kapitel") or r.get("Thema"),
                    lehrer_name=r.get("lehrer") or r.get("fahrlehrer_Name") or r.get("Lehrer"),
                    filiale=r.get("filiale") or r.get("Filiale"),
                    dauer_minuten=_parse_german_number(r.get("dauer") or r.get("dauer_minuten")) or 90.0,
                    storno=bool(r.get("storno", False)),
                )
            )

        result = TheoriestundenResponse(count=len(items), student_uuid=clean_uuid, theoriestunden=items)
        await cache.set(cache_key, result, ttl=settings.THEORIE_CACHE_TTL_SECONDS)
        response.headers["X-Cache-Hit"] = "0"
        return result

    except (FsmException, HTTPException):
        raise
    except Exception as exc:
        logger.error("Fehler beim Abrufen der Theoriestunden für %s: %s", clean_uuid, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Theorieabruf fehlgeschlagen: {exc}",
        )


@router.get(
    "/{student_uuid}/theorie/vorlage",
    response_model=TheoriestundeVorlageResponse,
    summary="Vorlage zur Theorieunterricht-Erfassung abrufen",
    description="Liefert die von FSM vorausgefüllte Erfassungsvorlage mit Schülername, Standard-Filiale und Fahrlehrer.",
)
async def get_theorie_vorlage(
    response: Response,
    student_uuid: str = Path(..., description="FSM Schüler-UUID"),
) -> TheoriestundeVorlageResponse:
    clean_uuid = student_uuid.strip()
    try:
        raw = await fsm_client.get_theoriestunde_vorlage(student_uuid=clean_uuid)
        response.headers["X-Cache-Hit"] = "0"
        return TheoriestundeVorlageResponse(
            fidKunde=raw.get("fidKunde") or clean_uuid,
            kunde=raw.get("kunde") or "Schüler",
            fidfiliale=raw.get("fidfiliale"),
            filiale=raw.get("filiale"),
            fidFahrlehrer=raw.get("fidFahrlehrer"),
            fahrlehrer=raw.get("fahrlehrer"),
            fidSystemtheoriegruppe=raw.get("fidSystemtheoriegruppe") or "*",
            von=raw.get("von"),
            bis=raw.get("bis"),
            minuten=float(raw.get("minuten") or 90.0),
            datum=raw.get("datum"),
        )
    except (FsmException, HTTPException):
        raise
    except Exception as exc:
        logger.error("Fehler beim Abrufen der Theorie-Vorlage für %s: %s", clean_uuid, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Theorie-Vorlage fehlgeschlagen: {exc}",
        )


@router.post(
    "/{student_uuid}/theorie",
    summary="Theorieunterricht für Schüler eintragen / buchen",
    description="Erfasst eine besuchte oder gebuchte Theoriestunde für einen Schüler in der FSM Cloud und invalidiert automatisch den Ausbildungs- und Theoriecache.",
)
async def create_schueler_theoriestunde(
    payload: TheoriestundeCreateRequest,
    student_uuid: str = Path(..., description="FSM Schüler-UUID"),
) -> dict[str, Any]:
    clean_uuid = student_uuid.strip()
    try:
        # Schülerdetails holen um den Namen sicherzustellen
        details = await fsm_client.get_schueler_details(student_uuid=clean_uuid)
        student_name = "Schüler"
        if isinstance(details, dict):
            vn = (details.get("vorname") or "").strip()
            nn = (details.get("nachname") or "").strip()
            student_name = f"{vn} {nn}".strip() or str(details.get("name") or "Schüler")

        res = await fsm_client.create_theoriestunde(
            student_uuid=clean_uuid,
            student_name=student_name,
            filiale_id=payload.fidfiliale,
            filiale_name=payload.filiale,
            fahrlehrer_id=payload.fidFahrlehrer,
            fahrlehrer_name=payload.fahrlehrer,
            systemtheoriegruppe=payload.fidSystemtheoriegruppe,
            kapitel=payload.kapitel,
            datum=payload.datum,
            von=payload.von,
            bis=payload.bis,
            minuten=payload.minuten,
        )
        return {"success": True, "student_uuid": clean_uuid, "result": res}
    except (FsmException, HTTPException):
        raise
    except Exception as exc:
        logger.error("Fehler beim Erfassen der Theoriestunde für %s: %s", clean_uuid, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Theorie-Erfassung fehlgeschlagen: {exc}",
        )


@router.get(
    "/{student_uuid}/preise",
    response_model=PreispositionenResponse,
    summary="Schüler-Preisliste abrufen (Read-Only)",
    description="Liefert alle für den Schüler hinterlegten Einzelpreise (Sonderpreise, Grundgebühr, Fahrstundenpreise).",
)
async def get_schueler_preise(
    request: Request,
    response: Response,
    student_uuid: str = Path(..., description="FSM Schüler-UUID"),
    refresh: bool = Query(default=False, description="Cache überspringen"),
) -> PreispositionenResponse:
    clean_uuid = student_uuid.strip()
    force_refresh = refresh or request.headers.get("x-refresh-cache") == "1"
    cache_key = f"schueler:preise:{clean_uuid}"

    if not force_refresh:
        cached_res = await cache.get(cache_key)
        if cached_res is not None:
            response.headers["X-Cache-Hit"] = "1"
            return cached_res

    try:
        raw_list = await fsm_client.get_schueler_preisliste(student_uuid=clean_uuid, fresh=force_refresh)
        items: list[PreispositionItem] = []
        for r in raw_list:
            if not isinstance(r, dict):
                continue
            pos_id = str(r.get("id") or "")
            if not pos_id:
                continue
            betrag_val = r.get("betrag")
            betrag_float = float(betrag_val) if isinstance(betrag_val, (int, float)) else 0.0

            items.append(
                PreispositionItem(
                    id=pos_id,
                    fidPreisliste=r.get("fidPreisliste"),
                    bezeichnung=r.get("bezeichnung") or "Schülerpreis",
                    betrag=betrag_float,
                    klasse=r.get("klasse"),
                    theorie=bool(r.get("theorie", False)),
                    praxis=bool(r.get("praxis", False)),
                    fidleistungsart=r.get("fidleistungsart"),
                    artikel=r.get("artikel"),
                )
            )

        result = PreispositionenResponse(count=len(items), preisliste_id=clean_uuid, preispositionen=items)
        await cache.set(cache_key, result, ttl=settings.STAMMDATEN_CACHE_TTL_SECONDS)
        response.headers["X-Cache-Hit"] = "0"
        return result
    except (FsmException, HTTPException):
        raise
    except Exception as exc:
        logger.error("Fehler beim Abrufen der Preise für %s: %s", clean_uuid, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Preise-Abruf fehlgeschlagen: {exc}",
        )
