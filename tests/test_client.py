"""Unit tests for FSMClient."""

import datetime as dt

import httpx
import pytest
import respx

from app.core.client import FSMClient


@pytest.mark.asyncio
async def test_pkce_pair():
    client = FSMClient()
    verifier, challenge = client._pkce_pair()
    assert len(verifier) >= 32
    assert len(challenge) >= 32
    assert verifier != challenge


@pytest.mark.asyncio
@respx.mock
async def test_auto_login_flow():
    client = FSMClient(
        base_url="https://api.fahrschulmanager.de",
        auth_url="https://login.fahren-lernen.de",
        portal_url="https://portal.fahrschulmanager.de",
    )

    # 1. Authorize GET
    respx.get("https://login.fahren-lernen.de/connect/authorize").respond(
        status_code=200,
        text='<html><head><meta name="xsrf" content="test-xsrf-token" /></head><body></body></html>',
    )

    # 2. Login POST
    respx.post("https://login.fahren-lernen.de/account/login").respond(
        status_code=200,
        json="/connect/authorize/callback?code=mock-auth-code-1234",
    )

    # 3. Callback GET
    respx.get("https://login.fahren-lernen.de/connect/authorize/callback").respond(
        status_code=302,
        headers={"Location": "https://portal.fahrschulmanager.de/login?code=mock-auth-code-1234"},
    )

    # 4. Token POST
    respx.post("https://login.fahren-lernen.de/connect/token").respond(
        status_code=200,
        json={"access_token": "mock-oidc-access-token", "token_type": "Bearer"},
    )

    # 5. BFF XSRF-Token
    respx.get("https://portal.fahrschulmanager.de/bff/xsrf-token").respond(
        status_code=204,
        headers={"Set-Cookie": "__Host-XSRF-TOKEN=test-xsrf; Path=/; Secure"},
    )

    # 6. BFF SSO Exchange
    respx.post(url__regex=r"https://portal\.fahrschulmanager\.de/bff/auth/sso/exchange.*").respond(
        status_code=200,
        json={"viewModel": {"authToken": "final-fsm-bearer-token-abc"}},
    )

    token = await client.auto_login(email="test@user.de", password="password123")
    assert token == "final-fsm-bearer-token-abc"
    assert await client.get_auth_token() == "final-fsm-bearer-token-abc"


@pytest.mark.asyncio
@respx.mock
async def test_request_with_401_retry():
    client = FSMClient(
        base_url="https://api.fahrschulmanager.de",
        auth_url="https://login.fahren-lernen.de",
        portal_url="https://portal.fahrschulmanager.de",
        auth_token="initial-expired-token",
    )

    # First request returns 401
    route = respx.get("https://api.fahrschulmanager.de/v1/lehrer/fahrlehrer")
    route.side_effect = [
        httpx.Response(401, json={"message": "Token expired"}),
        httpx.Response(200, json=[{"id": "fl-1", "vorname": "Max", "nachname": "Mustermann"}]),
    ]

    # Setup auto-login mock endpoints
    respx.get("https://login.fahren-lernen.de/connect/authorize").respond(
        status_code=200,
        text='<html><meta name="xsrf" content="test-xsrf-token" /></html>',
    )
    respx.post("https://login.fahren-lernen.de/account/login").respond(
        status_code=200,
        json="/connect/authorize/callback?code=mock-code",
    )
    respx.get("https://login.fahren-lernen.de/connect/authorize/callback").respond(
        status_code=302,
        headers={"Location": "https://portal.fahrschulmanager.de/login?code=mock-code"},
    )
    respx.post("https://login.fahren-lernen.de/connect/token").respond(
        status_code=200,
        json={"access_token": "mock-oidc-token"},
    )
    respx.get("https://portal.fahrschulmanager.de/bff/xsrf-token").respond(
        status_code=204,
        headers={"Set-Cookie": "__Host-XSRF-TOKEN=test-xsrf; Path=/; Secure"},
    )
    respx.post(url__regex=r"https://portal\.fahrschulmanager\.de/bff/auth/sso/exchange.*").respond(
        status_code=200,
        json={"viewModel": {"authToken": "refreshed-fsm-token"}},
    )

    res = await client.request("GET", "v1/lehrer/fahrlehrer", params={"onlyActive": "true"})
    assert isinstance(res, list)
    assert res[0]["vorname"] == "Max"
    assert await client.get_auth_token() == "refreshed-fsm-token"


@pytest.mark.asyncio
@respx.mock
async def test_create_termin_chunking_over_600_minutes():
    client = FSMClient(base_url="https://api.fahrschulmanager.de", auth_token="valid-token")

    # Mock POST /v1/termine
    post_route = respx.post("https://api.fahrschulmanager.de/v1/termine")
    post_route.side_effect = [
        httpx.Response(200, json={"viewModel": {"id": "block-id-1"}}),
        httpx.Response(200, json={"viewModel": {"id": "block-id-2"}}),
    ]

    # 12 hours block = 720 minutes (> 600 min) -> should be split into 2 chunks
    start = dt.datetime(2026, 8, 20, 8, 0, 0)
    end = dt.datetime(2026, 8, 20, 20, 0, 0)

    created_ids = await client.create_termin(
        fahrlehrer_id="fl-uuid-1",
        von=start,
        bis=end,
        titel="Ganztägige Schulung",
    )

    assert len(created_ids) == 2
    assert created_ids == ["block-id-1", "block-id-2"]
    assert post_route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_request_with_403_retry():
    client = FSMClient(
        base_url="https://api.fahrschulmanager.de",
        auth_url="https://login.fahren-lernen.de",
        portal_url="https://portal.fahrschulmanager.de",
        auth_token="initial-expired-token",
    )

    # First request returns 403 Forbidden
    route = respx.get("https://api.fahrschulmanager.de/v1/lehrer/fahrlehrer")
    route.side_effect = [
        httpx.Response(403, json={"message": "Forbidden / Session expired"}),
        httpx.Response(200, json=[{"id": "fl-1", "vorname": "Max", "nachname": "Mustermann"}]),
    ]

    # Setup auto-login mock endpoints
    respx.get("https://login.fahren-lernen.de/connect/authorize").respond(
        status_code=200,
        text='<html><meta name="xsrf" content="test-xsrf-token" /></html>',
    )
    respx.post("https://login.fahren-lernen.de/account/login").respond(
        status_code=200,
        json="/connect/authorize/callback?code=mock-code",
    )
    respx.get("https://login.fahren-lernen.de/connect/authorize/callback").respond(
        status_code=302,
        headers={"Location": "https://portal.fahrschulmanager.de/login?code=mock-code"},
    )
    respx.post("https://login.fahren-lernen.de/connect/token").respond(
        status_code=200,
        json={"access_token": "mock-oidc-token"},
    )
    respx.get("https://portal.fahrschulmanager.de/bff/xsrf-token").respond(
        status_code=204,
        headers={"Set-Cookie": "__Host-XSRF-TOKEN=test-xsrf; Path=/; Secure"},
    )
    respx.post(url__regex=r"https://portal\.fahrschulmanager\.de/bff/auth/sso/exchange.*").respond(
        status_code=200,
        json={"viewModel": {"authToken": "refreshed-fsm-token-403"}},
    )

    res = await client.request("GET", "v1/lehrer/fahrlehrer", params={"onlyActive": "true"})
    assert isinstance(res, list)
    assert res[0]["vorname"] == "Max"
    assert await client.get_auth_token() == "refreshed-fsm-token-403"


@pytest.mark.asyncio
@respx.mock
async def test_request_with_login_redirect_retry():
    client = FSMClient(
        base_url="https://api.fahrschulmanager.de",
        auth_url="https://login.fahren-lernen.de",
        portal_url="https://portal.fahrschulmanager.de",
        auth_token="initial-expired-token",
    )

    # First request redirected to login page (302 -> /login -> 200 OK HTML)
    route = respx.get("https://api.fahrschulmanager.de/v1/lehrer/fahrlehrer")
    route.side_effect = [
        httpx.Response(302, headers={"Location": "https://portal.fahrschulmanager.de/login"}),
        httpx.Response(200, json=[{"id": "fl-1", "vorname": "Max", "nachname": "Mustermann"}]),
    ]
    respx.get("https://portal.fahrschulmanager.de/login").respond(
        status_code=200,
        text="<html>Login Required</html>",
    )

    # Setup auto-login mock endpoints
    respx.get("https://login.fahren-lernen.de/connect/authorize").respond(
        status_code=200,
        text='<html><meta name="xsrf" content="test-xsrf-token" /></html>',
    )
    respx.post("https://login.fahren-lernen.de/account/login").respond(
        status_code=200,
        json="/connect/authorize/callback?code=mock-code",
    )
    respx.get("https://login.fahren-lernen.de/connect/authorize/callback").respond(
        status_code=302,
        headers={"Location": "https://portal.fahrschulmanager.de/login?code=mock-code"},
    )
    respx.post("https://login.fahren-lernen.de/connect/token").respond(
        status_code=200,
        json={"access_token": "mock-oidc-token"},
    )
    respx.get("https://portal.fahrschulmanager.de/bff/xsrf-token").respond(
        status_code=204,
        headers={"Set-Cookie": "__Host-XSRF-TOKEN=test-xsrf; Path=/; Secure"},
    )
    respx.post(url__regex=r"https://portal\.fahrschulmanager\.de/bff/auth/sso/exchange.*").respond(
        status_code=200,
        json={"viewModel": {"authToken": "refreshed-fsm-token-redirect"}},
    )

    res = await client.request("GET", "v1/lehrer/fahrlehrer", params={"onlyActive": "true"})
    assert isinstance(res, list)
    assert res[0]["vorname"] == "Max"
    assert await client.get_auth_token() == "refreshed-fsm-token-redirect"


@pytest.mark.asyncio
@respx.mock
async def test_search_schueler_three_words_and_umlaut_fallback():
    client = FSMClient(
        base_url="https://api.fahrschulmanager.de",
        auth_url="https://login.fahren-lernen.de",
        portal_url="https://portal.fahrschulmanager.de",
        auth_token="valid-token",
    )

    def _mock_search(request: httpx.Request) -> httpx.Response:
        vn = request.url.params.get("vorname", "")
        nn = request.url.params.get("name", "")
        if vn == "Hans Peter" and nn == "Schmidt":
            return httpx.Response(200, json={"rows": [{"data": {"id": "s-hp", "vorname": "Hans Peter", "nachname": "Schmidt"}}]})
        if vn == "Björn" and nn == "Müller":
            return httpx.Response(200, json={"rows": [{"data": {"id": "s-bm", "vorname": "Björn", "nachname": "Müller"}}]})
        return httpx.Response(200, json={"rows": []})

    respx.get("https://api.fahrschulmanager.de/v2/schueler/suche").mock(side_effect=_mock_search)

    res1 = await client.search_schueler(query="Hans Peter Schmidt")
    assert len(res1["rows"]) == 1
    assert res1["rows"][0]["data"]["id"] == "s-hp"

    res2 = await client.search_schueler(query="Bjoern Mueller")
    assert len(res2["rows"]) == 1
    assert res2["rows"][0]["data"]["id"] == "s-bm"


