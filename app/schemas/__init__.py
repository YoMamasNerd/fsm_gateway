"""Schemas package export."""

from app.schemas.auth import AuthStatusResponse, LoginRequest, LoginResponse
from app.schemas.errors import (
    ClearErrorsResponse,
    ErrorEntry,
    ErrorsResponse,
    LastErrorResponse,
)
from app.schemas.fahrlehrer import FahrlehrerItem, FahrlehrerListResponse
from app.schemas.finanzen import (
    FahrstundeItem,
    FahrstundenResponse,
    LeistungenResponse,
    LeistungItem,
    ZahlungCreateRequest,
    ZahlungResponse,
)
from app.schemas.kalender import (
    KalenderEvent,
    KalenderResponse,
    TerminCreateRequest,
    TerminCreateResponse,
    TerminUpdateRequest,
)
from app.schemas.schueler import (
    SchuelerDetails,
    SchuelerKurzItem,
    SchuelerSucheRequest,
    SchuelerSucheResponse,
)
from app.schemas.webhooks import SumUpWebhookEvent, SumUpWebhookResponse

__all__ = [
    "AuthStatusResponse",
    "LoginRequest",
    "LoginResponse",
    "FahrlehrerItem",
    "FahrlehrerListResponse",
    "KalenderEvent",
    "KalenderResponse",
    "TerminCreateRequest",
    "TerminUpdateRequest",
    "TerminCreateResponse",
    "SchuelerSucheRequest",
    "SchuelerKurzItem",
    "SchuelerSucheResponse",
    "SchuelerDetails",
    "FahrstundeItem",
    "FahrstundenResponse",
    "LeistungItem",
    "LeistungenResponse",
    "ZahlungCreateRequest",
    "ZahlungResponse",
    "SumUpWebhookEvent",
    "SumUpWebhookResponse",
    "ClearErrorsResponse",
    "ErrorEntry",
    "ErrorsResponse",
    "LastErrorResponse",
]
