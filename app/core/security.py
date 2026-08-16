"""Security and optional Gateway API-Key verification dependency."""

import secrets
from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
http_bearer = HTTPBearer(auto_error=False)

EXEMPT_PATHS = {
    "/",
    "/metrics",
    "/docs",
    "/redoc",
    "/openapi.json",
}


async def verify_gateway_api_key(
    request: Request,
    header_key: str | None = Security(api_key_header),
    bearer_auth: HTTPAuthorizationCredentials | None = Security(http_bearer),
) -> bool:
    """Verify internal gateway API Key if configured in settings.

    If GATEWAY_API_KEY is empty, authentication is bypassed (trusted internal network).
    Exempts public/dashboard routes from API-Key requirement.
    """
    path = request.url.path
    if path in EXEMPT_PATHS or path.startswith("/dashboard"):
        return True

    configured_key = settings.GATEWAY_API_KEY.strip()
    if not configured_key:
        return True

    provided_key = header_key or (bearer_auth.credentials if bearer_auth else None)
    if not provided_key or not secrets.compare_digest(provided_key, configured_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ungültiger oder fehlender Gateway API-Key.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return True

