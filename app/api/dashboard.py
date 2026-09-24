"""Dashboard Web UI, Statistics API, and Prometheus /metrics endpoint."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
import urllib.parse
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Cookie, Header, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from pydantic import BaseModel

from app.core.cache import CACHE_CATEGORY_LABELS, cache
from app.core.client import fsm_client
from app.core.config import settings
from app.core.icons import icon, substitute
from app.core.metrics import metrics_collector

router = APIRouter(tags=["Monitoring & Dashboard"])

# Cookie name for dashboard authentication session
SESSION_COOKIE_NAME = "fsm_dash_auth"
SESSION_SECRET = settings.VOIDAUTH_CLIENT_SECRET or settings.DASHBOARD_PASSWORD or "fsm-gateway-dash-secret"


def _generate_session_token(password: str) -> str:
    """Generates an HMAC session token derived from the dashboard password."""
    secret = settings.DASHBOARD_PASSWORD or "fsm-gateway-default-key"
    ts = str(int(time.time() // 86400))  # Valid for the day
    return hmac.new(secret.encode(), ts.encode(), hashlib.sha256).hexdigest()


def _generate_sso_session_token(sub: str, username: str) -> str:
    """Generates a signed, tamper-proof session token for an authenticated SSO user."""
    ts_str = str(int(time.time()))
    payload = f"sso:{sub}:{username}:{ts_str}"
    sig = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    token_str = f"{payload}:{sig}"
    return base64.urlsafe_b64encode(token_str.encode()).decode()


def _verify_sso_session_token(token: str) -> bool:
    """Validates an SSO session token and ensures it hasn't expired (valid for 7 days)."""
    try:
        raw = base64.urlsafe_b64decode(token.encode()).decode()
        parts = raw.split(":")
        if len(parts) != 5 or parts[0] != "sso":
            return False
        sub, username, ts_str, sig = parts[1], parts[2], parts[3], parts[4]
        ts = int(ts_str)
        if time.time() - ts > 86400 * 7:
            return False
        payload = f"sso:{sub}:{username}:{ts_str}"
        expected_sig = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, expected_sig)
    except Exception:
        return False


def _is_authenticated(
    cookie_token: str | None = None,
    auth_header: str | None = None,
) -> bool:
    """Checks if the user is authorized to access the dashboard."""
    # If neither password nor VoidAuth SSO is configured, dashboard is open
    if not settings.DASHBOARD_PASSWORD and not settings.VOIDAUTH_ENABLED:
        return True

    # 1. Check SSO signed session cookie
    if cookie_token and _verify_sso_session_token(cookie_token):
        return True

    # 2. Check password-derived cookie
    if settings.DASHBOARD_PASSWORD:
        expected_token = _generate_session_token(settings.DASHBOARD_PASSWORD)
        if cookie_token and hmac.compare_digest(cookie_token, expected_token):
            return True

        # 3. Check HTTP Basic Auth header
        if auth_header and auth_header.startswith("Basic "):
            try:
                encoded = auth_header.split(" ", 1)[1]
                decoded = base64.b64decode(encoded).decode("utf-8")
                _, password = decoded.split(":", 1)
                if hmac.compare_digest(password, settings.DASHBOARD_PASSWORD):
                    return True
            except Exception:
                pass

    return False


class LoginRequest(BaseModel):
    password: str


@router.get("/dashboard/auth/sso/login", summary="Initiate VoidAuth SSO Login for Dashboard")
async def dashboard_sso_login(request: Request, next: str | None = None) -> RedirectResponse:
    """Redirects the browser to VoidAuth OIDC authorization endpoint."""
    if not settings.VOIDAUTH_ENABLED:
        raise HTTPException(status_code=400, detail="VoidAuth SSO ist nicht konfiguriert.")

    state = secrets.token_urlsafe(24)
    redirect_uri = settings.VOIDAUTH_REDIRECT_URI.strip()
    if not redirect_uri:
        proto = request.headers.get("x-forwarded-proto", request.url.scheme)
        host = request.headers.get("x-forwarded-host") or request.headers.get("host", "localhost:8090")
        redirect_uri = f"{proto}://{host}/dashboard/auth/sso/callback"

    issuer = settings.VOIDAUTH_ISSUER_URL.rstrip("/")
    auth_url = (
        f"{issuer}/auth?client_id={settings.VOIDAUTH_CLIENT_ID}"
        f"&redirect_uri={redirect_uri}"
        f"&response_type=code"
        f"&scope=openid+profile+email+groups"
        f"&state={state}"
    )
    response = RedirectResponse(url=auth_url, status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        key="fsm_dash_sso_state",
        value=state,
        httponly=True,
        samesite="lax",
        max_age=600,  # 10 minutes
    )
    if next and next.startswith("/dashboard"):
        response.set_cookie(
            key="fsm_dash_sso_next",
            value=next,
            httponly=True,
            samesite="lax",
            max_age=600,
        )
    return response


@router.get("/dashboard/auth/sso/callback", summary="VoidAuth SSO Callback")
async def dashboard_sso_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    fsm_dash_sso_state: str | None = Cookie(None),
    fsm_dash_sso_next: str | None = Cookie(None),
) -> Response:
    """Handles authorization code exchange with VoidAuth and signs in user."""
    if not settings.VOIDAUTH_ENABLED:
        raise HTTPException(status_code=400, detail="VoidAuth SSO ist nicht konfiguriert.")

    if error:
        raise HTTPException(status_code=400, detail=f"SSO Fehler: {error} - {error_description}")

    if not code or not state or not fsm_dash_sso_state or not secrets.compare_digest(state, fsm_dash_sso_state):
        raise HTTPException(status_code=400, detail="Ungültiger SSO-Status oder abgelaufene Sitzung.")

    redirect_uri = settings.VOIDAUTH_REDIRECT_URI.strip()
    if not redirect_uri:
        proto = request.headers.get("x-forwarded-proto", request.url.scheme)
        host = request.headers.get("x-forwarded-host") or request.headers.get("host", "localhost:8090")
        redirect_uri = f"{proto}://{host}/dashboard/auth/sso/callback"

    issuer = settings.VOIDAUTH_ISSUER_URL.rstrip("/")
    token_url = f"{issuer}/token"

    user_sub = "sso-user"
    username = "admin"

    try:
        async with httpx.AsyncClient(timeout=15.0) as http_client:
            token_resp = await http_client.post(
                token_url,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": settings.VOIDAUTH_CLIENT_ID,
                    "client_secret": settings.VOIDAUTH_CLIENT_SECRET,
                },
                headers={"Accept": "application/json"},
            )
            if token_resp.status_code != 200:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=f"Fehler beim Token-Abruf von VoidAuth: {token_resp.text}",
                )
            token_data = token_resp.json()

            id_token = token_data.get("id_token")
            if id_token:
                try:
                    payload_part = id_token.split(".")[1]
                    payload_part += "=" * (-len(payload_part) % 4)
                    claims = json.loads(base64.urlsafe_b64decode(payload_part.encode()).decode())
                    user_sub = claims.get("sub", user_sub)
                    username = claims.get("preferred_username") or claims.get("name") or claims.get("email") or username
                except Exception:
                    pass
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"SSO Kommunikationsfehler: {exc}")

    target_url = "/dashboard"
    if fsm_dash_sso_next and fsm_dash_sso_next.startswith("/dashboard"):
        target_url = fsm_dash_sso_next

    session_token = _generate_sso_session_token(user_sub, username)
    redirect_response = RedirectResponse(url=target_url, status_code=status.HTTP_302_FOUND)
    redirect_response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_token,
        httponly=True,
        samesite="lax",
        max_age=86400 * 7,  # 7 days
    )
    redirect_response.delete_cookie(key="fsm_dash_sso_state")
    redirect_response.delete_cookie(key="fsm_dash_sso_next")
    return redirect_response


@router.post("/dashboard/api/login", summary="Login to Gateway Dashboard")
async def dashboard_login(payload: LoginRequest, response: Response) -> dict[str, Any]:
    """Validates dashboard password and sets auth cookie."""
    if not settings.DASHBOARD_PASSWORD or hmac.compare_digest(payload.password, settings.DASHBOARD_PASSWORD):
        token = _generate_session_token(settings.DASHBOARD_PASSWORD)
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=token,
            httponly=True,
            samesite="lax",
            max_age=86400 * 7,  # 7 days
        )
        return {"success": True, "message": "Login erfolgreich"}

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Ungültiges Passwort")


@router.post("/dashboard/api/logout", summary="Logout from Gateway Dashboard")
async def dashboard_logout(response: Response) -> dict[str, Any]:
    """Clears dashboard session cookie."""
    response.delete_cookie(key=SESSION_COOKIE_NAME)
    return {"success": True, "message": "Erfolgreich abgemeldet"}


@router.get("/metrics", summary="Prometheus Metrics", response_class=PlainTextResponse)
async def prometheus_metrics() -> str:
    """Exposes gateway metrics in standard Prometheus plaintext format."""
    return metrics_collector.get_prometheus_metrics()


@router.get("/dashboard/api/stats", summary="Get Aggregated Stats")
async def get_dashboard_stats(
    range: str = "24h",
    fsm_dash_auth: str | None = Cookie(None),
    authorization: str | None = Header(None),
) -> dict[str, Any]:
    """Returns aggregated time-series, summaries, and endpoint statistics."""
    if not _is_authenticated(fsm_dash_auth, authorization):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Nicht authentifiziert")

    stats = metrics_collector.get_timeseries_stats(range)
    live = metrics_collector.get_live_stats()
    token = await fsm_client.get_auth_token()

    # Enrich with FSM Cloud session state
    cloud_status = {
        "authenticated": bool(token),
        "cached_entities_count": await cache.size(),
    }

    return {
        **stats,
        "live": live,
        "cloud_status": cloud_status,
    }


@router.get("/dashboard/api/live", summary="Get Live Stats & Recent Requests")
async def get_dashboard_live(
    fsm_dash_auth: str | None = Cookie(None),
    authorization: str | None = Header(None),
) -> dict[str, Any]:
    """Returns live metrics and recent requests feed."""
    if not _is_authenticated(fsm_dash_auth, authorization):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Nicht authentifiziert")

    live = metrics_collector.get_live_stats()
    recent = metrics_collector.get_recent_requests(limit=40)
    recent_errors = metrics_collector.get_recent_errors(limit=10)
    token = await fsm_client.get_auth_token()
    cloud_status = {
        "authenticated": bool(token),
        "cached_entities_count": await cache.size(),
    }
    return {
        "live": live,
        "recent": recent,
        "recent_errors": recent_errors,
        "cloud_status": cloud_status,
    }


@router.get("/dashboard/api/cache/status", summary="Get Valkey Cache Backend Status")
async def dashboard_cache_status(
    fsm_dash_auth: str | None = Cookie(None),
    authorization: str | None = Header(None),
) -> dict[str, Any]:
    """Liefert Valkey-Backend-Status, Server-Metriken und Key-Verteilung."""
    if not _is_authenticated(fsm_dash_auth, authorization):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Nicht authentifiziert")

    return {
        "backend": cache.get_info(),
        "valkey": await cache.valkey_info(),
        "key_counts": await cache.valkey_key_counts(),
        "key_labels": CACHE_CATEGORY_LABELS,
        "total_keys": await cache.size(),
    }


@router.post("/dashboard/api/cache/clear", summary="Clear All Gateway Cache Entries")
async def dashboard_clear_cache(
    fsm_dash_auth: str | None = Cookie(None),
    authorization: str | None = Header(None),
) -> dict[str, Any]:
    """Clears all in-memory caches (calendar, instructors, students, lessons)."""
    if not _is_authenticated(fsm_dash_auth, authorization):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Nicht authentifiziert")

    count_before = await cache.size()
    await cache.clear()
    return {
        "success": True,
        "message": f"Gateway-Cache erfolgreich geleert ({count_before} Einträge gelöscht).",
        "cleared_count": count_before,
    }


@router.get("/dashboard", response_class=HTMLResponse, summary="Gateway Monitoring Dashboard")
async def dashboard_view(
    request: Request,
    fsm_dash_auth: str | None = Cookie(None),
    authorization: str | None = Header(None),
) -> HTMLResponse:
    """Serves the interactive monitoring web dashboard."""
    is_auth = _is_authenticated(fsm_dash_auth, authorization)
    requires_auth = bool(settings.DASHBOARD_PASSWORD or settings.VOIDAUTH_ENABLED)

    if requires_auth and not is_auth:
        return HTMLResponse(_render_login_html(redirect_to="/dashboard"))

    return HTMLResponse(_render_dashboard_html())


@router.get("/dashboard/api/errors", summary="Get Dashboard Error Logs")
async def dashboard_get_errors(
    limit: int = 100,
    status_code: int | None = None,
    since_minutes: int | None = None,
    path: str | None = None,
    fsm_dash_auth: str | None = Cookie(None),
    authorization: str | None = Header(None),
) -> dict[str, Any]:
    """Returns recent errors with reasons for the authenticated dashboard."""
    if not _is_authenticated(fsm_dash_auth, authorization):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Nicht authentifiziert")

    raw_errors = metrics_collector.get_recent_errors(
        limit=limit,
        status_code=status_code,
        since_minutes=since_minutes,
        path=path,
    )
    return {
        "has_errors": len(raw_errors) > 0,
        "count": len(raw_errors),
        "last_error": raw_errors[0] if raw_errors else None,
        "errors": raw_errors,
    }


@router.delete("/dashboard/api/errors", summary="Clear Dashboard Errors")
@router.post("/dashboard/api/errors/clear", summary="Clear Dashboard Errors (POST)")
async def dashboard_clear_errors(
    fsm_dash_auth: str | None = Cookie(None),
    authorization: str | None = Header(None),
) -> dict[str, Any]:
    """Clears all logged errors for the authenticated dashboard."""
    if not _is_authenticated(fsm_dash_auth, authorization):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Nicht authentifiziert")

    deleted = metrics_collector.clear_errors()
    return {
        "success": True,
        "deleted_count": deleted,
        "message": f"{deleted} Fehlerprotokolle wurden gelöscht.",
    }


@router.get("/dashboard/errors", response_class=HTMLResponse, summary="Gateway Error Logs Dashboard Page")
@router.get("/dashboard/fehler", response_class=HTMLResponse, include_in_schema=False)
async def dashboard_errors_view(
    request: Request,
    fsm_dash_auth: str | None = Cookie(None),
    authorization: str | None = Header(None),
) -> HTMLResponse:
    """Serves the dedicated error log and explanations dashboard page."""
    is_auth = _is_authenticated(fsm_dash_auth, authorization)
    requires_auth = bool(settings.DASHBOARD_PASSWORD or settings.VOIDAUTH_ENABLED)

    if requires_auth and not is_auth:
        return HTMLResponse(_render_login_html(redirect_to="/dashboard/errors"))

    return HTMLResponse(_render_errors_html())


def _render_login_html(redirect_to: str = "/dashboard") -> str:
    """HTML for SSO & password login screen."""
    sso_enabled = settings.VOIDAUTH_ENABLED
    has_password = bool(settings.DASHBOARD_PASSWORD)

    sso_html = ""
    if sso_enabled:
        sso_url = f"/dashboard/auth/sso/login?next={urllib.parse.quote(redirect_to)}"
        sso_html = f"""
        <div class="mb-3">
            <a href="{sso_url}" class="btn btn-primary w-100 py-2 fw-semibold rounded-3 d-flex align-items-center justify-content-center gap-2 text-decoration-none shadow-sm">
                {icon('shield-check', 'fs-5')} Mit VoidAuth SSO anmelden
            </a>
        </div>
        """

    divider_html = ""
    if sso_enabled and has_password:
        divider_html = """
        <div class="position-relative text-center my-3">
            <hr class="border-secondary border-opacity-50 my-0">
            <span class="position-absolute top-50 start-50 translate-middle px-2 bg-dark text-secondary small">oder mit Passwort</span>
        </div>
        """

    password_html = ""
    if has_password:
        password_html = f"""
        <form id="loginForm" onsubmit="handleLogin(event)">
            <div class="mb-3 text-start">
                <label for="password" class="form-label small text-secondary">Admin Passwort</label>
                <input type="password" class="form-control bg-dark border-secondary text-light py-2" id="password" required autofocus placeholder="Passwort eingeben">
            </div>
            <div id="errorAlert" class="alert alert-danger py-2 small d-none" role="alert"></div>
            <button type="submit" class="btn btn-outline-light w-100 py-2 fw-semibold rounded-3" id="submitBtn">
                {icon('log-in', 'me-1')} Anmelden
            </button>
        </form>
        """

    return f"""<!DOCTYPE html>
<html lang="de" data-bs-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>FSM Gateway • Login</title>
    <link rel="icon" type="image/svg+xml" href="data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCA1MTIgNTEyIiB3aWR0aD0iMTAwJSIgaGVpZ2h0PSIxMDAlIj48ZGVmcz48bGluZWFyR3JhZGllbnQgaWQ9ImJnR3JhZCIgeDE9IjAlIiB5MT0iMCUiIHgyPSIxMDAlIiB5Mj0iMTAwJSI+PHN0b3Agb2Zmc2V0PSIwJSIgc3RvcC1jb2xvcj0iIzM0Njg5OSIvPjxzdG9wIG9mZnNldD0iNTAlIiBzdG9wLWNvbG9yPSIjMmI1ODgzIi8+PHN0b3Agb2Zmc2V0PSIxMDAlIiBzdG9wLWNvbG9yPSIjMTkzNzU0Ii8+PC9saW5lYXJHcmFkaWVudD48bGluZWFyR3JhZGllbnQgaWQ9Imdsb3dHcmFkIiB4MT0iMCUiIHkxPSIwJSIgeDI9IjAlIiB5Mj0iMTAwJSI+PHN0b3Agb2Zmc2V0PSIwJSIgc3RvcC1jb2xvcj0iIzhmYjllMyIgc3RvcC1vcGFjaXR5PSIwLjYiLz48c3RvcCBvZmZzZXQ9IjEwMCUiIHN0b3AtY29sb3I9IiM4ZmI5ZTMiIHN0b3Atb3BhY2l0eT0iMCIvPjwvbGluZWFyR3JhZGllbnQ+PGxpbmVhckdyYWRpZW50IGlkPSJib2x0R3JhZCIgeDE9IjAlIiB5MT0iMCUiIHgyPSIxMDAlIiB5Mj0iMTAwJSI+PHN0b3Agb2Zmc2V0PSIwJSIgc3RvcC1jb2xvcj0iI2ZmZmZmZiIvPjxzdG9wIG9mZnNldD0iNTAlIiBzdG9wLWNvbG9yPSIjZGJlOGVmIi8+PHN0b3Agb2Zmc2V0PSIxMDAlIiBzdG9wLWNvbG9yPSIjOGZiOWUzIi8+PC9saW5lYXJHcmFkaWVudD48ZmlsdGVyIGlkPSJzaGFkb3ciIHg9Ii0xMCUiIHk9Ii0xMCUiIHdpZHRoPSIxMjAlIiBoZWlnaHQ9IjEyNSUiPjxmZURyb3BTaGFkb3cgZHg9IjAiIGR5PSIxMCIgc3RkRGV2aWF0aW9uPSIxNCIgZmxvb2QtY29sb3I9IiMwMDAwMDAiIGZsb29kLW9wYWNpdHk9IjAuMyIvPjwvZmlsdGVyPjwvZGVmcz48cmVjdCB4PSIyNCIgeT0iMjQiIHdpZHRoPSI0NjQiIGhlaWdodD0iNDY0IiByeD0iMTA4IiBmaWxsPSJ1cmwoI2JnR3JhZCkiLz48cmVjdCB4PSIyNCIgeT0iMjQiIHdpZHRoPSI0NjQiIGhlaWdodD0iNDY0IiByeD0iMTA4IiBmaWxsPSJub25lIiBzdHJva2U9InVybCgjZ2xvd0dyYWQpIiBzdHJva2Utd2lkdGg9IjYiLz48ZyBmaWx0ZXI9InVybCgjc2hhZG93KSI+PGNpcmNsZSBjeD0iMTYwIiBjeT0iMjU2IiByPSIzNiIgZmlsbD0iIzFmNDQ2NyIgc3Ryb2tlPSIjOGZiOWUzIiBzdHJva2Utd2lkdGg9IjgiLz48Y2lyY2xlIGN4PSIxNjAiIGN5PSIyNTYiIHI9IjE0IiBmaWxsPSIjZmZmZmZmIi8+PGNpcmNsZSBjeD0iMzUyIiBjeT0iMjU2IiByPSIzNiIgZmlsbD0iIzFmNDQ2NyIgc3Ryb2tlPSIjOGZiOWUzIiBzdHJva2Utd2lkdGg9IjgiLz48Y2lyY2xlIGN4PSIzNTIiIGN5PSIyNTYiIHI9IjE0IiBmaWxsPSIjZmZmZmZmIi8+PHBhdGggZD0iTTI2NiAxMTYgTDE5NiAyNjYgTDI1NCAyNjYgTDIzNCAzOTYgTDMxNiAyMzYgTDI1OCAyMzYgWiIgZmlsbD0idXJsKCNib2x0R3JhZCkiIHN0cm9rZT0iIzE5Mzc1NCIgc3Ryb2tlLXdpZHRoPSI2IiBzdHJva2UtbGluZWpvaW49InJvdW5kIi8+PC9nPjwvc3ZnPg==">
    <link rel="icon" type="image/png" sizes="32x32" href="/static/favicon-32x32.png">
    <link rel="icon" type="image/x-icon" href="/favicon.ico">
    <link rel="shortcut icon" href="/favicon.ico">
    <link rel="apple-touch-icon" href="/static/apple-touch-icon.png">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body {{
            font-family: 'Inter', system-ui, -apple-system, sans-serif;
            background: #0f172a;
            color: #f8fafc;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
        }}
        svg.icon {{
            width: 0.85em;
            height: 0.85em;
            flex-shrink: 0;
            vertical-align: -0.12em;
        }}
        a .icon, button .icon {{ pointer-events: none; }}
        .login-card {{
            background: #1e293b;
            border: 1px solid #334155;
            border-radius: 1rem;
            width: 100%;
            max-width: 400px;
            box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.5);
        }}
        .btn-primary {{
            background: #3b82f6;
            border-color: #3b82f6;
        }}
        .btn-primary:hover {{
            background: #2563eb;
            border-color: #2563eb;
        }}
    </style>
</head>
<body>
    <div class="p-4 login-card text-center">
        <div class="rounded-circle bg-primary bg-opacity-10 text-primary d-inline-flex p-3 mb-3">
            {icon('lock-keyhole', 'fs-2')}
        </div>
        <h4 class="fw-bold mb-1">FSM Gateway</h4>
        <p class="text-secondary small mb-4">Authentifizierung für Dashboard erforderlich</p>

        {sso_html}
        {divider_html}
        {password_html}
    </div>

    <script>
        async function handleLogin(e) {{
            e.preventDefault();
            const btn = document.getElementById('submitBtn');
            const alert = document.getElementById('errorAlert');
            const password = document.getElementById('password').value;

            btn.disabled = true;
            alert.classList.add('d-none');

            try {{
                const res = await fetch('/dashboard/api/login', {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{ password }})
                }});
                if (res.ok) {{
                    window.location.reload();
                }} else {{
                    const data = await res.json();
                    alert.textContent = data.detail || 'Falsches Passwort';
                    alert.classList.remove('d-none');
                }}
            }} catch (err) {{
                alert.textContent = 'Verbindungsfehler zum Gateway';
                alert.classList.remove('d-none');
            }} finally {{
                btn.disabled = false;
            }}
</body>
</html>"""


def _load_template(name: str) -> str:
    """Load a dashboard HTML template from static/templates/ (icons substituted at call time)."""
    template_path = Path(__file__).resolve().parent.parent.parent / "static" / "templates" / name
    return substitute(template_path.read_text(encoding="utf-8"))


def _render_dashboard_html() -> str:
    """HTML for the modern interactive metrics dashboard."""
    return _load_template("dashboard.html")


def _render_errors_html() -> str:
    """HTML for the dedicated interactive error logs & explanations dashboard."""
    return _load_template("errors.html")
