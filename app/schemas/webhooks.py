"""Webhook schemas for third-party integrations (e.g. SumUp)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SumUpWebhookEvent(BaseModel):
    """SumUp transaction webhook payload."""

    id: str | None = Field(default=None, description="Webhook Event ID")
    event_type: str | None = Field(default=None, description="Event type, e.g. TRANSACTION_SUCCESSFUL")
    event_time: str | None = Field(default=None, description="Timestamp of event")
    resource_id: str | None = Field(default=None, description="SumUp transaction code / resource ID")
    amount: float | None = Field(default=None, description="Transaction amount")
    currency: str | None = Field(default="EUR", description="Currency code")
    status: str | None = Field(default=None, description="Payment status (e.g. SUCCESSFUL, PAID)")
    description: str | None = Field(default=None, description="Description containing student name or reference")
    student_uuid: str | None = Field(default=None, description="Direct student UUID if provided")
    student_name: str | None = Field(default=None, description="Student name to search and match")
    extra: dict[str, Any] = Field(default_factory=dict, description="Raw metadata payload")


class SumUpWebhookResponse(BaseModel):
    """Response returned after processing webhook."""

    success: bool = Field(..., description="Processing status")
    action_taken: str = Field(..., description="Action summary (e.g. booked_payment, skipped, unmatched_student)")
    student_uuid: str | None = Field(default=None, description="Matched student UUID")
    betrag: float | None = Field(default=None, description="Amount booked")
    message: str = Field(..., description="Detailed message")
