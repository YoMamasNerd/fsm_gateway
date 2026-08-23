"""Main FastAPI application entry point."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any
from pathlib import Path

from fastapi import Depends, FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

import time

from app.api.router import main_router
from app.core.cache import cache
from app.core.client import (
    FsmApiError,
    FsmAuthError,
    FsmConfigError,
    FsmException,
    fsm_client,
)
from app.core.config import settings
from app.core.metrics import metrics_collector
from app.core.security import verify_gateway_api_key

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("fsm_gateway")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: Startup & Shutdown events."""
    logger.info("🚀 FSM-Gateway gestartet auf Port %s (Target: %s)", settings.GATEWAY_PORT, settings.FSM_BASE_URL)
    
    # Initialize cache backend (Valkey if configured, or memory fallback)
    await cache.init()

    # Initialize metrics collector
    await metrics_collector.start()

    # Pre-seed token or trigger auto-login on startup in background
    if settings.FSM_AUTH_TOKEN:
        await fsm_client.set_auth_token(settings.FSM_AUTH_TOKEN)
        logger.info("Pre-seeded Auth Token gesetzt.")
    elif settings.FSM_EMAIL and settings.FSM_PASSWORD:
        async def _warmup_login():
            try:
                logger.info("Führe initialen Auto-Login beim Serverstart durch...")
                await fsm_client.auto_login()
                logger.info("✅ Initialer Auto-Login beim Start erfolgreich abgeschlossen.")
            except Exception as exc:
                logger.warning("Initialer Auto-Login beim Start fehlgeschlagen (wird bei Bedarf wiederholt): %s", exc)

        asyncio.create_task(_warmup_login())

    # Start periodic background cleanup task (cache every 60s, metrics every 24h)
    async def _periodic_cleanup():
        last_metrics_clean = time.time()
        while True:
            try:
                await asyncio.sleep(60)
                purged = await cache.cleanup()
                if purged > 0:
                    logger.debug("Cache Cleanup: %s abgelaufene Einträge entfernt.", purged)

                # Daily metrics retention cleanup
                if time.time() - last_metrics_clean > 86400:
                    deleted_metrics = await metrics_collector.cleanup_old_records()
                    if deleted_metrics > 0:
                        logger.info("Metrics Cleanup: %s alte Metrik-Einträge entfernt.", deleted_metrics)
                    last_metrics_clean = time.time()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("Fehler beim periodischen Cleanup: %s", e)

    cleanup_task = asyncio.create_task(_periodic_cleanup())

    yield

    logger.info("🛑 FSM-Gateway wird beendet. Schließe Verbindungen...")
    cleanup_task.cancel()
    await metrics_collector.stop()
    await fsm_client.close()
    await cache.close()


app = FastAPI(
    title="FSM-Gateway 🚗⚡",
    description=(
        "Zentraler FastAPI-Microservice für alle Interaktionen mit der **Fahrschulmanager (FSM)** API. "
        "Dient als Single Source of Truth für `schalti_termine`, `django_rechn`, `django_diacard` und SumUp-Zahlungen."
    ),
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
    dependencies=[Depends(verify_gateway_api_key)],
)

# Mount static directory for favicons & assets
static_path = Path(__file__).resolve().parent.parent / "static"
if static_path.is_dir():
    app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

# Metrics Middleware (records timing, status code, cache-hit for every request)
@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    duration_ms = (time.time() - start_time) * 1000.0
    cached = response.headers.get("X-Cache-Hit") == "1"
    client_ip = request.client.host if request.client else ""
    metrics_collector.record_request(
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        duration_ms=duration_ms,
        cached=cached,
        client_ip=client_ip,
    )
    return response


# CORS Configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Exception Handlers
@app.exception_handler(FsmAuthError)
async def fsm_auth_exception_handler(request: Request, exc: FsmAuthError):
    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content={"detail": str(exc), "error_type": "FsmAuthError"},
    )


@app.exception_handler(FsmConfigError)
async def fsm_config_exception_handler(request: Request, exc: FsmConfigError):
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": str(exc), "error_type": "FsmConfigError"},
    )


@app.exception_handler(FsmApiError)
async def fsm_api_exception_handler(request: Request, exc: FsmApiError):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "detail": str(exc),
            "error_type": "FsmApiError",
            "fsm_response": exc.response_body,
        },
    )


@app.exception_handler(FsmException)
async def fsm_generic_exception_handler(request: Request, exc: FsmException):
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY,
        content={"detail": str(exc), "error_type": "FsmException"},
    )


# Root & Healthcheck Endpoints
@app.get(
    "/",
    tags=["System"],
    summary="Gateway Root Dashboard Redirect",
    include_in_schema=False,
)
async def root_redirect():
    return RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)


@app.get(
    "/health",
    tags=["System"],
    summary="Healthcheck",
)
async def healthcheck() -> dict[str, Any]:
    cache_items = await cache.size()
    token = await fsm_client.get_auth_token()
    return {
        "status": "healthy",
        "service": "fsm_gateway",
        "cache_items": cache_items,
        "cache_backend": cache.get_info(),
        "has_token": bool(token),
    }


@app.api_route("/favicon.ico", methods=["GET", "HEAD"], include_in_schema=False)
async def favicon_ico():
    fav_path = static_path / "favicon.ico"
    if fav_path.is_file():
        return FileResponse(fav_path, media_type="image/x-icon")
    return Response(status_code=204)


@app.api_route("/favicon.svg", methods=["GET", "HEAD"], include_in_schema=False)
async def favicon_svg():
    fav_path = static_path / "favicon.svg"
    if fav_path.is_file():
        return FileResponse(fav_path, media_type="image/svg+xml")
    return Response(status_code=204)


@app.api_route("/favicon.png", methods=["GET", "HEAD"], include_in_schema=False)
async def favicon_png():
    fav_path = static_path / "favicon-32x32.png"
    if fav_path.is_file():
        return FileResponse(fav_path, media_type="image/png")
    return Response(status_code=204)


@app.api_route("/apple-touch-icon.png", methods=["GET", "HEAD"], include_in_schema=False)
@app.api_route("/apple-touch-icon-precomposed.png", methods=["GET", "HEAD"], include_in_schema=False)
async def apple_touch_icon():
    fav_path = static_path / "apple-touch-icon.png"
    if fav_path.is_file():
        return FileResponse(fav_path, media_type="image/png")
    return Response(status_code=204)


# Include Routers
app.include_router(main_router)


# Self-hosted docs routes (built-in ones disabled above) so that the
# app-level API-key/network dependency applies to them too.
from fastapi.openapi.docs import get_swagger_ui_html, get_redoc_html


@app.get("/openapi.json", include_in_schema=False, dependencies=[Depends(verify_gateway_api_key)])
async def custom_openapi():
    return app.openapi()


@app.get("/docs", include_in_schema=False, dependencies=[Depends(verify_gateway_api_key)])
async def custom_swagger():
    return get_swagger_ui_html(openapi_url="/openapi.json", title=app.title + " - Docs")


@app.get("/redoc", include_in_schema=False, dependencies=[Depends(verify_gateway_api_key)])
async def custom_redoc():
    return get_redoc_html(openapi_url="/openapi.json", title=app.title + " - Docs")




if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.GATEWAY_HOST,
        port=settings.GATEWAY_PORT,
        reload=False,
        log_level=settings.LOG_LEVEL.lower(),
    )
