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
    CACHE_TTL_SECONDS: int = Field(
        default=300,
        description="Default TTL in seconds for cached entities (e.g. instructors)",
    )
    HTTP_TIMEOUT: float = Field(
        default=20.0,
        description="Timeout for external HTTP requests in seconds",
    )
    LOG_LEVEL: str = Field(default="INFO", description="Log level")
    ENVIRONMENT: str = Field(default="production", description="Environment name")

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
