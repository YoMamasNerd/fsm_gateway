"""Webhook endpoints (e.g. SumUp payment automation)."""

from __future__ import annotations

import datetime as dt
import logging
from fastapi import APIRouter, HTTPException, status

from app.core.cache import cache
from app.core.client import fsm_client
from app.schemas.webhooks import SumUpWebhookEvent, SumUpWebhookResponse

logger = logging.getLogger("fsm_gateway.api.webhooks")
router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


@router.post(
    "/sumup",
    response_model=SumUpWebhookResponse,
    summary="SumUp Webhook Empfänger",
    description="Empfängt SumUp Kartenzahlungen und bucht sie idempotent im FSM-Leistungskonto des Schülers ein.",
)
async def handle_sumup_webhook(payload: SumUpWebhookEvent) -> SumUpWebhookResponse:
    event_key = payload.resource_id or payload.id
    logger.info("SumUp Webhook empfangen: Key=%s, Typ=%s, Betrag=%s", event_key, payload.event_type, payload.amount)

    # 1. Idempotency Check: Prevent duplicate payment bookings
    if event_key:
        idempotency_cache_key = f"fsm:webhook:processed:{event_key}"
        already_processed = await cache.get(idempotency_cache_key)
        if already_processed:
            logger.info("SumUp Webhook %s wurde bereits verarbeitet. Überspringe erneute Buchung.", event_key)
            return SumUpWebhookResponse(
                success=True,
                action_taken="already_processed",
                student_uuid=already_processed.get("student_uuid"),
                betrag=already_processed.get("betrag"),
                message="Zahlung wurde bereits erfolgreich verbucht (Idempotenz).",
            )

    amount = payload.amount
    if not amount or amount <= 0:
        return SumUpWebhookResponse(
            success=False,
            action_taken="skipped_no_amount",
            message="Webhook enthält keinen gültigen Zahlungsbetrag (> 0).",
        )

    # 1. Resolve student UUID
    target_student_uuid = payload.student_uuid
    matched_student_name = payload.student_name

    if not target_student_uuid:
        search_query = payload.student_name or payload.description
        if search_query:
            try:
                search_res = await fsm_client.search_schueler(query=search_query, only_active=True, count=5)
                rows = search_res.get("rows", [])
                if len(rows) == 1:
                    data = rows[0].get("data", rows[0])
                    target_student_uuid = data.get("id")
                    matched_student_name = f"{data.get('vorname', '')} {data.get('nachname', '')}".strip()
                elif len(rows) > 1:
                    # Ambiguous match
                    return SumUpWebhookResponse(
                        success=False,
                        action_taken="ambiguous_student_match",
                        message=f"Mehrere Schüler ({len(rows)}) für '{search_query}' gefunden. Automatische Buchung abgebrochen.",
                    )
            except Exception as exc:
                logger.error("Fehler bei automatischer Schülersuche für Webhook: %s", exc)

    if not target_student_uuid:
        return SumUpWebhookResponse(
            success=False,
            action_taken="unmatched_student",
            betrag=amount,
            message="Kein passender Schüler anhand der SumUp-Zahlungsdaten gefunden.",
        )

    # 2. Book payment into FSM
    try:
        booking_text = f"SumUp Zahlung ({payload.resource_id or payload.id or 'Webhook'})"
        if payload.description:
            booking_text = f"SumUp: {payload.description}"

        await fsm_client.create_zahlung(
            student_uuid=target_student_uuid,
            betrag=float(amount),
            datum=payload.event_time or dt.date.today().isoformat(),
            zahlungsart="SumUp / Kartenzahlung",
            text=booking_text,
            belegnummer=payload.resource_id or payload.id,
        )

        # Mark as processed in cache for 48 hours (172800s)
        if event_key:
            await cache.set(
                f"fsm:webhook:processed:{event_key}",
                {"student_uuid": target_student_uuid, "betrag": amount},
                ttl=172800,
            )

        # Invalidate student cache
        await cache.delete_prefix(f"schueler:leistungen:{target_student_uuid}")
        await cache.delete_prefix(f"schueler:details:{target_student_uuid}")

        return SumUpWebhookResponse(
            success=True,
            action_taken="booked_payment",
            student_uuid=target_student_uuid,
            betrag=amount,
            message=f"Zahlung über {amount:.2f} € für Schüler {matched_student_name or target_student_uuid} erfolgreich in FSM eingebucht.",
        )
    except Exception as exc:
        logger.error("Fehler beim automatischen Einbuchen der SumUp-Zahlung: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Automatisches Einbuchen fehlgeschlagen: {exc}",
        )
