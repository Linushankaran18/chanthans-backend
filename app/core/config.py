"""Application settings loaded from environment / .env."""
from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_WEAK_SECRETS = {"", "secret", "changeme", "change-me", "dev-insecure-secret-change-me"}
_MIN_SECRET_LENGTH = 32


def normalize_database_url(url: str) -> str:
    """Map plain Postgres URLs to the asyncpg driver; leave others untouched."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+asyncpg://" + url[len(prefix):]
    return url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    environment: str = "development"
    database_url: str

    jwt_secret: str = "dev-insecure-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    # "r2" (Cloudflare R2, for production) or "local" (files on disk, development only).
    storage_backend: str = "r2"
    local_media_dir: str = "media"
    local_media_url: str = "http://localhost:8000/media"

    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket_name: str = ""
    r2_endpoint_url: str = ""
    r2_public_url: str = ""

    max_upload_size_mb: int = 10
    # Kept as a plain string: comma-separated (pydantic-settings would expect JSON for list types).
    allowed_origins: str = "http://localhost:5173"

    @model_validator(mode="after")
    def _normalize_and_validate(self) -> "Settings":
        self.database_url = normalize_database_url(self.database_url)
        self.r2_public_url = self.r2_public_url.rstrip("/")
        self.local_media_url = self.local_media_url.rstrip("/")
        self.storage_backend = self.storage_backend.lower()
        if self.storage_backend not in ("r2", "local"):
            raise ValueError("STORAGE_BACKEND must be 'r2' or 'local'")
        if self.is_production:
            if self.storage_backend == "local":
                raise ValueError("STORAGE_BACKEND=local is for development only")
            if self.jwt_secret in _WEAK_SECRETS or len(self.jwt_secret) < _MIN_SECRET_LENGTH:
                raise ValueError(
                    f"JWT_SECRET must be a strong secret (>= {_MIN_SECRET_LENGTH} chars) in production"
                )
            if "*" in self.cors_origins:
                raise ValueError("Wildcard ALLOWED_ORIGINS is not allowed in production")
        return self

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def max_upload_size_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024

    @property
    def r2_endpoint(self) -> str:
        if self.r2_endpoint_url:
            return self.r2_endpoint_url
        return f"https://{self.r2_account_id}.r2.cloudflarestorage.com"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
