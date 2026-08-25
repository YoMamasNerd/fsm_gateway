"""API Root Router."""

from fastapi import APIRouter

from app.api.v1 import api_v1_router

main_router = APIRouter()
main_router.include_router(api_v1_router)

# Also expose un-prefixed routes for direct compatibility if needed
# (e.g. /fahrlehrer -> /v1/fahrlehrer, /kalender -> /v1/kalender, etc.)
from app.api.v1.auth import router as direct_auth
from app.api.v1.fahrlehrer import router as direct_fahrlehrer
from app.api.v1.finanzen import router as direct_finanzen
from app.api.v1.fuhrpark import router as direct_fuhrpark
from app.api.v1.kalender import router as direct_kalender
from app.api.v1.kassenbuch import router as direct_kassenbuch
from app.api.v1.preislisten import router as direct_preislisten
from app.api.v1.schueler import router as direct_schueler
from app.api.v1.stammdaten import router as direct_stammdaten
from app.api.v1.statistiken import router as direct_statistiken
from app.api.v1.webhooks import router as direct_webhooks

from app.api.dashboard import router as dashboard_router

compat_router = APIRouter(include_in_schema=False)
compat_router.include_router(direct_auth)
compat_router.include_router(direct_fahrlehrer)
compat_router.include_router(direct_kalender)
compat_router.include_router(direct_schueler)
compat_router.include_router(direct_finanzen)
compat_router.include_router(direct_fuhrpark)
compat_router.include_router(direct_preislisten)
compat_router.include_router(direct_stammdaten)
compat_router.include_router(direct_statistiken)
compat_router.include_router(direct_kassenbuch)
compat_router.include_router(direct_webhooks)

main_router.include_router(dashboard_router)
main_router.include_router(compat_router)

