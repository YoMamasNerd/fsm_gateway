"""API v1 Package."""

from fastapi import APIRouter

from app.api.v1.auth import router as auth_router
from app.api.v1.fahrlehrer import router as fahrlehrer_router
from app.api.v1.finanzen import router as finanzen_router
from app.api.v1.kalender import router as kalender_router
from app.api.v1.schueler import router as schueler_router
from app.api.v1.webhooks import router as webhooks_router

api_v1_router = APIRouter(prefix="/v1")
api_v1_router.include_router(auth_router)
api_v1_router.include_router(fahrlehrer_router)
api_v1_router.include_router(kalender_router)
api_v1_router.include_router(schueler_router)
api_v1_router.include_router(finanzen_router)
api_v1_router.include_router(webhooks_router)
