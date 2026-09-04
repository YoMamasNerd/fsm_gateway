"""Security and optional Gateway API-Key verification dependency."""

import ipaddress
import secrets

from fastapi import HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
http_bearer = HTTPBearer(auto_error=False)

# Paths that never require an API key (public assets & dashboard).
EXEMPT_PATHS = {
    "/",
    "/favicon.ico",
    "/favicon.svg",
    "/favicon.png",
    "/apple-touch-icon.png",
}

# Docs & metrics: reachable without API key only from private (docker) networks.
PRIVATE_ONLY_PATHS = {
    "/docs",
    "/redoc",
    "/openapi.json",
    "/metrics",
}

_PRIVATE_NETWORKS = [
    ipaddress.ip_network(net)
    for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8")
]


def _is_private_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in _PRIVATE_NETWORKS)


async def _require_private_network(request: Request) -> None:
    client = request.client.host if request.client else ""
    forwarded_for = request.headers.get("X-Forwarded-For")
    # Behind a reverse proxy the direct peer is the proxy (private); judge by
    # the original client IP instead so public visitors stay blocked.
    effective_ip = forwarded_for.split(",")[0].strip() if forwarded_for else client
    if not _is_private_ip(effective_ip):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Diese Ressource ist nur aus dem internen Netzwerk erreichbar.",
        )


async def verify_gateway_api_key(
    request: Request,
    header_key: str | None = Security(api_key_header),
    bearer_auth: HTTPAuthorizationCredentials | None = Security(http_bearer),
) -> bool:
    """Verify internal gateway API Key for all API routes.

    Exempts public assets and dashboard routes from the API-Key requirement.
    Docs and metrics are additionally restricted to private networks.
    """
    path = request.url.path
    if path in EXEMPT_PATHS or path.startswith("/dashboard") or path.startswith("/static"):
        return True

    # Public clients get nothing at all: docs, metrics and every API route
    # are internal-only. Only the dashboard (password-protected separately)
    # and static assets are reachable from outside.
    await _require_private_network(request)

    configured_key = settings.GATEWAY_API_KEY.strip()
    provided_key = header_key or (bearer_auth.credentials if bearer_auth else None)

    if not configured_key:
        # No key configured: internal callers are trusted.
        return True

    if not provided_key or not secrets.compare_digest(provided_key, configured_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ungültiger oder fehlender Gateway API-Key.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return True
