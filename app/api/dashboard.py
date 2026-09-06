"""Dashboard Web UI, Statistics API, and Prometheus /metrics endpoint."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
import urllib.parse
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


def _render_dashboard_html() -> str:
    """HTML for the modern interactive metrics dashboard."""
    return substitute("""<!DOCTYPE html>
<html lang="de" data-bs-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>FSM Gateway • Dashboard</title>
    <link rel="icon" type="image/svg+xml" href="data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCA1MTIgNTEyIiB3aWR0aD0iMTAwJSIgaGVpZ2h0PSIxMDAlIj48ZGVmcz48bGluZWFyR3JhZGllbnQgaWQ9ImJnR3JhZCIgeDE9IjAlIiB5MT0iMCUiIHgyPSIxMDAlIiB5Mj0iMTAwJSI+PHN0b3Agb2Zmc2V0PSIwJSIgc3RvcC1jb2xvcj0iIzM0Njg5OSIvPjxzdG9wIG9mZnNldD0iNTAlIiBzdG9wLWNvbG9yPSIjMmI1ODgzIi8+PHN0b3Agb2Zmc2V0PSIxMDAlIiBzdG9wLWNvbG9yPSIjMTkzNzU0Ii8+PC9saW5lYXJHcmFkaWVudD48bGluZWFyR3JhZGllbnQgaWQ9Imdsb3dHcmFkIiB4MT0iMCUiIHkxPSIwJSIgeDI9IjAlIiB5Mj0iMTAwJSI+PHN0b3Agb2Zmc2V0PSIwJSIgc3RvcC1jb2xvcj0iIzhmYjllMyIgc3RvcC1vcGFjaXR5PSIwLjYiLz48c3RvcCBvZmZzZXQ9IjEwMCUiIHN0b3AtY29sb3I9IiM4ZmI5ZTMiIHN0b3Atb3BhY2l0eT0iMCIvPjwvbGluZWFyR3JhZGllbnQ+PGxpbmVhckdyYWRpZW50IGlkPSJib2x0R3JhZCIgeDE9IjAlIiB5MT0iMCUiIHgyPSIxMDAlIiB5Mj0iMTAwJSI+PHN0b3Agb2Zmc2V0PSIwJSIgc3RvcC1jb2xvcj0iI2ZmZmZmZiIvPjxzdG9wIG9mZnNldD0iNTAlIiBzdG9wLWNvbG9yPSIjZGJlOGVmIi8+PHN0b3Agb2Zmc2V0PSIxMDAlIiBzdG9wLWNvbG9yPSIjOGZiOWUzIi8+PC9saW5lYXJHcmFkaWVudD48ZmlsdGVyIGlkPSJzaGFkb3ciIHg9Ii0xMCUiIHk9Ii0xMCUiIHdpZHRoPSIxMjAlIiBoZWlnaHQ9IjEyNSUiPjxmZURyb3BTaGFkb3cgZHg9IjAiIGR5PSIxMCIgc3RkRGV2aWF0aW9uPSIxNCIgZmxvb2QtY29sb3I9IiMwMDAwMDAiIGZsb29kLW9wYWNpdHk9IjAuMyIvPjwvZmlsdGVyPjwvZGVmcz48cmVjdCB4PSIyNCIgeT0iMjQiIHdpZHRoPSI0NjQiIGhlaWdodD0iNDY0IiByeD0iMTA4IiBmaWxsPSJ1cmwoI2JnR3JhZCkiLz48cmVjdCB4PSIyNCIgeT0iMjQiIHdpZHRoPSI0NjQiIGhlaWdodD0iNDY0IiByeD0iMTA4IiBmaWxsPSJub25lIiBzdHJva2U9InVybCgjZ2xvd0dyYWQpIiBzdHJva2Utd2lkdGg9IjYiLz48ZyBmaWx0ZXI9InVybCgjc2hhZG93KSI+PGNpcmNsZSBjeD0iMTYwIiBjeT0iMjU2IiByPSIzNiIgZmlsbD0iIzFmNDQ2NyIgc3Ryb2tlPSIjOGZiOWUzIiBzdHJva2Utd2lkdGg9IjgiLz48Y2lyY2xlIGN4PSIxNjAiIGN5PSIyNTYiIHI9IjE0IiBmaWxsPSIjZmZmZmZmIi8+PGNpcmNsZSBjeD0iMzUyIiBjeT0iMjU2IiByPSIzNiIgZmlsbD0iIzFmNDQ2NyIgc3Ryb2tlPSIjOGZiOWUzIiBzdHJva2Utd2lkdGg9IjgiLz48Y2lyY2xlIGN4PSIzNTIiIGN5PSIyNTYiIHI9IjE0IiBmaWxsPSIjZmZmZmZmIi8+PHBhdGggZD0iTTI2NiAxMTYgTDE5NiAyNjYgTDI1NCAyNjYgTDIzNCAzOTYgTDMxNiAyMzYgTDI1OCAyMzYgWiIgZmlsbD0idXJsKCNib2x0R3JhZCkiIHN0cm9rZT0iIzE5Mzc1NCIgc3Ryb2tlLXdpZHRoPSI2IiBzdHJva2UtbGluZWpvaW49InJvdW5kIi8+PC9nPjwvc3ZnPg==">
    <link rel="icon" type="image/png" sizes="32x32" href="/static/favicon-32x32.png">
    <link rel="icon" type="image/x-icon" href="/favicon.ico">
    <link rel="shortcut icon" href="/favicon.ico">
    <link rel="apple-touch-icon" href="/static/apple-touch-icon.png">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.2/dist/chart.umd.min.js"></script>
    <style>
        body {
            font-family: 'Inter', system-ui, -apple-system, sans-serif;
            background: #0b1120;
            color: #f1f5f9;
            min-height: 100vh;
        }
        svg.icon {
            width: 0.85em;
            height: 0.85em;
            flex-shrink: 0;
            vertical-align: -0.12em;
        }
        a .icon, button .icon { pointer-events: none; }
        .navbar-custom {
            background: rgba(15, 23, 42, 0.85);
            backdrop-filter: blur(12px);
            border-bottom: 1px solid #1e293b;
        }
        .card-custom {
            background: #111827;
            border: 1px solid #1f2937;
            border-radius: 0.85rem;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.2);
            transition: border-color 0.2s;
        }
        .card-custom:hover {
            border-color: #374151;
        }
        .badge-method-get { background: rgba(59, 130, 246, 0.15); color: #60a5fa; border: 1px solid rgba(59, 130, 246, 0.3); }
        .badge-method-post { background: rgba(16, 185, 129, 0.15); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.3); }
        .badge-method-put { background: rgba(245, 158, 11, 0.15); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.3); }
        .badge-method-delete { background: rgba(239, 68, 68, 0.15); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.3); }
        .table-custom {
            --bs-table-bg: transparent;
            --bs-table-border-color: #1f2937;
            color: #cbd5e1;
        }
        .live-dot {
            width: 9px;
            height: 9px;
            border-radius: 50%;
            background-color: #10b981;
            display: inline-block;
            box-shadow: 0 0 8px #10b981;
            animation: pulse 2s infinite;
        }
        @keyframes pulse {
            0% { opacity: 1; transform: scale(1); }
            50% { opacity: 0.4; transform: scale(0.85); }
            100% { opacity: 1; transform: scale(1); }
        }
    </style>
</head>
<body class="pb-5">

    <!-- Top Navbar -->
    <nav class="navbar navbar-custom sticky-top py-2 px-3 mb-4">
        <div class="container-fluid d-flex justify-content-between align-items-center">
            <div class="d-flex align-items-center gap-3">
                <a href="/dashboard" class="d-flex align-items-center gap-2 text-decoration-none">
                    <span class="fs-4 d-inline-flex align-items-center gap-1">{{icon:car-front}}{{icon:zap}}</span>
                    <span class="fw-bold fs-5 text-white">FSM Gateway</span>
                    <span class="badge bg-secondary bg-opacity-25 text-secondary border border-secondary border-opacity-25 rounded-pill px-2 py-1 small">v1.0.0</span>
                </a>
                <!-- Navigation Tabs -->
                <ul class="nav nav-pills gap-1 ms-2">
                    <li class="nav-item">
                        <a href="/dashboard" class="nav-link active px-3 py-1 rounded-pill small fw-semibold">
                            {{icon:layout-dashboard:me-1}} Übersicht
                        </a>
                    </li>
                    <li class="nav-item">
                        <a href="/dashboard/errors" class="nav-link text-secondary px-3 py-1 rounded-pill small fw-semibold position-relative">
                            {{icon:triangle-alert:me-1}} Fehlerprotokoll
                            <span class="badge bg-danger rounded-pill ms-1 d-none" id="navErrorsBadge">0</span>
                        </a>
                    </li>
                </ul>
                <div class="d-none d-xl-flex align-items-center gap-2 ms-3 ps-3 border-start border-secondary border-opacity-25">
                    <span class="live-dot"></span>
                    <span class="small text-secondary" id="cloudStatusBadge">FSM Cloud Verbunden</span>
                </div>
            </div>

            <div class="d-flex align-items-center gap-2">
                <!-- Time Range Buttons -->
                <div class="btn-group btn-group-sm rounded-pill p-1 bg-dark border border-secondary border-opacity-25" role="group">
                    <button type="button" class="btn btn-sm btn-primary rounded-pill px-3 fw-medium range-btn" onclick="setRange('24h')">24h</button>
                    <button type="button" class="btn btn-sm btn-dark rounded-pill px-3 fw-medium range-btn" onclick="setRange('7d')">7 Tage</button>
                    <button type="button" class="btn btn-sm btn-dark rounded-pill px-3 fw-medium range-btn" onclick="setRange('30d')">30 Tage</button>
                </div>

                <!-- Cache Clear Button -->
                <button type="button" id="btnClearCache" class="btn btn-sm btn-outline-warning rounded-pill px-3 fw-medium" onclick="clearGatewayCache()" title="Gesamten In-Memory Cache des Gateways sofort leeren">
                    {{icon:trash-2:me-1}} Cache leeren
                </button>

                <a href="/docs" target="_blank" class="btn btn-sm btn-outline-secondary rounded-pill px-3 text-decoration-none">
                    {{icon:code-xml:me-1}} API Docs
                </a>
                <button type="button" class="btn btn-sm btn-outline-danger rounded-pill px-3" onclick="logout()">
                    {{icon:log-out}}
                </button>
            </div>
        </div>
    </nav>

    <div class="container-fluid px-4">
        <!-- Live KPI Cards -->
        <div class="row g-3 mb-4">
            <div class="col-12 col-sm-6 col-xl-3">
                <div class="card-custom p-3 h-100">
                    <div class="d-flex justify-content-between align-items-center text-secondary mb-1">
                        <span class="small fw-semibold">Anfragen (Gewählter Zeitraum)</span>
                        <span class="text-primary fs-5">{{icon:activity}}</span>
                    </div>
                    <div class="d-flex align-items-baseline gap-2">
                        <h2 class="fw-bold mb-0 text-white" id="kpiTotalReq">-</h2>
                        <span class="small text-secondary" id="kpiReqSec">0.0 req/s</span>
                    </div>
                    <div class="small text-secondary mt-2">
                        Gesamt seit Start: <strong class="text-light" id="kpiLifetime">-</strong>
                    </div>
                </div>
            </div>

            <div class="col-12 col-sm-6 col-xl-3">
                <div class="card-custom p-3 h-100">
                    <div class="d-flex justify-content-between align-items-center text-secondary mb-1">
                        <span class="small fw-semibold">Cache-Effizienz</span>
                        <span class="text-warning fs-5">{{icon:zap}}</span>
                    </div>
                    <div class="d-flex align-items-baseline gap-2">
                        <h2 class="fw-bold mb-0 text-warning" id="kpiCacheRatio">-%</h2>
                        <span class="small text-secondary" id="kpiCacheCount">0 Treffer</span>
                    </div>
                    <div class="small text-secondary mt-2">
                        Aktive Cache-Objekte: <strong class="text-light" id="kpiCacheObjects">-</strong>
                    </div>
                </div>
            </div>

            <div class="col-12 col-sm-6 col-xl-3">
                <div class="card-custom p-3 h-100">
                    <div class="d-flex justify-content-between align-items-center text-secondary mb-1">
                        <span class="small fw-semibold">Valkey-Status</span>
                        <span class="text-info fs-5">{{icon:database}}</span>
                    </div>
                    <div class="d-flex align-items-baseline gap-2">
                        <h2 class="fw-bold mb-0 text-info" id="kpiValkeyBackend">-</h2>
                        <span class="small text-secondary" id="kpiValkeyHitRatio">-</span>
                    </div>
                    <div class="small text-secondary mt-2">
                        Memory: <strong class="text-light" id="kpiValkeyMemory">-</strong>
                    </div>
                    <div class="progress mt-1" style="height: 6px; background: #1f2937;">
                        <div class="progress-bar" id="kpiValkeyMemoryBar" role="progressbar" style="width: 0%; background: #3b82f6;" aria-valuenow="0" aria-valuemin="0" aria-valuemax="100"></div>
                    </div>
                    <div class="small text-secondary mt-1">
                        Keys: <strong class="text-light" id="kpiValkeyKeys">-</strong>
                    </div>
                    <div class="small mt-1 d-none" id="kpiValkeyEviction">
                        <span class="badge bg-danger bg-opacity-25 text-danger border border-danger border-opacity-25">{{icon:triangle-alert:me-1}}<span id="kpiValkeyEvictionCount">0</span> Evictions</span>
                    </div>
                </div>
            </div>

            <div class="col-12 col-sm-6 col-xl-3">
                <div class="card-custom p-3 h-100">
                    <div class="d-flex justify-content-between align-items-center text-secondary mb-1">
                        <span class="small fw-semibold">Durchschnittliche Latenz</span>
                        <span class="text-info fs-5">{{icon:timer}}</span>
                    </div>
                    <div class="d-flex align-items-baseline gap-2">
                        <h2 class="fw-bold mb-0 text-info" id="kpiLatency">- ms</h2>
                        <span class="small text-secondary">Ø Response Time</span>
                    </div>
                    <div class="small text-secondary mt-2">
                        Letzte 60 Sek.: <strong class="text-light" id="kpiLatency60s">- ms</strong>
                    </div>
                </div>
            </div>

            <div class="col-12 col-sm-6 col-xl-3">
                <div class="card-custom p-3 h-100">
                    <div class="d-flex justify-content-between align-items-center text-secondary mb-1">
                        <span class="small fw-semibold">Fehlerquote & Uptime</span>
                        <span class="text-success fs-5">{{icon:shield-check}}</span>
                    </div>
                    <div class="d-flex align-items-baseline gap-2">
                        <h2 class="fw-bold mb-0 text-success" id="kpiErrorRate">0.0%</h2>
                        <span class="small text-secondary" id="kpiErrors">0 Fehler</span>
                    </div>
                    <div class="small text-secondary mt-2">
                        Gateway Uptime: <strong class="text-light" id="kpiUptime">-</strong>
                    </div>
                </div>
            </div>
        </div>

        <!-- Charts Row -->
        <div class="row g-3 mb-4">
            <div class="col-12 col-lg-8">
                <div class="card-custom p-3 h-100">
                    <div class="d-flex justify-content-between align-items-center mb-3">
                        <h6 class="fw-bold mb-0 text-light">{{icon:chart-column:text-primary:me-2}}Anfragevolumen & Cache-Treffer</h6>
                        <span class="badge bg-secondary bg-opacity-25 text-secondary border border-secondary border-opacity-25" id="chartRangeLabel">24 Stunden</span>
                    </div>
                    <div style="position: relative; height: 260px;">
                        <canvas id="trafficChart"></canvas>
                    </div>
                </div>
            </div>

            <div class="col-12 col-lg-4">
                <div class="card-custom p-3 h-100">
                    <div class="d-flex justify-content-between align-items-center mb-3">
                        <h6 class="fw-bold mb-0 text-light">{{icon:chart-pie:text-info:me-2}}HTTP Statusverteilung</h6>
                    </div>
                    <div style="position: relative; height: 260px;">
                        <canvas id="statusChart"></canvas>
                    </div>
                </div>
            </div>
        </div>

        <!-- Tables Row: Top Endpoints & Live Requests Feed -->
        <div class="row g-3">
            <!-- Top Endpoints -->
            <div class="col-12 col-lg-6">
                <div class="card-custom p-3 h-100">
                    <h6 class="fw-bold mb-3 text-light">{{icon:trophy:text-warning:me-2}}Meistaufgerufene Endpunkte</h6>
                    <div class="table-responsive">
                        <table class="table table-custom table-hover align-middle mb-0 small">
                            <thead class="text-secondary">
                                <tr>
                                    <th>Methode</th>
                                    <th>Endpunkt</th>
                                    <th class="text-end">Aufrufe</th>
                                    <th class="text-end">Cache %</th>
                                    <th class="text-end">Ø Latenz</th>
                                </tr>
                            </thead>
                            <tbody id="topEndpointsBody">
                                <tr><td colspan="5" class="text-center text-secondary py-3">Lade Statistiken...</td></tr>
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>

            <!-- Real-Time Stream -->
            <div class="col-12 col-lg-6">
                <div class="card-custom p-3 h-100">
                    <div class="d-flex justify-content-between align-items-center mb-3">
                        <h6 class="fw-bold mb-0 text-light">
                            <span class="live-dot me-2"></span>Echtzeit-Anfragenfeed
                        </h6>
                        <small class="text-secondary">Letzte Aufrufe</small>
                    </div>
                    <div class="table-responsive" style="max-height: 380px; overflow-y: auto;">
                        <table class="table table-custom table-hover align-middle mb-0 small">
                            <thead class="text-secondary sticky-top" style="background: #111827;">
                                <tr>
                                    <th>Zeit</th>
                                    <th>Methode</th>
                                    <th>Pfad</th>
                                    <th>Status</th>
                                    <th class="text-end">Latenz</th>
                                </tr>
                            </thead>
                            <tbody id="liveRequestsBody">
                                <tr><td colspan="5" class="text-center text-secondary py-3">Warte auf Live-Daten...</td></tr>
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>

        <!-- Fehlerprotokoll & Begründungen -->
        <div class="row g-3 mb-4" id="errorsSection" style="display: none;">
            <div class="col-12">
                <div class="card p-3 border-danger" style="background: rgba(239, 68, 68, 0.05);">
                    <div class="d-flex justify-content-between align-items-center mb-2">
                        <h6 class="fw-bold mb-0 text-danger d-flex align-items-center gap-2">
                            <span>⚠️</span> Letzte Fehler & Begründungen
                            <span class="badge bg-danger rounded-pill" id="errorsCountBadge">0</span>
                        </h6>
                        <div class="d-flex gap-2">
                            <button class="btn btn-sm btn-outline-secondary text-light" onclick="clearGatewayErrors()" title="Fehlerprotokoll zurücksetzen">Fehler löschen</button>
                            <a href="/dashboard/errors" class="btn btn-sm btn-primary">Fehlerprotokoll öffnen →</a>
                        </div>
                    </div>
                    <div class="table-responsive" style="max-height: 240px; overflow-y: auto;">
                        <table class="table table-custom table-hover align-middle mb-0 small">
                            <thead class="text-secondary sticky-top" style="background: #111827;">
                                <tr>
                                    <th>Zeit</th>
                                    <th>Methode</th>
                                    <th>Pfad</th>
                                    <th>Status</th>
                                    <th>Fehlertyp</th>
                                    <th>Begründung / Ursache</th>
                                </tr>
                            </thead>
                            <tbody id="errorsTableBody">
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script>
        let currentRange = '24h';
        let trafficChart = null;
        let statusChart = null;
        let lastTrafficHash = null;
        let lastStatusHash = null;
        let lastTopEndpointsHash = null;
        let lastLiveFeedHash = null;

        function setRange(range) {
            currentRange = range;
            document.querySelectorAll('.range-btn').forEach(btn => {
                btn.classList.remove('btn-primary');
                btn.classList.add('btn-dark');
            });
            event.target.classList.remove('btn-dark');
            event.target.classList.add('btn-primary');
            document.getElementById('chartRangeLabel').textContent = range === '24h' ? '24 Stunden' : (range === '7d' ? '7 Tage' : '30 Tage');
            // Reset hashes so new range renders immediately
            lastTrafficHash = null;
            lastStatusHash = null;
            lastTopEndpointsHash = null;
            loadStats();
        }

        async function logout() {
            await fetch('/dashboard/api/logout', { method: 'POST' });
            window.location.reload();
        }

        function getMethodBadge(method) {
            const m = (method || 'GET').toUpperCase();
            if (m === 'GET') return '<span class="badge badge-method-get px-2 py-1">GET</span>';
            if (m === 'POST') return '<span class="badge badge-method-post px-2 py-1">POST</span>';
            if (m === 'PUT') return '<span class="badge badge-method-put px-2 py-1">PUT</span>';
            if (m === 'DELETE') return '<span class="badge badge-method-delete px-2 py-1">DELETE</span>';
            return `<span class="badge bg-secondary px-2 py-1">${m}</span>`;
        }

        function getStatusBadge(code) {
            const c = parseInt(code, 10);
            if (c >= 200 && c < 300) return `<span class="badge bg-success bg-opacity-25 text-success border border-success border-opacity-25">${c}</span>`;
            if (c >= 300 && c < 400) return `<span class="badge bg-info bg-opacity-25 text-info border border-info border-opacity-25">${c}</span>`;
            if (c >= 400 && c < 500) return `<span class="badge bg-warning bg-opacity-25 text-warning border border-warning border-opacity-25">${c}</span>`;
            return `<span class="badge bg-danger bg-opacity-25 text-danger border border-danger border-opacity-25">${c}</span>`;
        }

        async function loadStats() {
            try {
                const res = await fetch(`/dashboard/api/stats?range=${currentRange}`);
                if (res.status === 401) {
                    window.location.reload();
                    return;
                }
                const data = await res.json();

                // Update KPIs
                const sum = data.summary;
                const live = data.live;
                document.getElementById('kpiTotalReq').textContent = Number(sum.total_requests).toLocaleString('de-DE');
                document.getElementById('kpiReqSec').textContent = `${live.requests_per_second} req/s`;
                document.getElementById('kpiLifetime').textContent = Number(live.lifetime_total).toLocaleString('de-DE');

                document.getElementById('kpiCacheRatio').textContent = `${sum.cache_hit_ratio_pct}%`;
                document.getElementById('kpiCacheCount').textContent = `${sum.cache_hits} Treffer`;
                document.getElementById('kpiCacheObjects').textContent = data.cloud_status.cached_entities_count;

                document.getElementById('kpiLatency').textContent = `${sum.avg_latency_ms} ms`;
                document.getElementById('kpiLatency60s').textContent = `${live.avg_latency_60s_ms} ms`;

                document.getElementById('kpiErrorRate').textContent = `${sum.error_rate_pct}%`;
                document.getElementById('kpiErrors').textContent = `${sum.error_requests} Fehler`;
                document.getElementById('kpiUptime').textContent = live.uptime_formatted;

                if (sum.error_rate_pct > 5) {
                    document.getElementById('kpiErrorRate').className = 'fw-bold mb-0 text-danger';
                } else {
                    document.getElementById('kpiErrorRate').className = 'fw-bold mb-0 text-success';
                }

                // Render Traffic Chart (mit Datenvergleich & ohne störende Neu-Animation)
                renderTrafficChart(data.timeseries);

                // Render Status Chart (mit Datenvergleich & ohne störende Neu-Animation)
                renderStatusChart(data.status_codes);

                // Render Top Endpoints
                renderTopEndpoints(data.top_endpoints);
            } catch (err) {
                console.error('Error loading stats:', err);
            }
        }

        function renderTrafficChart(timeseries) {
            const labels = (timeseries || []).map(t => t.time);
            const cachedData = (timeseries || []).map(t => t.cached);
            const directData = (timeseries || []).map(t => Math.max(0, t.total - t.cached));
            const errorData = (timeseries || []).map(t => t.errors);

            const hash = JSON.stringify({ labels, directData, cachedData, errorData });
            if (hash === lastTrafficHash && trafficChart) {
                return; // Keine Datenänderung: kein Re-Render
            }
            lastTrafficHash = hash;

            const ctx = document.getElementById('trafficChart').getContext('2d');

            if (trafficChart) {
                // Bestehende Chart-Instanz aktualisieren ohne Zerstören und ohne Animation
                trafficChart.data.labels = labels;
                trafficChart.data.datasets[0].data = directData;
                trafficChart.data.datasets[1].data = cachedData;
                trafficChart.data.datasets[2].data = errorData;
                trafficChart.update('none');
                return;
            }

            // Initiale Erstellung (Animation deaktiviert für sofortige, flimmerfreie Anzeige)
            trafficChart = new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: labels,
                    datasets: [
                        {
                            label: 'Direkte Requests',
                            data: directData,
                            backgroundColor: '#3b82f6',
                            borderRadius: 4,
                            stack: 'traffic',
                        },
                        {
                            label: 'Cache Hits',
                            data: cachedData,
                            backgroundColor: '#f59e0b',
                            borderRadius: 4,
                            stack: 'traffic',
                        },
                        {
                            label: 'Fehler (>=400)',
                            data: errorData,
                            backgroundColor: '#ef4444',
                            borderRadius: 4,
                            stack: 'traffic',
                        }
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    animation: false,
                    scales: {
                        x: { stacked: true, grid: { color: '#1f2937' }, ticks: { color: '#94a3b8' } },
                        y: { stacked: true, beginAtZero: true, grid: { color: '#1f2937' }, ticks: { color: '#94a3b8', precision: 0 } }
                    },
                    plugins: {
                        legend: { position: 'top', labels: { color: '#cbd5e1', font: { size: 12 } } },
                        tooltip: { mode: 'index', intersect: false }
                    }
                }
            });
        }

        function renderStatusChart(statusCodes) {
            let labels = Object.keys(statusCodes || {});
            let data = Object.values(statusCodes || {});
            let colors = labels.map(code => {
                const c = parseInt(code, 10);
                if (c >= 200 && c < 300) return '#10b981';
                if (c >= 300 && c < 400) return '#06b6d4';
                if (c >= 400 && c < 500) return '#f59e0b';
                return '#ef4444';
            });

            if (labels.length === 0) {
                labels = ['Keine Daten'];
                data = [1];
                colors = ['#374151'];
            }

            const hash = JSON.stringify({ labels, data });
            if (hash === lastStatusHash && statusChart) {
                return; // Keine Datenänderung: kein Re-Render
            }
            lastStatusHash = hash;

            const ctx = document.getElementById('statusChart').getContext('2d');

            if (statusChart) {
                // Bestehende Chart-Instanz aktualisieren ohne Zerstören und ohne Animation
                statusChart.data.labels = labels;
                statusChart.data.datasets[0].data = data;
                statusChart.data.datasets[0].backgroundColor = colors;
                statusChart.update('none');
                return;
            }

            // Initiale Erstellung (Animation deaktiviert)
            statusChart = new Chart(ctx, {
                type: 'doughnut',
                data: {
                    labels: labels,
                    datasets: [{ data: data, backgroundColor: colors, borderWidth: 0 }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    animation: false,
                    plugins: {
                        legend: { position: 'bottom', labels: { color: '#cbd5e1', font: { size: 11 } } }
                    }
                }
            });
        }

        function renderTopEndpoints(endpoints) {
            const hash = JSON.stringify(endpoints || []);
            if (hash === lastTopEndpointsHash) {
                return;
            }
            lastTopEndpointsHash = hash;

            const tbody = document.getElementById('topEndpointsBody');
            if (!endpoints || endpoints.length === 0) {
                tbody.innerHTML = '<tr><td colspan="5" class="text-center text-secondary py-3">Noch keine Anfragen erfasst</td></tr>';
                return;
            }

            tbody.innerHTML = endpoints.map(ep => {
                const cachePct = ep.count > 0 ? Math.round((ep.cache_hits / ep.count) * 100) : 0;
                return `<tr>
                    <td>${getMethodBadge(ep.method)}</td>
                    <td class="font-monospace text-light text-truncate" style="max-width: 200px;" title="${ep.path}">${ep.path}</td>
                    <td class="text-end fw-bold text-light">${Number(ep.count).toLocaleString('de-DE')}</td>
                    <td class="text-end text-warning">${cachePct}%</td>
                    <td class="text-end text-info">${ep.avg_ms} ms</td>
                </tr>`;
            }).join('');
        }

        async function loadLiveFeed() {
            try {
                const res = await fetch('/dashboard/api/live');
                if (!res.ok) return;
                const data = await res.json();

                const hash = JSON.stringify(data.recent || []);
                if (hash === lastLiveFeedHash) {
                    return;
                }
                lastLiveFeedHash = hash;

                const tbody = document.getElementById('liveRequestsBody');
                if (!data.recent || data.recent.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="5" class="text-center text-secondary py-3">Noch keine Requests vorhanden</td></tr>';
                    return;
                }

                tbody.innerHTML = data.recent.map(r => {
                    const cacheBadge = r.cached ? '<span class="badge bg-warning bg-opacity-10 text-warning ms-1" style="font-size: 0.65rem;">CACHE</span>' : '';
                    return `<tr>
                        <td class="text-secondary">${r.time}</td>
                        <td>${getMethodBadge(r.method)}</td>
                        <td class="font-monospace text-light text-truncate" style="max-width: 180px;" title="${r.path}">${r.path}${cacheBadge}</td>
                        <td>${getStatusBadge(r.status_code)}</td>
                        <td class="text-end text-secondary">${r.duration_ms} ms</td>
                    </tr>`;
                }).join('');

                // Update Errors Section & Nav Badge
                const errorsSec = document.getElementById('errorsSection');
                const errorsBody = document.getElementById('errorsTableBody');
                const errorsBadge = document.getElementById('errorsCountBadge');
                const navErrorsBadge = document.getElementById('navErrorsBadge');
                const errs = data.recent_errors || [];

                if (navErrorsBadge) {
                    if (errs.length > 0) {
                        navErrorsBadge.textContent = errs.length;
                        navErrorsBadge.classList.remove('d-none');
                    } else {
                        navErrorsBadge.classList.add('d-none');
                    }
                }

                if (errorsSec && errorsBody && errorsBadge) {
                    if (errs.length > 0) {
                        errorsSec.style.display = 'block';
                        errorsBadge.textContent = errs.length;
                        errorsBody.innerHTML = errs.map(err => {
                            const escBegruendung = (err.begruendung || err.message || '').replace(/[&<>"']/g, m => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[m]));
                            const escPath = (err.path || '').replace(/[&<>"']/g, m => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[m]));
                            return `<tr>
                                <td class="text-secondary">${err.time || ''}</td>
                                <td>${getMethodBadge(err.method)}</td>
                                <td class="font-monospace text-light text-truncate" style="max-width: 160px;" title="${escPath}">${escPath}</td>
                                <td>${getStatusBadge(err.status_code)}</td>
                                <td><span class="badge bg-danger bg-opacity-25 text-danger">${err.error_type || 'Error'}</span></td>
                                <td class="text-light" style="max-width: 400px; word-break: break-word;" title="${escBegruendung}">${escBegruendung}</td>
                            </tr>`;
                        }).join('');
                    } else {
                        errorsSec.style.display = 'none';
                    }
                }
            } catch (err) {
                console.error('Error fetching live feed:', err);
            }
        }

        async function clearGatewayErrors() {
            if (!confirm('Fehlerprotokoll wirklich leeren?')) return;
            try {
                const res = await fetch('/dashboard/api/errors', { method: 'DELETE' });
                if (res.ok) {
                    await loadLiveFeed();
                }
            } catch (err) {
                console.error('Fehler beim Löschen der Protokolle:', err);
            }
        }

        async function loadValkeyStatus() {
            try {
                const res = await fetch('/dashboard/api/cache/status');
                if (!res.ok) return;
                const data = await res.json();
                const backend = data.backend || {};
                const vk = data.valkey || {};

                const backendEl = document.getElementById('kpiValkeyBackend');
                const hitEl = document.getElementById('kpiValkeyHitRatio');
                const memEl = document.getElementById('kpiValkeyMemory');
                const keysEl = document.getElementById('kpiValkeyKeys');
                const memBarEl = document.getElementById('kpiValkeyMemoryBar');
                const evictionEl = document.getElementById('kpiValkeyEviction');
                const evictionCountEl = document.getElementById('kpiValkeyEvictionCount');

                if (backend.connected) {
                    backendEl.textContent = 'Valkey';
                    backendEl.className = 'fw-bold mb-0 text-info';
                    hitEl.textContent = `${vk.hit_ratio_pct ?? 0}% Hit-Ratio`;
                    memEl.textContent = `${vk.used_memory_human || '-'} / ${vk.maxmemory_human || '-'} (${vk.memory_usage_pct ?? 0}%)`;

                    // Memory-Balken: Farbe nach Auslastung (blau <70%, gelb <90%, rot >=90%)
                    const memPct = vk.memory_usage_pct ?? 0;
                    memBarEl.style.width = `${Math.min(100, memPct)}%`;
                    memBarEl.style.background = memPct >= 90 ? '#ef4444' : (memPct >= 70 ? '#f59e0b' : '#3b82f6');
                    memBarEl.setAttribute('aria-valuenow', String(memPct));

                    // Eviction-Warnung nur bei evicted_keys > 0
                    const evicted = vk.evicted_keys ?? 0;
                    if (evicted > 0) {
                        evictionCountEl.textContent = evicted;
                        evictionEl.classList.remove('d-none');
                    } else {
                        evictionEl.classList.add('d-none');
                    }

                    const kc = data.key_counts || {};
                    const labels = data.key_labels || {
                        'kalender': 'Kalender',
                        'fahrlehrer': 'Fahrlehrer',
                        'schueler': 'Schüler',
                        'fahrstunden': 'Fahrstunden',
                        'leistungen': 'Leistungen',
                        'auth': 'FSM-Auth',
                        'webhooks': 'Webhooks'
                    };
                    const keyParts = Object.entries(kc)
                        .filter(([_, count]) => count > 0)
                        .map(([k, v]) => `${labels[k] || k}: ${v}`)
                        .join(' · ');
                    keysEl.textContent = `${data.total_keys ?? 0} gesamt${keyParts ? ' — ' + keyParts : ''}`;
                } else {
                    backendEl.textContent = 'Memory';
                    backendEl.className = 'fw-bold mb-0 text-warning';
                    hitEl.textContent = 'Fallback aktiv';
                    memEl.textContent = '—';
                    memBarEl.style.width = '0%';
                    evictionEl.classList.add('d-none');

                    const kc = data.key_counts || {};
                    const labels = data.key_labels || {
                        'kalender': 'Kalender',
                        'fahrlehrer': 'Fahrlehrer',
                        'schueler': 'Schüler',
                        'fahrstunden': 'Fahrstunden',
                        'leistungen': 'Leistungen',
                        'auth': 'FSM-Auth',
                        'webhooks': 'Webhooks'
                    };
                    const keyParts = Object.entries(kc)
                        .filter(([_, count]) => count > 0)
                        .map(([k, v]) => `${labels[k] || k}: ${v}`)
                        .join(' · ');
                    keysEl.textContent = `${data.total_keys ?? 0} gesamt${keyParts ? ' — ' + keyParts : ''}`;
                }
            } catch (err) {
                console.error('Error loading Valkey status:', err);
            }
        }

        async function clearGatewayCache() {
            const btn = document.getElementById('btnClearCache');
            if (!confirm('Möchtest du den gesamten Gateway-Cache leeren? Alle nächsten Abfragen (Kalender, Schüler, Fahrlehrer) werden dann live von FSM geladen.')) {
                return;
            }
            const originalHtml = btn.innerHTML;
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1" role="status"></span> Leere...';
            try {
                const resp = await fetch('/dashboard/api/cache/clear', { method: 'POST' });
                const data = await resp.json();
                if (resp.ok && data.success) {
                    btn.innerHTML = '{{icon:circle-check:me-1}} Geleert!';
                    btn.classList.remove('btn-outline-warning');
                    btn.classList.add('btn-success');
                    setTimeout(() => {
                        btn.innerHTML = originalHtml;
                        btn.classList.remove('btn-success');
                        btn.classList.add('btn-outline-warning');
                        btn.disabled = false;
                    }, 2500);
                    // Refresh stats and live view immediately
                    await loadStats();
                    await loadLiveFeed();
                } else {
                    alert('Fehler beim Leeren des Caches: ' + (data.detail || data.message || 'Unbekannter Fehler'));
                    btn.innerHTML = originalHtml;
                    btn.disabled = false;
                }
            } catch (err) {
                alert('Netzwerkfehler: ' + err);
                btn.innerHTML = originalHtml;
                btn.disabled = false;
            }
        }

        // Initialize dashboard
        loadStats();
        loadLiveFeed();
        loadValkeyStatus();

        // Refresh intervals: live feed every 3s, charts every 15s, valkey every 15s
        setInterval(loadLiveFeed, 3000);
        setInterval(loadStats, 15000);
        setInterval(loadValkeyStatus, 15000);
    </script>
</body>
</html>""")


def _render_errors_html() -> str:
    """HTML for the dedicated interactive error logs & explanations dashboard."""
    return substitute("""<!DOCTYPE html>
<html lang="de" data-bs-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>FSM Gateway • Fehlerprotokoll & Begründungen</title>
    <link rel="icon" type="image/svg+xml" href="data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCA1MTIgNTEyIiB3aWR0aD0iMTAwJSIgaGVpZ2h0PSIxMDAlIj48ZGVmcz48bGluZWFyR3JhZGllbnQgaWQ9ImJnR3JhZCIgeDE9IjAlIiB5MT0iMCUiIHgyPSIxMDAlIiB5Mj0iMTAwJSI+PHN0b3Agb2Zmc2V0PSIwJSIgc3RvcC1jb2xvcj0iIzM0Njg5OSIvPjxzdG9wIG9mZnNldD0iNTAlIiBzdG9wLWNvbG9yPSIjMmI1ODgzIi8+PHN0b3Agb2Zmc2V0PSIxMDAlIiBzdG9wLWNvbG9yPSIjMTkzNzU0Ii8+PC9saW5lYXJHcmFkaWVudD48bGluZWFyR3JhZGllbnQgaWQ9Imdsb3dHcmFkIiB4MT0iMCUiIHkxPSIwJSIgeDI9IjAlIiB5Mj0iMTAwJSI+PHN0b3Agb2Zmc2V0PSIwJSIgc3RvcC1jb2xvcj0iIzhmYjllMyIgc3RvcC1vcGFjaXR5PSIwLjYiLz48c3RvcCBvZmZzZXQ9IjEwMCUiIHN0b3AtY29sb3I9IiM4ZmI5ZTMiIHN0b3Atb3BhY2l0eT0iMCIvPjwvbGluZWFyR3JhZGllbnQ+PGxpbmVhckdyYWRpZW50IGlkPSJib2x0R3JhZCIgeDE9IjAlIiB5MT0iMCUiIHgyPSIxMDAlIiB5Mj0iMTAwJSI+PHN0b3Agb2Zmc2V0PSIwJSIgc3RvcC1jb2xvcj0iI2ZmZmZmZiIvPjxzdG9wIG9mZnNldD0iNTAlIiBzdG9wLWNvbG9yPSIjZGJlOGVmIi8+PHN0b3Agb2Zmc2V0PSIxMDAlIiBzdG9wLWNvbG9yPSIjOGZiOWUzIi8+PC9saW5lYXJHcmFkaWVudD48ZmlsdGVyIGlkPSJzaGFkb3ciIHg9Ii0xMCUiIHk9Ii0xMCUiIHdpZHRoPSIxMjAlIiBoZWlnaHQ9IjEyNSUiPjxmZURyb3BTaGFkb3cgZHg9IjAiIGR5PSIxMCIgc3RkRGV2aWF0aW9uPSIxNCIgZmxvb2QtY29sb3I9IiMwMDAwMDAiIGZsb29kLW9wYWNpdHk9IjAuMyIvPjwvZmlsdGVyPjwvZGVmcz48cmVjdCB4PSIyNCIgeT0iMjQiIHdpZHRoPSI0NjQiIGhlaWdodD0iNDY0IiByeD0iMTA4IiBmaWxsPSJ1cmwoI2JnR3JhZCkiLz48cmVjdCB4PSIyNCIgeT0iMjQiIHdpZHRoPSI0NjQiIGhlaWdodD0iNDY0IiByeD0iMTA4IiBmaWxsPSJub25lIiBzdHJva2U9InVybCgjZ2xvd0dyYWQpIiBzdHJva2Utd2lkdGg9IjYiLz48ZyBmaWx0ZXI9InVybCgjc2hhZG93KSI+PGNpcmNsZSBjeD0iMTYwIiBjeT0iMjU2IiByPSIzNiIgZmlsbD0iIzFmNDQ2NyIgc3Ryb2tlPSIjOGZiOWUzIiBzdHJva2Utd2lkdGg9IjgiLz48Y2lyY2xlIGN4PSIxNjAiIGN5PSIyNTYiIHI9IjE0IiBmaWxsPSIjZmZmZmZmIi8+PGNpcmNsZSBjeD0iMzUyIiBjeT0iMjU2IiByPSIzNiIgZmlsbD0iIzFmNDQ2NyIgc3Ryb2tlPSIjOGZiOWUzIiBzdHJva2Utd2lkdGg9IjgiLz48Y2lyY2xlIGN4PSIzNTIiIGN5PSIyNTYiIHI9IjE0IiBmaWxsPSIjZmZmZmZmIi8+PHBhdGggZD0iTTI2NiAxMTYgTDE5NiAyNjYgTDI1NCAyNjYgTDIzNCAzOTYgTDMxNiAyMzYgTDI1OCAyMzYgWiIgZmlsbD0idXJsKCNib2x0R3JhZCkiIHN0cm9rZT0iIzE5Mzc1NCIgc3Ryb2tlLXdpZHRoPSI2IiBzdHJva2UtbGluZWpvaW49InJvdW5kIi8+PC9nPjwvc3ZnPg==">
    <link rel="icon" type="image/png" sizes="32x32" href="/static/favicon-32x32.png">
    <link rel="icon" type="image/x-icon" href="/favicon.ico">
    <link rel="shortcut icon" href="/favicon.ico">
    <link rel="apple-touch-icon" href="/static/apple-touch-icon.png">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
    <style>
        body {
            font-family: 'Inter', system-ui, -apple-system, sans-serif;
            background: #0b1120;
            color: #f1f5f9;
            min-height: 100vh;
        }
        svg.icon {
            width: 0.85em;
            height: 0.85em;
            flex-shrink: 0;
            vertical-align: -0.12em;
        }
        a .icon, button .icon { pointer-events: none; }
        .navbar-custom {
            background: rgba(15, 23, 42, 0.85);
            backdrop-filter: blur(12px);
            border-bottom: 1px solid #1e293b;
        }
        .card-custom {
            background: #111827;
            border: 1px solid #1f2937;
            border-radius: 0.85rem;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.2);
            transition: border-color 0.2s;
        }
        .card-custom:hover {
            border-color: #374151;
        }
        .table-custom {
            --bs-table-bg: transparent;
            --bs-table-border-color: #1f2937;
            color: #cbd5e1;
        }
        .badge-method-get { background: rgba(59, 130, 246, 0.15); color: #60a5fa; border: 1px solid rgba(59, 130, 246, 0.3); }
        .badge-method-post { background: rgba(16, 185, 129, 0.15); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.3); }
        .badge-method-put { background: rgba(245, 158, 11, 0.15); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.3); }
        .badge-method-delete { background: rgba(239, 68, 68, 0.15); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.3); }
        .live-dot {
            width: 9px;
            height: 9px;
            border-radius: 50%;
            background-color: #10b981;
            display: inline-block;
            box-shadow: 0 0 8px #10b981;
            animation: pulse 2s infinite;
        }
        @keyframes pulse {
            0% { opacity: 1; transform: scale(1); }
            50% { opacity: 0.4; transform: scale(0.85); }
            100% { opacity: 1; transform: scale(1); }
        }
        .error-row {
            cursor: pointer;
            transition: background 0.15s ease;
        }
        .error-row:hover {
            background: rgba(239, 68, 68, 0.06) !important;
        }
        pre.code-block {
            background: #090d16;
            color: #38bdf8;
            border: 1px solid #1e293b;
            border-radius: 0.5rem;
            padding: 1rem;
            max-height: 420px;
            overflow-y: auto;
            font-size: 0.85rem;
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
        }
    </style>
</head>
<body class="pb-5">
    <!-- Navbar -->
    <nav class="navbar navbar-custom sticky-top py-2 px-3 mb-4">
        <div class="container-fluid d-flex justify-content-between align-items-center">
            <div class="d-flex align-items-center gap-3">
                <a href="/dashboard" class="d-flex align-items-center gap-2 text-decoration-none">
                    <span class="fs-4 d-inline-flex align-items-center gap-1">{{icon:car-front}}{{icon:zap}}</span>
                    <span class="fw-bold fs-5 text-white">FSM Gateway</span>
                    <span class="badge bg-secondary bg-opacity-25 text-secondary border border-secondary border-opacity-25 rounded-pill px-2 py-1 small">v1.0.0</span>
                </a>
                <!-- Navigation Tabs -->
                <ul class="nav nav-pills gap-1 ms-2">
                    <li class="nav-item">
                        <a href="/dashboard" class="nav-link text-secondary px-3 py-1 rounded-pill small fw-semibold">
                            {{icon:layout-dashboard:me-1}} Übersicht
                        </a>
                    </li>
                    <li class="nav-item">
                        <a href="/dashboard/errors" class="nav-link active px-3 py-1 rounded-pill small fw-semibold position-relative">
                            {{icon:triangle-alert:me-1}} Fehlerprotokoll
                            <span class="badge bg-danger rounded-pill ms-1 d-none" id="navErrorsBadge">0</span>
                        </a>
                    </li>
                </ul>
            </div>

            <div class="d-flex align-items-center gap-2">
                <button type="button" id="btnClearCache" class="btn btn-sm btn-outline-warning rounded-pill px-3 fw-medium" onclick="clearGatewayCache()" title="Gesamten In-Memory Cache leeren">
                    {{icon:trash-2:me-1}} Cache leeren
                </button>
                <a href="/docs" target="_blank" class="btn btn-sm btn-outline-secondary rounded-pill px-3 text-decoration-none">
                    {{icon:code-xml:me-1}} API Docs
                </a>
                <button type="button" class="btn btn-sm btn-outline-danger rounded-pill px-3" onclick="logout()">
                    {{icon:log-out}}
                </button>
            </div>
        </div>
    </nav>

    <div class="container-fluid px-4">
        <!-- Page Header -->
        <div class="d-flex flex-column flex-md-row justify-content-between align-items-md-center gap-3 mb-4">
            <div>
                <h4 class="fw-bold mb-1 d-flex align-items-center gap-2 text-light">
                    {{icon:shield-alert:text-danger}} Fehlerprotokoll & Begründungen
                </h4>
                <p class="text-secondary small mb-0">Zentral erfasste API-, Validierungs- und Upstream-Fehler mit detaillierter Ursachenanalyse</p>
            </div>
            <div class="d-flex align-items-center gap-2">
                <div class="form-check form-switch me-2">
                    <input class="form-check-input" type="checkbox" id="chkAutoSync" checked onchange="toggleAutoSync()">
                    <label class="form-check-label small text-secondary" for="chkAutoSync">
                        <span class="live-dot me-1" id="autoSyncDot"></span>Live-Sync (5s)
                    </label>
                </div>
                <button type="button" class="btn btn-sm btn-outline-primary rounded-pill px-3" onclick="loadErrors()">
                    {{icon:refresh-cw:me-1}} Aktualisieren
                </button>
                <button type="button" class="btn btn-sm btn-outline-danger rounded-pill px-3" onclick="clearAllErrors()">
                    {{icon:trash-2:me-1}} Protokoll leeren
                </button>
                <a href="/v1/errors" target="_blank" class="btn btn-sm btn-outline-secondary rounded-pill px-3 text-decoration-none">
                    {{icon:code-xml:me-1}} JSON API
                </a>
            </div>
        </div>

        <!-- KPI Cards -->
        <div class="row g-3 mb-4">
            <div class="col-12 col-sm-6 col-xl-3">
                <div class="card-custom p-3 h-100">
                    <div class="d-flex justify-content-between align-items-center text-secondary mb-1">
                        <span class="small fw-semibold">Protokollierte Fehler</span>
                        <span class="text-danger fs-5">{{icon:triangle-alert}}</span>
                    </div>
                    <div class="fs-2 fw-bold text-light" id="kpiTotalErrors">0</div>
                    <small class="text-secondary">Im aktuellen Speicher / Filter</small>
                </div>
            </div>
            <div class="col-12 col-sm-6 col-xl-3">
                <div class="card-custom p-3 h-100">
                    <div class="d-flex justify-content-between align-items-center text-secondary mb-1">
                        <span class="small fw-semibold">4xx Client- / Validierungsfehler</span>
                        <span class="text-warning fs-5">{{icon:circle-alert}}</span>
                    </div>
                    <div class="fs-2 fw-bold text-warning" id="kpi4xxErrors">0</div>
                    <small class="text-secondary">z.B. Kursgrenzen, Datumsfehler</small>
                </div>
            </div>
            <div class="col-12 col-sm-6 col-xl-3">
                <div class="card-custom p-3 h-100">
                    <div class="d-flex justify-content-between align-items-center text-secondary mb-1">
                        <span class="small fw-semibold">5xx Server- / FSM-Fehler</span>
                        <span class="text-danger fs-5">{{icon:circle-x}}</span>
                    </div>
                    <div class="fs-2 fw-bold text-danger" id="kpi5xxErrors">0</div>
                    <small class="text-secondary">Cloud-Abbrüche oder Gateway-Exceptions</small>
                </div>
            </div>
            <div class="col-12 col-sm-6 col-xl-3">
                <div class="card-custom p-3 h-100">
                    <div class="d-flex justify-content-between align-items-center text-secondary mb-1">
                        <span class="small fw-semibold">Letzter Fehler</span>
                        <span class="text-info fs-5">{{icon:timer}}</span>
                    </div>
                    <div class="fs-5 fw-bold text-light text-truncate" id="kpiLastError" title="Keine Fehler">Keine Fehler</div>
                    <small class="text-secondary" id="kpiLastErrorSub">System läuft stabil</small>
                </div>
            </div>
        </div>

        <!-- Filter Card -->
        <div class="card-custom p-3 mb-4">
            <div class="row g-2 align-items-center">
                <div class="col-12 col-md-3">
                    <div class="input-group input-group-sm">
                        <span class="input-group-text bg-dark border-secondary border-opacity-50 text-secondary">
                            {{icon:search}}
                        </span>
                        <input type="text" class="form-control bg-dark border-secondary border-opacity-50 text-light" id="filterPath" placeholder="Pfad oder Begründung suchen..." oninput="debounceLoadErrors()">
                    </div>
                </div>
                <div class="col-6 col-md-3">
                    <select class="form-select form-select-sm bg-dark border-secondary border-opacity-50 text-light" id="filterStatus" onchange="loadErrors()">
                        <option value="">Alle Statuscodes</option>
                        <option value="400">400 Bad Request</option>
                        <option value="401">401 Unauthorized</option>
                        <option value="403">403 Forbidden</option>
                        <option value="404">404 Not Found</option>
                        <option value="422">422 Validierungsfehler</option>
                        <option value="500">500 Server Error</option>
                        <option value="502">502 Bad Gateway</option>
                    </select>
                </div>
                <div class="col-6 col-md-3">
                    <select class="form-select form-select-sm bg-dark border-secondary border-opacity-50 text-light" id="filterTime" onchange="loadErrors()">
                        <option value="">Gesamter Zeitraum</option>
                        <option value="15">Letzte 15 Minuten</option>
                        <option value="60">Letzte 1 Stunde</option>
                        <option value="1440">Letzte 24 Stunden</option>
                    </select>
                </div>
                <div class="col-12 col-md-3 d-flex gap-2">
                    <select class="form-select form-select-sm bg-dark border-secondary border-opacity-50 text-light" id="filterLimit" onchange="loadErrors()">
                        <option value="25">25 Einträge</option>
                        <option value="50" selected>50 Einträge</option>
                        <option value="100">100 Einträge</option>
                        <option value="200">200 Einträge</option>
                    </select>
                    <button type="button" class="btn btn-sm btn-outline-secondary text-nowrap rounded-3" onclick="resetFilters()">
                        Reset
                    </button>
                </div>
            </div>
        </div>

        <!-- Table Card -->
        <div class="card-custom p-0 mb-4 overflow-hidden">
            <div class="p-3 border-bottom border-secondary border-opacity-25 d-flex justify-content-between align-items-center">
                <h6 class="fw-bold mb-0 text-light">
                    {{icon:funnel:me-2:text-primary}}Fehlerübersicht
                </h6>
                <small class="text-secondary" id="tableStatusText">Lade Fehlerprotokoll...</small>
            </div>

            <!-- Empty State -->
            <div id="emptyState" class="p-5 text-center d-none">
                <div class="rounded-circle bg-success bg-opacity-10 text-success d-inline-flex p-3 mb-3">
                    {{icon:circle-check:fs-1}}
                </div>
                <h5 class="fw-bold text-light">Keine Fehler protokolliert!</h5>
                <p class="text-secondary small mb-0">Aktuell liegen keine Gateway- oder Upstream-Fehler für die gewählten Filter vor.</p>
            </div>

            <!-- Table Container -->
            <div class="table-responsive" id="tableContainer">
                <table class="table table-custom table-hover align-middle mb-0">
                    <thead class="text-secondary sticky-top" style="background: #111827;">
                        <tr>
                            <th style="width: 60px;">ID</th>
                            <th style="width: 140px;">Zeit</th>
                            <th style="width: 80px;">Methode</th>
                            <th style="width: 80px;">Status</th>
                            <th>Pfad</th>
                            <th>Fehlertyp</th>
                            <th>Begründung / Ursache</th>
                            <th class="text-end" style="width: 90px;">Aktion</th>
                        </tr>
                    </thead>
                    <tbody id="errorsTableBody">
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <!-- Error Detail Modal -->
    <div class="modal fade" id="errorDetailModal" tabindex="-1" aria-labelledby="errorDetailModalLabel" aria-hidden="true">
        <div class="modal-dialog modal-lg modal-dialog-centered modal-dialog-scrollable">
            <div class="modal-content border-secondary border-opacity-50" style="background: #111827;">
                <div class="modal-header border-secondary border-opacity-25">
                    <h6 class="modal-title fw-bold text-light d-flex align-items-center gap-2" id="errorDetailModalLabel">
                        {{icon:shield-alert:text-danger}} Fehlerdetails <span class="badge bg-secondary rounded-pill font-monospace" id="modalErrorId"></span>
                    </h6>
                    <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal" aria-label="Schließen"></button>
                </div>
                <div class="modal-body">
                    <!-- Meta Grid -->
                    <div class="row g-3 mb-3">
                        <div class="col-sm-6 col-md-4">
                            <small class="text-secondary d-block">Zeitstempel</small>
                            <span class="text-light fw-medium small" id="modalTimestamp"></span>
                        </div>
                        <div class="col-sm-6 col-md-2">
                            <small class="text-secondary d-block">Methode</small>
                            <span id="modalMethod"></span>
                        </div>
                        <div class="col-sm-6 col-md-2">
                            <small class="text-secondary d-block">Status</small>
                            <span id="modalStatus"></span>
                        </div>
                        <div class="col-sm-6 col-md-4">
                            <small class="text-secondary d-block">Client IP</small>
                            <span class="text-light font-monospace small" id="modalIp"></span>
                        </div>
                        <div class="col-12 col-md-6">
                            <small class="text-secondary d-block">Aufgerufener Pfad</small>
                            <span class="text-info font-monospace small text-break" id="modalPath"></span>
                        </div>
                        <div class="col-12 col-md-6">
                            <small class="text-secondary d-block">Fehlertyp</small>
                            <span class="badge bg-danger bg-opacity-25 text-danger small" id="modalType"></span>
                        </div>
                    </div>

                    <!-- Begründung Box -->
                    <div class="alert alert-danger bg-opacity-10 border-danger border-opacity-50 p-3 mb-3">
                        <small class="fw-bold text-danger d-block mb-1">Begründung / Erklärung:</small>
                        <div class="text-light fw-medium" id="modalBegruendung"></div>
                    </div>

                    <!-- Raw JSON Payload Box -->
                    <div class="d-flex justify-content-between align-items-center mb-1">
                        <small class="text-secondary fw-semibold">Vollständiger Fehler-Payload / Upstream FSM Response:</small>
                        <button type="button" class="btn btn-sm btn-outline-secondary py-0 px-2 small rounded-pill" id="btnCopyJson" onclick="copyJsonPayload()">
                            {{icon:copy:me-1}} Kopieren
                        </button>
                    </div>
                    <pre class="code-block mb-0" id="modalJsonPayload"></pre>
                </div>
                <div class="modal-footer border-secondary border-opacity-25">
                    <button type="button" class="btn btn-sm btn-secondary rounded-pill px-3" data-bs-dismiss="modal">Schließen</button>
                </div>
            </div>
        </div>
    </div>

    <script>
        let autoSyncInterval = null;
        let currentErrors = [];
        let debounceTimer = null;

        function escapeHtml(str) {
            if (!str) return '';
            return String(str).replace(/[&<>"']/g, m => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[m]));
        }

        function getMethodBadge(method) {
            const m = (method || '').toUpperCase();
            const cls = {
                'GET': 'badge-method-get',
                'POST': 'badge-method-post',
                'PUT': 'badge-method-put',
                'DELETE': 'badge-method-delete'
            }[m] || 'bg-secondary';
            return `<span class="badge ${cls} rounded-1 px-2 py-0 small">${m}</span>`;
        }

        function getStatusBadge(code) {
            if (!code) return '';
            let bg = 'bg-secondary';
            if (code >= 200 && code < 300) bg = 'bg-success bg-opacity-25 text-success border border-success border-opacity-25';
            else if (code >= 300 && code < 400) bg = 'bg-info bg-opacity-25 text-info border border-info border-opacity-25';
            else if (code >= 400 && code < 500) bg = 'bg-warning bg-opacity-25 text-warning border border-warning border-opacity-25';
            else if (code >= 500) bg = 'bg-danger bg-opacity-25 text-danger border border-danger border-opacity-25';
            return `<span class="badge ${bg} rounded-1 px-2 py-0 small">${code}</span>`;
        }

        async function loadErrors() {
            const statusFilter = document.getElementById('filterStatus').value;
            const timeFilter = document.getElementById('filterTime').value;
            const pathFilter = document.getElementById('filterPath').value.trim();
            const limitFilter = document.getElementById('filterLimit').value;

            const params = new URLSearchParams();
            params.set('limit', limitFilter);
            if (statusFilter) params.set('status_code', statusFilter);
            if (timeFilter) params.set('since_minutes', timeFilter);
            if (pathFilter) params.set('path', pathFilter);

            try {
                const res = await fetch('/dashboard/api/errors?' + params.toString());
                if (res.status === 401) {
                    window.location.reload();
                    return;
                }
                if (!res.ok) throw new Error('HTTP ' + res.status);
                const data = await res.json();
                currentErrors = data.errors || [];
                renderErrorsTable(currentErrors);
                updateKpis(data);

                const navBadge = document.getElementById('navErrorsBadge');
                if (navBadge) {
                    if (currentErrors.length > 0) {
                        navBadge.textContent = currentErrors.length;
                        navBadge.classList.remove('d-none');
                    } else {
                        navBadge.classList.add('d-none');
                    }
                }

                document.getElementById('tableStatusText').textContent = `${currentErrors.length} Fehler gefunden (Stand ${new Date().toLocaleTimeString('de-DE')})`;
            } catch (err) {
                console.error('Fehler beim Laden der Fehlerprotokolle:', err);
                document.getElementById('tableStatusText').textContent = 'Fehler beim Laden der Daten';
            }
        }

        function updateKpis(data) {
            const errs = data.errors || [];
            document.getElementById('kpiTotalErrors').textContent = Number(data.count || errs.length).toLocaleString('de-DE');

            const c4xx = errs.filter(e => e.status_code >= 400 && e.status_code < 500).length;
            document.getElementById('kpi4xxErrors').textContent = Number(c4xx).toLocaleString('de-DE');

            const c5xx = errs.filter(e => e.status_code >= 500).length;
            document.getElementById('kpi5xxErrors').textContent = Number(c5xx).toLocaleString('de-DE');

            const lastErr = data.last_error || (errs.length > 0 ? errs[0] : null);
            const lastErrEl = document.getElementById('kpiLastError');
            const lastErrSub = document.getElementById('kpiLastErrorSub');
            if (lastErr) {
                lastErrEl.textContent = `[${lastErr.status_code}] ${lastErr.time} Uhr`;
                lastErrEl.title = lastErr.begruendung || lastErr.message || '';
                lastErrSub.textContent = lastErr.path || '';
            } else {
                lastErrEl.textContent = 'Keine Fehler';
                lastErrEl.title = 'Keine Fehler';
                lastErrSub.textContent = 'System läuft stabil';
            }
        }

        function renderErrorsTable(errors) {
            const tbody = document.getElementById('errorsTableBody');
            const emptyState = document.getElementById('emptyState');
            const tableContainer = document.getElementById('tableContainer');

            if (!errors || errors.length === 0) {
                emptyState.classList.remove('d-none');
                tableContainer.classList.add('d-none');
                return;
            }

            emptyState.classList.add('d-none');
            tableContainer.classList.remove('d-none');

            tbody.innerHTML = errors.map((err, idx) => {
                const escMsg = escapeHtml(err.begruendung || err.message || '');
                const escPath = escapeHtml(err.path || '');
                const statusBadge = getStatusBadge(err.status_code);
                const methodBadge = getMethodBadge(err.method);
                return `<tr class="error-row" onclick="showErrorDetails(${idx})">
                    <td class="text-secondary small font-monospace">#${err.id || (idx + 1)}</td>
                    <td class="text-nowrap small text-secondary">${escapeHtml(err.date)} ${escapeHtml(err.time)}</td>
                    <td>${methodBadge}</td>
                    <td>${statusBadge}</td>
                    <td class="font-monospace text-light text-truncate small" style="max-width: 180px;" title="${escPath}">${escPath}</td>
                    <td><span class="badge bg-danger bg-opacity-25 text-danger small">${escapeHtml(err.error_type || 'Error')}</span></td>
                    <td class="text-light fw-medium small" style="max-width: 380px; word-break: break-word;">
                        <span class="text-danger me-1">●</span>${escMsg}
                    </td>
                    <td class="text-end text-nowrap">
                        <button type="button" class="btn btn-sm btn-outline-info rounded-pill px-2 py-0 small" onclick="event.stopPropagation(); showErrorDetails(${idx})">
                            Details
                        </button>
                    </td>
                </tr>`;
            }).join('');
        }

        function showErrorDetails(index) {
            const err = currentErrors[index];
            if (!err) return;

            document.getElementById('modalErrorId').textContent = err.id ? ('#' + err.id) : '';
            document.getElementById('modalTimestamp').textContent = (err.date || '') + ' ' + (err.time || '');
            document.getElementById('modalMethod').innerHTML = getMethodBadge(err.method);
            document.getElementById('modalStatus').innerHTML = getStatusBadge(err.status_code);
            document.getElementById('modalPath').textContent = err.path || '';
            document.getElementById('modalType').textContent = err.error_type || '';
            document.getElementById('modalIp').textContent = err.client_ip || 'Unbekannt';
            document.getElementById('modalBegruendung').textContent = err.begruendung || err.message || 'Keine Begründung angegeben';

            const payloadObj = {
                id: err.id,
                timestamp: err.timestamp,
                method: err.method,
                path: err.path,
                status_code: err.status_code,
                error_type: err.error_type,
                begruendung: err.begruendung,
                message: err.message,
                details: err.details,
                client_ip: err.client_ip
            };

            document.getElementById('modalJsonPayload').textContent = JSON.stringify(payloadObj, null, 2);

            const modalEl = document.getElementById('errorDetailModal');
            const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
            modal.show();
        }

        function copyJsonPayload() {
            const code = document.getElementById('modalJsonPayload').textContent;
            navigator.clipboard.writeText(code).then(() => {
                const btn = document.getElementById('btnCopyJson');
                const orig = btn.innerHTML;
                btn.innerHTML = 'Kopiert!';
                setTimeout(() => { btn.innerHTML = orig; }, 2000);
            });
        }

        function debounceLoadErrors() {
            clearTimeout(debounceTimer);
            debounceTimer = setTimeout(loadErrors, 300);
        }

        function resetFilters() {
            document.getElementById('filterPath').value = '';
            document.getElementById('filterStatus').value = '';
            document.getElementById('filterTime').value = '';
            document.getElementById('filterLimit').value = '50';
            loadErrors();
        }

        function toggleAutoSync() {
            const chk = document.getElementById('chkAutoSync');
            const dot = document.getElementById('autoSyncDot');
            if (chk.checked) {
                dot.style.display = 'inline-block';
                if (!autoSyncInterval) {
                    autoSyncInterval = setInterval(loadErrors, 5000);
                }
            } else {
                dot.style.display = 'none';
                if (autoSyncInterval) {
                    clearInterval(autoSyncInterval);
                    autoSyncInterval = null;
                }
            }
        }

        async function clearAllErrors() {
            if (!confirm('Möchten Sie wirklich alle protokollierten Fehler unwiderruflich löschen?')) return;
            try {
                const res = await fetch('/dashboard/api/errors', { method: 'DELETE' });
                if (res.ok) {
                    await loadErrors();
                } else {
                    alert('Fehler beim Löschen des Protokolls');
                }
            } catch (err) {
                alert('Verbindungsfehler zum Gateway');
            }
        }

        async function logout() {
            await fetch('/dashboard/api/logout', { method: 'POST' });
            window.location.href = '/dashboard';
        }

        async function clearGatewayCache() {
            if (!confirm('Möchten Sie wirklich den gesamten Cache leeren?')) return;
            try {
                const resp = await fetch('/dashboard/api/cache/clear', { method: 'POST' });
                const data = await resp.json();
                alert(data.message);
            } catch (err) {
                alert('Fehler beim Leeren des Caches');
            }
        }

        // Initial load
        loadErrors();
        autoSyncInterval = setInterval(loadErrors, 5000);
    </script>
</body>
</html>""")
