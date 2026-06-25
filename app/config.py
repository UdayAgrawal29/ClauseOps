"""Environment-driven application settings.

All configuration is sourced from environment variables (optionally via a
``.env`` file) so the offline-first MVP runs with no external accounts. Nothing
here requires a managed/hosted service; the defaults target a local PostgreSQL,
local Redis, and a local-filesystem storage root.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Sentinel/weak secrets that must never be used in production. The app boots with
# these in development for zero-config local runs, but fails fast in production.
_WEAK_SECRETS = {
    "",
    "clauseops",
    "refresh",
    "change-me",
    "changeme",
    "dev-only-change-me",
    "dev-only-change-me-refresh",
    "secret",
}
_MIN_SECRET_LEN = 16


class Settings(BaseSettings):
    """Backend settings loaded from the environment.

    The async SQLAlchemy engine is driven by ``database_url`` which must use an
    async driver (e.g. ``postgresql+asyncpg://...``).
    """

    model_config = SettingsConfigDict(
        env_prefix="CLAUSEOPS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Deployment environment ---
    environment: str = Field(
        default="development",
        description="Deployment environment: 'development' or 'production'. "
        "In production, weak/sentinel JWT secrets are rejected at startup.",
    )

    # --- Database (async SQLAlchemy 2.0) ---
    database_url: str = Field(
        default="postgresql+asyncpg://clauseops:clauseops@localhost:5432/clauseops",
        description="Async SQLAlchemy database URL.",
    )
    db_echo: bool = Field(
        default=False,
        description="Echo emitted SQL statements (debugging only).",
    )

    # --- Redis (broker / result backend / pub-sub) ---
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis URL used as Celery broker, result backend, and pub/sub.",
    )

    # --- Object storage (local filesystem for the MVP) ---
    storage_backend: str = Field(
        default="local",
        description="Selected storage backend implementation ('local' for the MVP).",
    )
    storage_root: str = Field(
        default="./var/storage",
        description="Filesystem root for stored files; kept outside the web root.",
    )

    # --- Auth / JWT (own JWT, no OAuth) ---
    jwt_secret: str = Field(
        default="ClauseOps",
        description="Secret used to sign access tokens.",
    )
    jwt_refresh_secret: str = Field(
        default="Refresh",
        description="Secret used to sign refresh tokens.",
    )
    jwt_algorithm: str = Field(default="HS256")
    access_token_ttl_seconds: int = Field(default=900)  # 15 minutes
    refresh_token_ttl_seconds: int = Field(default=60 * 60 * 24 * 14)  # 14 days

    # --- Upload hardening ---
    max_upload_bytes: int = Field(
        default=20 * 1024 * 1024,
        description="Maximum accepted upload size in bytes (20 MB cap).",
    )

    # --- Web / CORS / cookies ---
    cors_origins: str = Field(
        default="http://localhost:5173",
        description="Comma-separated list of allowed CORS origins for the SPA.",
    )
    cookie_secure: bool = Field(
        default=False,
        description="Set the refresh cookie 'Secure' flag (enable behind TLS).",
    )
    refresh_store_backend: str = Field(
        default="memory",
        description="Refresh-token revocation store: 'memory' (single-process "
        "MVP default) or 'redis' (shared across workers, survives restarts).",
    )

    @property
    def cors_origin_list(self) -> list[str]:
        """The configured CORS origins as a clean list."""

        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() in {"production", "prod"}

    @model_validator(mode="after")
    def _reject_weak_secrets_in_production(self) -> "Settings":
        """Fail fast if production is configured with weak/sentinel JWT secrets."""

        if not self.is_production:
            return self
        for name, value in (
            ("jwt_secret", self.jwt_secret),
            ("jwt_refresh_secret", self.jwt_refresh_secret),
        ):
            v = (value or "").strip()
            if v.lower() in _WEAK_SECRETS or len(v) < _MIN_SECRET_LEN:
                raise ValueError(
                    f"{name} is too weak for production: set CLAUSEOPS_{name.upper()} "
                    f"to a strong random value (>= {_MIN_SECRET_LEN} chars)."
                )
        if self.jwt_secret == self.jwt_refresh_secret:
            raise ValueError(
                "jwt_secret and jwt_refresh_secret must differ in production."
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached :class:`Settings` instance."""

    return Settings()
