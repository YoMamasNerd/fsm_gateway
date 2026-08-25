"""Application configuration via pydantic-settings."""

from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration for FSM-Gateway."""

    # FSM Credentials & Endpoints
    FSM_EMAIL: str = Field(default="", description="FSM Login E-Mail")
    FSM_PASSWORD: str = Field(default="", description="FSM Login Password")
    FSM_BASE_URL: str = Field(
        default="https://api.fahrschulmanager.de",
        description="FSM Base API URL (without trailing slash)",
    )
    FSM_AUTH_URL: str = Field(
        default="https://login.fahren-lernen.de",
        description="FSM SSO / Identity Provider URL",
    )
    FSM_PORTAL_URL: str = Field(
        default="https://portal.fahrschulmanager.de",
        description="FSM Web Portal URL",
    )
    FSM_API_KEY: str = Field(
        default="04TapXakdwXWUDVJyNEE8.W3t83Y3FhNryQABM0cMUq10JBH6Wv7X2k1iassfBsXOJpgyUHlYm2nUfCk6vdgVl10NadmfI8KnSmqefUlOJjv.8gCXHwujMBoT0TY2gGQ",
        description="FSM Portal X-FSM-ApiKey header value",
    )
    FSM_AUTH_TOKEN: str = Field(
        default="",
        description="Optional static or pre-seeded Auth Bearer Token",
    )
    FSM_DEFAULT_LEISTUNGSART_ID: str = Field(
        default="4330ec51-91b9-45f1-a3fb-88179db000ce",
        description="Standard Leistungsart-UUID for calendar blocks",
    )

    # Gateway Server Settings
    GATEWAY_HOST: str = Field(default="0.0.0.0", description="Host to bind server")
    GATEWAY_PORT: int = Field(default=8090, description="Port to bind server")
    GATEWAY_API_KEY: str = Field(
        default="",
        description="Optional secret key to require for inbound requests to this Gateway",
    )
    VALKEY_URL: str = Field(
        default="",
        description="Optional Valkey / Redis connection URL (e.g. redis://fsm-valkey:6379/0). If empty or unreachable, falls back to memory cache.",
    )
    CACHE_TTL_SECONDS: int = Field(
        default=300,
        description="Default TTL in seconds for general cached entities",
    )
    CALENDAR_CACHE_TTL_SECONDS: int = Field(
        default=43200,
        description="Optimistic TTL in seconds for instructor calendar responses (default: 12 hours / 43200s)",
    )
    CALENDAR_SWR_MAX_AGE_SECONDS: int = Field(
        default=86400,
        description="Maximum stale window in seconds for Stale-While-Revalidate background refresh (default: 24 hours)",
    )
    FAHRLEHRER_CACHE_TTL_SECONDS: int = Field(
        default=43200,
        description="TTL in seconds for instructor list cache (default: 12 hours / 43200s)",
    )
    SCHUELER_CACHE_TTL_SECONDS: int = Field(
        default=21600,
        description="TTL in seconds for student details/profile cache (default: 6 hours / 21600s)",
    )
    FAHRSTUNDEN_CACHE_TTL_SECONDS: int = Field(
        default=300,
        description="TTL in seconds for student driving lessons history cache (default: 5 minutes / 300s)",
    )
    LEISTUNGEN_CACHE_TTL_SECONDS: int = Field(
        default=60,
        description="TTL in seconds for student services/balance cache (default: 1 minute / 60s)",
    )
    AUSBILDUNG_CACHE_TTL_SECONDS: int = Field(
        default=3600,
        description="TTL in seconds for student training status/classes cache (default: 1 hour / 3600s)",
    )
    THEORIE_CACHE_TTL_SECONDS: int = Field(
        default=3600,
        description="TTL in seconds for theory lessons/chapters cache (default: 1 hour / 3600s)",
    )
    FUHRPARK_CACHE_TTL_SECONDS: int = Field(
        default=43200,
        description="TTL in seconds for fleet/vehicles cache (default: 12 hours / 43200s)",
    )
    STAMMDATEN_CACHE_TTL_SECONDS: int = Field(
        default=86400,
        description="TTL in seconds for branches/classes/services master data (default: 24 hours / 86400s)",
    )
    STATISTIKEN_CACHE_TTL_SECONDS: int = Field(
        default=1800,
        description="TTL in seconds for exam performance statistics cache (default: 30 minutes / 1800s)",
    )
    KASSENBUCH_CACHE_TTL_SECONDS: int = Field(
        default=300,
        description="TTL in seconds for cashbook data cache (default: 5 minutes / 300s)",
    )
    HTTP_TIMEOUT: float = Field(
        default=20.0,
        description="Timeout for external HTTP requests in seconds",
    )
    LOG_LEVEL: str = Field(default="INFO", description="Log level")
    ENVIRONMENT: str = Field(default="production", description="Environment name")
    TIMEZONE: str = Field(
        default="Europe/Berlin",
        description="Application timezone for formatting dates and metrics",
    )

    # Metrics & Dashboard Settings
    DASHBOARD_PASSWORD: str = Field(
        default="",
        description="Optional password to protect /dashboard (if empty, accessible without password)",
    )
    METRICS_DB_PATH: str = Field(
        default="data/metrics.db",
        description="Path to SQLite metrics database file",
    )
    METRICS_RETENTION_DAYS: int = Field(
        default=60,
        description="Number of days to retain detailed request metrics in SQLite",
    )

    # VoidAuth SSO for Dashboard
    VOIDAUTH_CLIENT_ID: str = Field(default="", description="VoidAuth OIDC Client ID")
    VOIDAUTH_CLIENT_SECRET: str = Field(default="", description="VoidAuth OIDC Client Secret")
    VOIDAUTH_ISSUER_URL: str = Field(
        default="",
        description="VoidAuth OIDC Issuer URL (e.g. https://auth.arbeits-zimmer.de/oidc)",
    )
    VOIDAUTH_REDIRECT_URI: str = Field(
        default="",
        description="Custom Redirect URI override (optional)",
    )

    @property
    def VOIDAUTH_ENABLED(self) -> bool:
        return bool(self.VOIDAUTH_CLIENT_ID and self.VOIDAUTH_CLIENT_SECRET and self.VOIDAUTH_ISSUER_URL)

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
