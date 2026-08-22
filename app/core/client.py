"""Central FSM API Client with OAuth2 PKCE login, connection pooling and auto-refresh."""

from __future__ import annotations

import asyncio
import base64
import datetime as dt
import hashlib
import json
import logging
import re
import secrets
import urllib.parse
import uuid
import zoneinfo
from typing import Any

import httpx

from app.core.cache import cache
from app.core.config import settings

logger = logging.getLogger("fsm_gateway.client")

CACHE_KEY_AUTH_TOKEN = "fsm:auth_token"
CACHE_KEY_API_KEY = "fsm:api_key"
CACHE_KEY_FAHRLEHRER = "fsm:fahrlehrer:active"
BERLIN_TZ = zoneinfo.ZoneInfo("Europe/Berlin")


class FsmException(Exception):
    """Base exception for FSM errors."""


class FsmConfigError(FsmException):
    """Raised when configuration values are missing."""


class FsmAuthError(FsmException):
    """Raised when authentication fails."""


class FsmApiError(FsmException):
    """Raised when FSM API returns an HTTP error."""

    def __init__(self, status_code: int, message: str, response_body: Any = None):
        super().__init__(f"FSM API Error {status_code}: {message}")
        self.status_code = status_code
        self.response_body = response_body


def _normalize_iso_datetime(dt_val: dt.date | dt.datetime | str, is_end: bool = False) -> str:
    """Safely converts date or datetime into timezone-aware ISO string for FSM."""
    if isinstance(dt_val, dt.datetime):
        if dt_val.tzinfo is None:
            # Assume local German time if naive
            dt_val = dt_val.replace(tzinfo=BERLIN_TZ)
        return dt_val.isoformat()
    elif isinstance(dt_val, dt.date):
        t = dt.time(23, 59, 59) if is_end else dt.time(0, 0, 0)
        dt_obj = dt.datetime.combine(dt_val, t, tzinfo=BERLIN_TZ)
        return dt_obj.isoformat()
    return str(dt_val)


class FSMClient:
    """Async HTTP Client for Fahrschulmanager API communication."""

    def __init__(
        self,
        base_url: str | None = None,
        auth_url: str | None = None,
        portal_url: str | None = None,
        api_key: str | None = None,
        auth_token: str | None = None,
        timeout: float | None = None,
    ):
        # Normalize base URL removing any accidental trailing /v1, /v2, /v3
        raw_base = (base_url or settings.FSM_BASE_URL).rstrip("/")
        for v_suffix in ("/v1", "/v2", "/v3"):
            if raw_base.endswith(v_suffix):
                raw_base = raw_base[: -len(v_suffix)]
        self.base_url = raw_base.rstrip("/")

        self.auth_url = (auth_url or settings.FSM_AUTH_URL).rstrip("/")
        self.portal_url = (portal_url or settings.FSM_PORTAL_URL).rstrip("/")
        self._api_key = api_key or settings.FSM_API_KEY
        self._auth_token = auth_token or settings.FSM_AUTH_TOKEN
        self.timeout = timeout or settings.HTTP_TIMEOUT

        self._client: httpx.AsyncClient | None = None
        self._auth_lock = asyncio.Lock()
        self._token_obtained_at: float | None = None

    async def get_http_client(self) -> httpx.AsyncClient:
        """Returns the shared httpx.AsyncClient with connection limits and pooling."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout, connect=10.0),
                limits=httpx.Limits(
                    max_keepalive_connections=50,
                    max_connections=200,
                    keepalive_expiry=30.0,
                ),
                follow_redirects=True,
                headers={
                    "User-Agent": "FSM-Gateway/1.0",
                    "Referer": f"{self.portal_url}/",
                    "Accept": "application/json, text/plain, */*",
                },
            )
        return self._client

    async def close(self) -> None:
        """Closes the underlying HTTP client."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    def _pkce_pair(self) -> tuple[str, str]:
        """Generates a PKCE code_verifier and code_challenge."""
        verifier = (
            base64.urlsafe_b64encode(secrets.token_bytes(32))
            .rstrip(b"=")
            .decode("ascii")
        )
        challenge = (
            base64.urlsafe_b64encode(
                hashlib.sha256(verifier.encode("ascii")).digest()
            )
            .rstrip(b"=")
            .decode("ascii")
        )
        return verifier, challenge

    async def get_api_key(self) -> str:
        """Returns the currently cached or configured FSM API Key."""
        if self._api_key:
            return self._api_key
        cached_key = await cache.get(CACHE_KEY_API_KEY)
        if cached_key:
            return str(cached_key)
        return settings.FSM_API_KEY

    async def set_api_key(self, api_key: str) -> None:
        """Sets and caches the FSM API Key."""
        self._api_key = api_key
        await cache.set(CACHE_KEY_API_KEY, api_key, ttl=86400 * 30)

    async def get_auth_token(self) -> str | None:
        """Returns the current Bearer token from memory or cache."""
        if self._auth_token:
            return self._auth_token
        cached = await cache.get(CACHE_KEY_AUTH_TOKEN)
        if cached:
            self._auth_token = str(cached)
            return self._auth_token
        return None

    async def set_auth_token(self, token: str, ttl: int = 43200) -> None:
        """Stores the Bearer token in memory and TTL cache (default 12 hours)."""
        self._auth_token = token
        self._token_obtained_at = dt.datetime.now(dt.timezone.utc).timestamp()
        await cache.set(CACHE_KEY_AUTH_TOKEN, token, ttl=ttl)

    async def auto_login(self, email: str | None = None, password: str | None = None) -> str:
        """Performs full OAuth2 PKCE login flow and SSO token exchange against FSM."""
        user_email = (email or settings.FSM_EMAIL).strip().strip("'\"")
        user_password = (password or settings.FSM_PASSWORD).strip().strip("'\"")

        if not user_email or not user_password:
            raise FsmConfigError("FSM_EMAIL und FSM_PASSWORD sind nicht konfiguriert.")

        masked_email = (
            user_email
            if "@" not in user_email
            else f"{user_email.split('@')[0][:3]}***@{user_email.split('@')[1]}"
        )
        logger.info("FSM: Starte automatischen Login für %s ...", masked_email)

        async with self._auth_lock:
            # Double-checked locking: check if another task refreshed token within last 30s
            existing_token = await self.get_auth_token()
            if existing_token and self._token_obtained_at and (
                dt.datetime.now(dt.timezone.utc).timestamp() - self._token_obtained_at < 30
            ):
                return existing_token

            # Dedicated client with cookies for login handshake
            async with httpx.AsyncClient(
                follow_redirects=False,
                timeout=httpx.Timeout(self.timeout, connect=10.0),
                headers={
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                },
            ) as session:
                state = (
                    base64.urlsafe_b64encode(secrets.token_bytes(24))
                    .rstrip(b"=")
                    .decode("ascii")
                )
                verifier, challenge = self._pkce_pair()

                # Step 1: GET /connect/authorize
                auth_params = {
                    "response_type": "code",
                    "client_id": "fsm",
                    "redirect_uri": f"{self.portal_url}/login",
                    "scope": "openid profile offline_access fsm_api",
                    "state": state,
                    "nonce": state,
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                }
                auth_url = f"{self.auth_url}/connect/authorize?{urllib.parse.urlencode(auth_params)}"

                try:
                    resp = await session.get(auth_url)
                    if resp.is_redirect:
                        login_url = resp.headers.get("location", "")
                        resp = await session.get(urllib.parse.urljoin(auth_url, login_url))
                    else:
                        login_url = str(resp.url)
                    html = resp.text
                except Exception as exc:
                    raise FsmAuthError(f"OAuth Authorize fehlgeschlagen: {exc}") from exc

                xsrf_match = re.search(r"<meta\s+name=[\"']xsrf[\"']\s+content=[\"']([^\"']+)[\"']", html)
                if not xsrf_match:
                    xsrf_match = re.search(r"content=[\"']([^\"']+)[\"']\s+name=[\"']xsrf[\"']", html)
                if not xsrf_match:
                    xsrf_match = re.search(r'name=["\']__RequestVerificationToken["\']\s+value=["\']([^"\']+)["\']', html)

                if not xsrf_match:
                    raise FsmAuthError("XSRF-Token konnte auf der FSM-Loginseite nicht gefunden werden.")

                xsrf = xsrf_match.group(1)
                parsed = urllib.parse.urlparse(login_url)
                qs = urllib.parse.parse_qs(parsed.query)
                return_url = qs.get("ReturnUrl", ["/connect/authorize/callback"])[0]

                # Step 2: Form POST to /account/login
                post_url = f"{self.auth_url}/account/login?ReturnUrl={urllib.parse.quote(return_url)}"
                form_data = {
                    "password": user_password,
                    "username": user_email,
                    "__RequestVerificationToken": xsrf,
                    "returnUrl": return_url,
                    "rememberLogin": "false",
                    "button": "login",
                }

                try:
                    post_resp = await session.post(
                        post_url,
                        data=form_data,
                        headers={
                            "Referer": login_url,
                            "Accept": "application/json, text/html, */*",
                        },
                    )
                except Exception as exc:
                    raise FsmAuthError(f"FSM Login-POST fehlgeschlagen: {exc}") from exc

                if post_resp.status_code >= 400:
                    raise FsmAuthError(f"FSM Login zurückgewiesen ({post_resp.status_code}): {post_resp.text}")

                callback_path = None
                try:
                    callback_path = post_resp.json()
                except Exception:
                    callback_path = post_resp.headers.get("Location")

                if not callback_path:
                    raise FsmAuthError(f"FSM Login lieferte keinen Callback-Pfad: {post_resp.text[:200]}")

                callback_url = urllib.parse.urljoin(self.auth_url, callback_path)

                # Step 3: Call callback URL to obtain OAuth code
                cb_resp = await session.get(callback_url)
                redirect_target = cb_resp.headers.get("Location") or str(cb_resp.url)
                cb_qs = urllib.parse.parse_qs(urllib.parse.urlparse(redirect_target).query)
                code = cb_qs.get("code", [None])[0]

                if not code:
                    raise FsmAuthError(f"FSM OAuth-Callback lieferte keinen Authorization-Code ({redirect_target}).")

                # Step 4: Exchange code for OIDC token
                token_data = {
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": f"{self.portal_url}/login",
                    "code_verifier": verifier,
                    "client_id": "fsm",
                }
                token_resp = await session.post(
                    f"{self.auth_url}/connect/token",
                    data=token_data,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                if token_resp.status_code != 200:
                    raise FsmAuthError(f"Token-Austausch fehlgeschlagen ({token_resp.status_code}): {token_resp.text}")

                oidc_res = token_resp.json()
                oauth_access_token = oidc_res.get("access_token")
                if not oauth_access_token:
                    raise FsmAuthError("Kein access_token in connect/token Antwort.")

                # Step 5: Exchange OIDC Token via POST /v1/auth/sso
                hardware_id = str(uuid.uuid4())
                current_api_key = await self.get_api_key()

                sso_headers = {
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Referer": f"{self.portal_url}/",
                }
                if current_api_key:
                    sso_headers["x-fsm-apikey"] = current_api_key

                sso_payload = {"viewModel": {"access_token": oauth_access_token}}

                sso_resp = await session.post(
                    f"{self.base_url}/v1/auth/sso?hardwareId={hardware_id}",
                    json=sso_payload,
                    headers=sso_headers,
                )

                if sso_resp.status_code != 200:
                    raise FsmAuthError(f"SSO-Austausch fehlgeschlagen ({sso_resp.status_code}): {sso_resp.text}")

                sso_data = sso_resp.json()
                final_auth_token = sso_data.get("viewModel", {}).get("authToken")
                if not final_auth_token:
                    raise FsmAuthError(f"Kein authToken im SSO viewModel: {sso_data}")

                await self.set_auth_token(final_auth_token)
                logger.info("FSM: Login erfolgreich abgeschlossen. Neues Bearer Token hinterlegt.")
                return final_auth_token

    async def _ensure_token(self) -> str:
        """Ensures a valid auth token is available, logging in if needed."""
        token = await self.get_auth_token()
        if token:
            return token
        return await self.auto_login()

    async def _build_headers(self) -> tuple[dict[str, str], str]:
        """Builds default request headers with Bearer token and returns (headers, token)."""
        token = await self._ensure_token()
        api_key = await self.get_api_key()

        headers = {
            "Accept": "application/json, text/plain, */*",
            "Referer": f"{self.portal_url}/",
            "User-Agent": "FSM-Gateway/1.0",
        }
        if token:
            headers["Authorization"] = token if token.startswith("Bearer ") else f"Bearer {token}"
        if api_key:
            headers["x-fsm-apikey"] = api_key
        return headers, token

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_data: Any = None,
        data: Any = None,
        retry_on_401: bool = True,
        headers_override: dict[str, str] | None = None,
    ) -> Any:
        """Executes an authenticated HTTP request to FSM with transparent 401 re-login."""
        client = await self.get_http_client()

        normalized_path = path.lstrip("/")
        url = f"{self.base_url}/{normalized_path}"

        headers, token_used = await self._build_headers()
        if headers_override:
            headers.update(headers_override)

        clean_params = {k: v for k, v in (params or {}).items() if v is not None} if params else None

        try:
            resp = await client.request(
                method=method.upper(),
                url=url,
                params=clean_params,
                json=json_data,
                data=data,
                headers=headers,
            )

            # Check for 401 Unauthorized -> auto re-login
            if resp.status_code == 401 and retry_on_401:
                logger.warning("FSM API: 401 Unauthorized auf %s. Prüfe Re-Login...", url)
                current_token = await self.get_auth_token()
                # Only perform auto_login if token has not already been refreshed by another concurrent task
                if current_token == token_used:
                    await self.auto_login()
                return await self.request(
                    method=method,
                    path=path,
                    params=params,
                    json_data=json_data,
                    data=data,
                    retry_on_401=False,
                    headers_override=headers_override,
                )

            if resp.status_code >= 400:
                err_text = resp.text
                err_json = None
                try:
                    err_json = resp.json()
                except Exception:
                    pass

                msg = f"HTTP {resp.status_code}"
                if isinstance(err_json, dict):
                    responses = err_json.get("responses", [])
                    if responses and isinstance(responses, list) and "errorMessage" in responses[0]:
                        msg = responses[0]["errorMessage"]
                    elif "message" in err_json:
                        msg = err_json["message"]
                    elif "error" in err_json:
                        msg = str(err_json["error"])

                logger.error("FSM API Error (%s) auf %s: %s", resp.status_code, url, msg)
                raise FsmApiError(resp.status_code, msg, response_body=err_json or err_text)

            if not resp.text.strip():
                return None

            try:
                return resp.json()
            except json.JSONDecodeError:
                return resp.text

        except httpx.RequestError as exc:
            logger.error("FSM API Netzwerkfehler bei %s: %s", url, exc)
            raise FsmException(f"Verbindungsfehler zu FSM ({url}): {exc}") from exc

    # =========================================================================
    # High-Level Business Methods
    # =========================================================================

    async def get_auth_status(self) -> dict[str, Any]:
        """Checks authentication status and token validity."""
        token = await self.get_auth_token()
        has_credentials = bool(settings.FSM_EMAIL and settings.FSM_PASSWORD)

        status_info = {
            "authenticated": bool(token),
            "has_credentials": has_credentials,
            "fsm_base_url": self.base_url,
            "email": settings.FSM_EMAIL if settings.FSM_EMAIL else None,
            "has_api_key": bool(await self.get_api_key()),
            "token_preview": f"{token[:10]}...{token[-5:]}" if token else None,
        }

        if token:
            try:
                res = await self.request("GET", "v1/lehrer/fahrlehrer", params={"onlyActive": "true"}, retry_on_401=False)
                status_info["valid"] = isinstance(res, list)
            except Exception:
                status_info["valid"] = False
        else:
            status_info["valid"] = False

        return status_info

    async def get_fahrlehrer(self, only_active: bool = True) -> list[dict[str, Any]]:
        """Retrieves list of instructors, caching active instructors."""
        cache_key = f"{CACHE_KEY_FAHRLEHRER}:{only_active}"
        cached = await cache.get(cache_key)
        if cached is not None and isinstance(cached, list):
            return cached

        res = await self.request(
            "GET",
            "v1/lehrer/fahrlehrer",
            params={"onlyActive": "true" if only_active else "false"},
        )

        instructors: list[dict[str, Any]] = []
        if isinstance(res, list):
            for row in res:
                if not isinstance(row, dict):
                    continue
                vorname = (row.get("vorname") or "").strip()
                nachname = (row.get("nachname") or "").strip()
                voller_name = f"{vorname} {nachname}".strip()
                if not voller_name:
                    voller_name = row.get("displayName") or row.get("name") or "Unbekannt"

                row["voller_name"] = voller_name
                row["name"] = voller_name
                instructors.append(row)

        await cache.set(cache_key, instructors, ttl=settings.FAHRLEHRER_CACHE_TTL_SECONDS)
        return instructors

    async def get_kalender(
        self,
        fahrlehrer_id: str,
        start_date: dt.date | dt.datetime | str,
        end_date: dt.date | dt.datetime | str,
        only_buchbar: bool = False,
        skip_deleted: bool = True,
    ) -> list[dict[str, Any]]:
        """Retrieves raw calendar events for an instructor from FSM API."""
        start_iso = _normalize_iso_datetime(start_date, is_end=False)
        end_iso = _normalize_iso_datetime(end_date, is_end=True)

        params = {
            "onlyBuchbar": "true" if only_buchbar else "false",
            "start": start_iso,
            "end": end_iso,
            "displayBegleitfahrzeug": "false",
            "skipDeleted": "true" if skip_deleted else "false",
        }

        path = f"v1/termine/lehrer/{fahrlehrer_id}"
        res = await self.request("GET", path, params=params)
        return res if isinstance(res, list) else []

    async def create_termin(
        self,
        fahrlehrer_id: str,
        von: dt.datetime,
        bis: dt.datetime,
        titel: str,
        leistungsart_id: str | None = None,
        terminart: str = "PX",
        schueler_id: str | None = None,
        fahrzeug_id: str | None = None,
        gebucht: bool = False,
    ) -> list[str]:
        """Creates appointment/blocker. Automatically splits blocks exceeding 600 min."""
        await cache.delete_prefix(f"fsm:kalender:{fahrlehrer_id}")

        leistungsart = leistungsart_id or settings.FSM_DEFAULT_LEISTUNGSART_ID
        duration_minutes = (bis - von).total_seconds() / 60.0

        created_ids: list[str] = []

        if duration_minutes > 600:
            current_start = von
            block_idx = 1
            total_blocks = int(duration_minutes // 600) + (1 if duration_minutes % 600 != 0 else 0)

            while current_start < bis:
                current_end = min(current_start + dt.timedelta(minutes=600), bis)
                block_title = f"{titel} (Teil {block_idx}/{total_blocks})"

                payload = {
                    "viewModel": {
                        "von": _normalize_iso_datetime(current_start),
                        "bis": _normalize_iso_datetime(current_end),
                        "fidFahrlehrer": [fahrlehrer_id],
                        "fidTerminart": terminart,
                        "gebucht": gebucht,
                        "fidFahrzeug": fahrzeug_id,
                        "fidLeistungsart": leistungsart,
                        "texte": block_title,
                        "fidSchueler": schueler_id,
                    }
                }
                res = await self.request("POST", "v1/termine", json_data=payload)
                tid = res.get("viewModel", {}).get("id") if isinstance(res, dict) else None
                if tid:
                    created_ids.append(str(tid))

                current_start = current_end
                block_idx += 1
        else:
            payload = {
                "viewModel": {
                    "von": _normalize_iso_datetime(von),
                    "bis": _normalize_iso_datetime(bis),
                    "fidFahrlehrer": [fahrlehrer_id],
                    "fidTerminart": terminart,
                    "gebucht": gebucht,
                    "fidFahrzeug": fahrzeug_id,
                    "fidLeistungsart": leistungsart,
                    "texte": titel,
                    "fidSchueler": schueler_id,
                }
            }
            res = await self.request("POST", "v1/termine", json_data=payload)
            tid = res.get("viewModel", {}).get("id") if isinstance(res, dict) else None
            if tid:
                created_ids.append(str(tid))
            elif isinstance(res, dict) and "id" in res:
                created_ids.append(str(res["id"]))

        return created_ids

    async def update_termin(
        self,
        termin_id: str,
        fahrlehrer_id: str,
        von: dt.datetime,
        bis: dt.datetime,
        titel: str,
        leistungsart_id: str | None = None,
        terminart: str = "PX",
        schueler_id: str | None = None,
        fahrzeug_id: str | None = None,
        gebucht: bool = False,
    ) -> bool:
        """Updates an existing calendar appointment in FSM."""
        await cache.delete_prefix(f"fsm:kalender:{fahrlehrer_id}")
        leistungsart = leistungsart_id or settings.FSM_DEFAULT_LEISTUNGSART_ID
        payload = {
            "viewModel": {
                "id": termin_id,
                "von": _normalize_iso_datetime(von),
                "bis": _normalize_iso_datetime(bis),
                "fidFahrlehrer": [fahrlehrer_id],
                "fidTerminart": terminart,
                "gebucht": gebucht,
                "fidFahrzeug": fahrzeug_id,
                "fidLeistungsart": leistungsart,
                "texte": titel,
                "fidSchueler": schueler_id,
            }
        }
        res = await self.request("PUT", "v1/termine", json_data=payload)
        return res is not None

    async def delete_termin(self, termin_id: str) -> bool:
        """Deletes an appointment from FSM by UUID."""
        await cache.delete_prefix("fsm:kalender:")
        payload = {"viewModel": {"id": termin_id}}
        try:
            await self.request("DELETE", "v1/termine", json_data=payload)
            return True
        except FsmApiError as exc:
            if exc.status_code in (404, 405):
                await self.request("DELETE", f"v1/termine/termin/{termin_id}")
                return True
            raise

    async def search_schueler(
        self,
        query: str | None = None,
        vorname: str | None = None,
        nachname: str | None = None,
        kartei_nr: str | None = None,
        only_active: bool = True,
        count: int = 5000,
        index: int = 0,
    ) -> dict[str, Any]:
        """Searches students with smart multi-field merging, pagination and umlaut handling."""
        base_params = {
            "einfacheSuche": "true",
            "onlyActive": "true" if only_active else "false",
            "filter.activeFlag": "true" if only_active else "false",
            "filter.listFlag": "true",
            "filter.count": str(count),
            "filter.index": str(index),
        }

        # Case 1: Specific field search
        if vorname or nachname or kartei_nr:
            params = dict(base_params)
            if vorname:
                params["vorname"] = vorname.strip()
            if nachname:
                params["name"] = nachname.strip()
            if kartei_nr:
                params["karteiNr"] = kartei_nr.strip()
            res = await self.request("GET", "v2/schueler/suche", params=params)
            return res if isinstance(res, dict) else {"rows": []}

        # Case 2: General query search
        if query and query.strip():
            clean_q = query.strip()
            words = clean_q.split()
            accumulated_rows: dict[str, dict[str, Any]] = {}

            async def _do_query(search_params: dict[str, str]):
                p = dict(base_params)
                p.update(search_params)
                try:
                    r = await self.request("GET", "v2/schueler/suche", params=p)
                    for item in r.get("rows", []) if isinstance(r, dict) else []:
                        sid = item.get("data", {}).get("id") or item.get("id")
                        if sid:
                            accumulated_rows[str(sid)] = item
                except Exception as exc:
                    logger.warning("Fehler bei Teilsuche %s: %s", search_params, exc)

            if len(words) >= 2:
                w1, w2 = words[0], " ".join(words[1:])
                # Try vorname=w1, name=w2 AND vorname=w2, name=w1
                await _do_query({"vorname": w1, "name": w2})
                await _do_query({"vorname": w2, "name": w1})
            else:
                # 1 word: query vorname, name, and karteiNr in parallel
                tasks = [_do_query({"name": clean_q}), _do_query({"vorname": clean_q})]
                if any(c.isdigit() for c in clean_q):
                    tasks.append(_do_query({"karteiNr": clean_q}))
                await asyncio.gather(*tasks, return_exceptions=True)

            return {"rows": list(accumulated_rows.values())}

        # Case 3: No search query -> return standard paginated list
        res = await self.request("GET", "v2/schueler/suche", params=base_params)
        return res if isinstance(res, dict) else {"rows": []}

    async def get_schueler_details(self, student_uuid: str, fresh: bool = False) -> dict[str, Any]:
        """Fetches full student master data, address, classes and status (cached 300s)."""
        cache_key = f"fsm:schueler:{student_uuid}"
        if not fresh:
            cached = await cache.get(cache_key)
            if cached is not None and isinstance(cached, dict):
                return cached

        res = await self.request("GET", f"v1/schueler/kartei/{student_uuid}")
        details = res if isinstance(res, dict) else {}
        if details:
            await cache.set(cache_key, details, ttl=settings.CACHE_TTL_SECONDS)
        return details

    async def get_schueler_fahrstunden(
        self,
        student_uuid: str,
        skip_deleted: bool = True,
        page: int = 1,
        page_size: int = 100,
    ) -> dict[str, Any]:
        """Retrieves driving lessons and paid status for a student."""
        params = {
            "skipDeleted": "true" if skip_deleted else "false",
            "pagination.page": str(page),
            "pagination.pageSize": str(page_size),
            "pagination.count": str(page_size),
        }
        res = await self.request("GET", f"v2/fahrstunden/kunde/{student_uuid}", params=params)
        return res if isinstance(res, dict) else {"rows": []}

    async def get_schueler_leistungen(
        self,
        student_uuid: str,
        skip_deleted: bool = True,
        page: int = 1,
        page_size: int = 500,
    ) -> dict[str, Any]:
        """Retrieves financial account services, payments and fees for a student."""
        params = {
            "skipDeleted": "true" if skip_deleted else "false",
            "pagination.page": str(page),
            "pagination.pageSize": str(page_size),
            "pagination.count": str(page_size),
        }
        res = await self.request("GET", f"v2/leistungen/{student_uuid}", params=params)
        return res if isinstance(res, dict) else {"rows": []}

    async def create_zahlung(
        self,
        student_uuid: str,
        betrag: float,
        datum: dt.date | dt.datetime | str,
        zahlungsart: str = "Karte",
        text: str = "SumUp Kartenzahlung",
        belegnummer: str | None = None,
    ) -> dict[str, Any]:
        """Records a payment (Gutschrift / Zahlung) for a student in FSM Cloud via v1/zahlungen."""
        await cache.delete_prefix(f"fsm:schueler:{student_uuid}")
        await cache.delete_prefix(f"fsm:leistungen:{student_uuid}")
        await cache.delete_prefix(f"fsm:fahrstunden:{student_uuid}")

        datum_iso = _normalize_iso_datetime(datum)

        # 1. Vorlage für Schüler abrufen
        kunde_name = ""
        fid_steuer = 5
        steuersatz = 0.19

        try:
            vorlage = await self.request("GET", f"v1/zahlungen/vorlage?fidkunde={student_uuid}")
            if isinstance(vorlage, dict):
                kunde_name = vorlage.get("kunde") or kunde_name
                fid_steuer = vorlage.get("fidsteuer", fid_steuer)
                steuersatz = vorlage.get("steuersatz", steuersatz)
        except Exception as exc:
            logger.warning("Konnte Zahlungs-Vorlage für %s nicht abrufen: %s", student_uuid, exc)

        # 2. Passendes Kassenbuch ermitteln (Kartenzahlung für Karte / SumUp)
        fid_kassenbuch = "b6f0fff6-21f7-4a5f-8109-ae258b9e9912"
        kassenbuch_name = "Kartenzahlung"

        try:
            buecher = await self.request("GET", "v1/kassenbuecher")
            if isinstance(buecher, list):
                art_lower = (zahlungsart or "Karte").lower()
                for kb in buecher:
                    kb_bez = str(kb.get("bezeichnung") or "").lower()
                    if any(k in art_lower for k in ["karte", "sumup", "card"]) and "karte" in kb_bez:
                        fid_kassenbuch = str(kb.get("id"))
                        kassenbuch_name = str(kb.get("bezeichnung"))
                        break
                    elif any(k in art_lower for k in ["bank", "überweisung"]) and "bank" in kb_bez:
                        fid_kassenbuch = str(kb.get("id"))
                        kassenbuch_name = str(kb.get("bezeichnung"))
                        break
        except Exception as exc:
            logger.warning("Konnte Kassenbücher nicht abrufen, nutze Kartenzahlung-Standard: %s", exc)

        note_text = text or "SumUp Kartenzahlung"
        if belegnummer and belegnummer not in note_text:
            note_text = f"{note_text} ({belegnummer})"

        # 3. Anmeldedatum des Schülers prüfen (FSM erlaubt keine Buchungen vor dem Anmeldedatum)
        try:
            schueler = await self.get_schueler_details(student_uuid)
            if isinstance(schueler, dict) and schueler.get("anmeldedatum"):
                anmelde_str = str(schueler["anmeldedatum"])[:10]
                target_date_str = str(datum_iso)[:10]
                if target_date_str < anmelde_str:
                    logger.info(
                        "Zahlungsdatum %s liegt vor Anmeldedatum %s für Schüler %s -> setze Anmeldedatum auf Zahlungsdatum",
                        target_date_str,
                        anmelde_str,
                        student_uuid,
                    )
                    updated = await self.update_schueler_anmeldedatum(student_uuid, target_date_str)
                    if not updated:
                        logger.warning("Konnte Anmeldedatum nicht vorverlegen, passe Buchungsdatum an.")
                        datum_iso = f"{anmelde_str}T12:00:00+02:00"
                        if f"Zahlung vom {target_date_str}" not in note_text:
                            note_text = f"{note_text} (Zahlung vom {target_date_str})"
        except Exception as exc:
            logger.warning("Konnte Anmeldedatum für %s nicht abgleichen: %s", student_uuid, exc)

        payload = {
            "viewModel": {
                "fidKunde": student_uuid,
                "kunde": kunde_name,
                "fidKassenbuch": fid_kassenbuch,
                "kassenbuch": kassenbuch_name,
                "fidsteuer": fid_steuer,
                "steuersatz": steuersatz,
                "datum": datum_iso,
                "betrag": float(betrag),
                "bemerkung": note_text,
                "beleg": belegnummer or "",
            }
        }
        try:
            res = await self.request("POST", "v1/zahlungen", json_data=payload)
        except FsmApiError as exc:
            # Fallback bei Datumsvalidierungsfehlern seitens FSM
            if "Anmeldedatum" in str(exc):
                logger.warning("FSM verweigerte Datum wegen Anmeldedatum, versuche Anmeldedatum anzupassen: %s", exc)
                target_date_str = str(datum_iso)[:10]
                await self.update_schueler_anmeldedatum(student_uuid, target_date_str)
                res = await self.request("POST", "v1/zahlungen", json_data=payload)
            else:
                raise
        return res if isinstance(res, dict) else {"success": True, "result": res}

    async def update_schueler_anmeldedatum(
        self,
        student_uuid: str,
        new_anmeldedatum: dt.date | dt.datetime | str,
    ) -> bool:
        """Updates a student's registration date (Anmeldedatum) in FSM Cloud via PUT v1/schueler."""
        try:
            kartei = await self.request("GET", f"v1/schueler/kartei/{student_uuid}")
            if not isinstance(kartei, dict):
                return False

            preise = await self.request("GET", f"v1/preislisten/schueler/{student_uuid}")
            if isinstance(preise, list):
                kartei["kundenpreise"] = preise

            date_iso = _normalize_iso_datetime(new_anmeldedatum)
            kartei["anmeldedatum"] = date_iso

            await self.request("PUT", "v1/schueler", json_data={"viewModel": kartei})
            await cache.delete_prefix(f"fsm:schueler:{student_uuid}")
            await cache.delete_prefix(f"schueler:details:{student_uuid}")
            logger.info("Anmeldedatum für Schüler %s erfolgreich auf %s aktualisiert.", student_uuid, date_iso)
            return True
        except Exception as exc:
            logger.error("Fehler beim Aktualisieren des Anmeldedatums für %s: %s", student_uuid, exc)
            return False


# Global FSM client singleton
fsm_client = FSMClient()
