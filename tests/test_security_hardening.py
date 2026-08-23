"""Tests für die Netzwerk-/API-Key-Absicherung (Docs, Metrics, v1)."""

import httpx
import pytest

from app.main import app

from .conftest import *  # noqa: F401,F403


def _public_client():
    """Client ohne API-Key, der von einer öffentlichen IP kommt."""
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("203.0.113.50", 9999)),
        base_url="http://testserver",
    )


@pytest.mark.asyncio
async def test_docs_blocked_from_public_ip():
    async with _public_client() as client:
        resp = await client.get("/docs")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_openapi_blocked_from_public_ip():
    async with _public_client() as client:
        resp = await client.get("/openapi.json")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_metrics_blocked_from_public_ip():
    async with _public_client() as client:
        resp = await client.get("/metrics")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_v1_blocked_from_public_ip():
    async with _public_client() as client:
        resp = await client.get("/v1/fahrlehrer")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_docs_allowed_from_private_ip(async_client):
    resp = await async_client.get("/openapi.json")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_internal_trusted_without_key(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "GATEWAY_API_KEY", "")
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("172.18.0.5", 1234)),
        base_url="http://testserver",
    )
    async with client:
        resp = await client.get("/health")
    assert resp.status_code == 200  # not 401/403
