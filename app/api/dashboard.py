"""Dashboard Web UI, Statistics API, and Prometheus /metrics endpoint."""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import Any

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from pydantic import BaseModel

from app.core.cache import cache
from app.core.client import fsm_client
from app.core.config import settings
from app.core.metrics import metrics_collector

router = APIRouter(tags=["Monitoring & Dashboard"])

# Cookie name for dashboard authentication session
SESSION_COOKIE_NAME = "fsm_dash_auth"


def _generate_session_token(password: str) -> str:
    """Generates an HMAC session token derived from the dashboard password."""
    secret = settings.DASHBOARD_PASSWORD or "fsm-gateway-default-key"
    ts = str(int(time.time() // 86400))  # Valid for the day
    return hmac.new(secret.encode(), ts.encode(), hashlib.sha256).hexdigest()


def _is_authenticated(
    cookie_token: str | None = None,
    auth_header: str | None = None,
) -> bool:
    """Checks if the user is authorized to access the dashboard."""
    # If no password is configured in settings, dashboard is open
    if not settings.DASHBOARD_PASSWORD:
        return True

    # Check cookie token
    expected_token = _generate_session_token(settings.DASHBOARD_PASSWORD)
    if cookie_token and hmac.compare_digest(cookie_token, expected_token):
        return True

    # Check HTTP Basic Auth header
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
    token = await fsm_client.get_auth_token()
    cloud_status = {
        "authenticated": bool(token),
        "cached_entities_count": await cache.size(),
    }
    return {
        "live": live,
        "recent": recent,
        "cloud_status": cloud_status,
    }



@router.get("/dashboard", response_class=HTMLResponse, summary="Gateway Monitoring Dashboard")
async def dashboard_view(
    request: Request,
    fsm_dash_auth: str | None = Cookie(None),
    authorization: str | None = Header(None),
) -> HTMLResponse:
    """Serves the interactive monitoring web dashboard."""
    is_auth = _is_authenticated(fsm_dash_auth, authorization)
    has_password = bool(settings.DASHBOARD_PASSWORD)

    if has_password and not is_auth:
        return HTMLResponse(_render_login_html())

    return HTMLResponse(_render_dashboard_html())


def _render_login_html() -> str:
    """HTML for password login screen."""
    return """<!DOCTYPE html>
<html lang="de" data-bs-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>FSM Gateway • Login</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css">
    <style>
        body {
            font-family: 'Inter', system-ui, -apple-system, sans-serif;
            background: #0f172a;
            color: #f8fafc;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .login-card {
            background: #1e293b;
            border: 1px solid #334155;
            border-radius: 1rem;
            width: 100%;
            max-width: 400px;
            box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.5);
        }
        .btn-primary {
            background: #3b82f6;
            border-color: #3b82f6;
        }
        .btn-primary:hover {
            background: #2563eb;
            border-color: #2563eb;
        }
    </style>
</head>
<body>
    <div class="p-4 login-card text-center">
        <div class="rounded-circle bg-primary bg-opacity-10 text-primary d-inline-flex p-3 mb-3">
            <i class="bi bi-shield-lock-fill fs-2"></i>
        </div>
        <h4 class="fw-bold mb-1">FSM Gateway</h4>
        <p class="text-secondary small mb-4">Authentifizierung für Dashboard erforderlich</p>

        <form id="loginForm" onsubmit="handleLogin(event)">
            <div class="mb-3 text-start">
                <label for="password" class="form-label small text-secondary">Admin Passwort</label>
                <input type="password" class="form-control bg-dark border-secondary text-light py-2" id="password" required autofocus placeholder="Passwort eingeben">
            </div>
            <div id="errorAlert" class="alert alert-danger py-2 small d-none" role="alert"></div>
            <button type="submit" class="btn btn-primary w-100 py-2 fw-semibold rounded-3" id="submitBtn">
                <i class="bi bi-box-arrow-in-right me-1"></i> Anmelden
            </button>
        </form>
    </div>

    <script>
        async function handleLogin(e) {
            e.preventDefault();
            const btn = document.getElementById('submitBtn');
            const alert = document.getElementById('errorAlert');
            const password = document.getElementById('password').value;
            
            btn.disabled = true;
            alert.classList.add('d-none');

            try {
                const res = await fetch('/dashboard/api/login', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ password })
                });
                if (res.ok) {
                    window.location.reload();
                } else {
                    const data = await res.json();
                    alert.textContent = data.detail || 'Falsches Passwort';
                    alert.classList.remove('d-none');
                }
            } catch (err) {
                alert.textContent = 'Verbindungsfehler zum Gateway';
                alert.classList.remove('d-none');
            } finally {
                btn.disabled = false;
            }
        }
    </script>
</body>
</html>"""


def _render_dashboard_html() -> str:
    """HTML for the modern interactive metrics dashboard."""
    return """<!DOCTYPE html>
<html lang="de" data-bs-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>FSM Gateway • Dashboard</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css">
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.2/dist/chart.umd.min.js"></script>
    <style>
        body {
            font-family: 'Inter', system-ui, -apple-system, sans-serif;
            background: #0b1120;
            color: #f1f5f9;
            min-height: 100vh;
        }
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
                <div class="d-flex align-items-center gap-2">
                    <span class="fs-4">🚗⚡</span>
                    <span class="fw-bold fs-5 text-white">FSM Gateway</span>
                    <span class="badge bg-secondary bg-opacity-25 text-secondary border border-secondary border-opacity-25 rounded-pill px-2 py-1 small">v1.0.0</span>
                </div>
                <div class="d-none d-md-flex align-items-center gap-2 ms-3 ps-3 border-start border-secondary border-opacity-25">
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

                <a href="/docs" target="_blank" class="btn btn-sm btn-outline-secondary rounded-pill px-3 text-decoration-none">
                    <i class="bi bi-code-slash me-1"></i> API Docs
                </a>
                <button type="button" class="btn btn-sm btn-outline-danger rounded-pill px-3" onclick="logout()">
                    <i class="bi bi-box-arrow-right"></i>
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
                        <i class="bi bi-activity text-primary fs-5"></i>
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
                        <i class="bi bi-lightning-charge-fill text-warning fs-5"></i>
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
                        <span class="small fw-semibold">Durchschnittliche Latenz</span>
                        <i class="bi bi-stopwatch-fill text-info fs-5"></i>
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
                        <i class="bi bi-shield-check text-success fs-5"></i>
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
                        <h6 class="fw-bold mb-0 text-light"><i class="bi bi-bar-chart-fill text-primary me-2"></i>Anfragevolumen & Cache-Treffer</h6>
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
                        <h6 class="fw-bold mb-0 text-light"><i class="bi bi-pie-chart-fill text-info me-2"></i>HTTP Statusverteilung</h6>
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
                    <h6 class="fw-bold mb-3 text-light"><i class="bi bi-trophy-fill text-warning me-2"></i>Meistaufgerufene Endpunkte</h6>
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
    </div>

    <script>
        let currentRange = '24h';
        let trafficChart = null;
        let statusChart = null;

        function setRange(range) {
            currentRange = range;
            document.querySelectorAll('.range-btn').forEach(btn => {
                btn.classList.remove('btn-primary');
                btn.classList.add('btn-dark');
            });
            event.target.classList.remove('btn-dark');
            event.target.classList.add('btn-primary');
            document.getElementById('chartRangeLabel').textContent = range === '24h' ? '24 Stunden' : (range === '7d' ? '7 Tage' : '30 Tage');
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

                // Render Traffic Chart
                renderTrafficChart(data.timeseries);

                // Render Status Chart
                renderStatusChart(data.status_codes);

                // Render Top Endpoints
                renderTopEndpoints(data.top_endpoints);
            } catch (err) {
                console.error('Error loading stats:', err);
            }
        }

        function renderTrafficChart(timeseries) {
            const labels = timeseries.map(t => t.time);
            const totalData = timeseries.map(t => t.total);
            const cachedData = timeseries.map(t => t.cached);
            const errorData = timeseries.map(t => t.errors);

            const ctx = document.getElementById('trafficChart').getContext('2d');
            if (trafficChart) trafficChart.destroy();

            trafficChart = new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: labels,
                    datasets: [
                        {
                            label: 'Direkte Requests',
                            data: totalData.map((tot, idx) => Math.max(0, tot - cachedData[idx])),
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
            const labels = Object.keys(statusCodes);
            const data = Object.values(statusCodes);
            const colors = labels.map(code => {
                const c = parseInt(code, 10);
                if (c >= 200 && c < 300) return '#10b981';
                if (c >= 300 && c < 400) return '#06b6d4';
                if (c >= 400 && c < 500) return '#f59e0b';
                return '#ef4444';
            });

            const ctx = document.getElementById('statusChart').getContext('2d');
            if (statusChart) statusChart.destroy();

            if (labels.length === 0) {
                labels.push('Keine Daten');
                data.push(1);
                colors.push('#374151');
            }

            statusChart = new Chart(ctx, {
                type: 'doughnut',
                data: {
                    labels: labels,
                    datasets: [{ data: data, backgroundColor: colors, borderWidth: 0 }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { position: 'bottom', labels: { color: '#cbd5e1', font: { size: 11 } } }
                    }
                }
            });
        }

        function renderTopEndpoints(endpoints) {
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
            } catch (err) {
                console.error('Error fetching live feed:', err);
            }
        }

        // Initialize dashboard
        loadStats();
        loadLiveFeed();

        // Refresh intervals: live feed every 3s, charts every 15s
        setInterval(loadLiveFeed, 3000);
        setInterval(loadStats, 15000);
    </script>
</body>
</html>"""
