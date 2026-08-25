"""Authentication API endpoints."""

import logging
from fastapi import APIRouter, HTTPException, Response, status

from app.core.client import FsmAuthError, FsmConfigError, fsm_client
from app.schemas.auth import AuthStatusResponse, LoginRequest, LoginResponse

logger = logging.getLogger("fsm_gateway.api.auth")
router = APIRouter(prefix="/auth", tags=["Authentifizierung"])


@router.get(
    "/status",
    response_model=AuthStatusResponse,
    summary="FSM Authentifizierungs-Status prüfen",
    description="Prüft, ob ein gültiges Token existiert und ob die Verbindung zur FSM-API steht.",
)
async def get_auth_status(response: Response) -> AuthStatusResponse:
    response.headers["X-Cache-Hit"] = "0"
    try:
        data = await fsm_client.get_auth_status()
        return AuthStatusResponse(**data)
    except Exception as exc:
        logger.error("Fehler beim Abrufen des Auth-Status: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Auth-Status Prüfung fehlgeschlagen: {exc}",
        )


@router.post(
    "/refresh",
    response_model=LoginResponse,
    summary="FSM Token neu generieren (Auto-Login)",
    description="Erzwingt einen erneuten Login-Handshake gegen FSM und aktualisiert das Bearer Token.",
)
async def refresh_token() -> LoginResponse:
    try:
        token = await fsm_client.auto_login()
        preview = f"{token[:10]}...{token[-5:]}" if token else None
        return LoginResponse(
            success=True,
            message="Token erfolgreich erneuert.",
            token_preview=preview,
        )
    except FsmConfigError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except FsmAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        )
    except Exception as exc:
        logger.error("Fehler beim Token-Refresh: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Login fehlgeschlagen: {exc}",
        )


@router.post(
    "/login",
    response_model=LoginResponse,
    summary="Manueller FSM Login mit optionalen Zugangsdaten",
    description="Führt Login mit den übergebenen oder konfigurierten Zugangsdaten durch.",
)
async def login(payload: LoginRequest) -> LoginResponse:
    try:
        token = await fsm_client.auto_login(email=payload.email, password=payload.password)
        preview = f"{token[:10]}...{token[-5:]}" if token else None
        return LoginResponse(
            success=True,
            message="Erfolgreich angemeldet.",
            token_preview=preview,
        )
    except FsmConfigError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except FsmAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        )
    except Exception as exc:
        logger.error("Fehler beim Login: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Login fehlgeschlagen: {exc}",
        )
