"""Application settings loaded from environment / .env."""
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cryptography.fernet import Fernet

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

    # IANA timezone of the studio; booking dates/times are studio-local wall-clock values.
    app_timezone: str = "America/Toronto"
    # Where the browser is sent after the Google OAuth callback (must be an allowed origin).
    frontend_url: str = "http://localhost:5173"

    # Google Calendar integration; everything stays optional (feature reports configured=false).
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = ""
    google_calendar_scopes: str = "openid email https://www.googleapis.com/auth/calendar.events"
    google_calendar_default_id: str = "primary"
    # "Continue with Google" dashboard sign-in (reuses the client above). Disabled unless the
    # redirect URI and a non-empty allow-list are both set. Allow-list: comma-separated emails.
    google_login_redirect_uri: str = ""
    google_login_allowed_emails: str = ""
    # Fernet key (urlsafe base64, 32 bytes) used to encrypt Google tokens at rest.
    token_encryption_key: str = ""

    # Business identity and invoice defaults. Shown on invoices and used to pre-fill new ones.
    # The studio timezone is APP_TIMEZONE (above); it is not duplicated here.
    business_name: str = "Chanthans"
    # Public URL of the logo (e.g. in R2). If unset or unreachable, the bundled logo is used.
    business_logo_url: str = ""
    business_address: str = ""
    business_phone: str = ""
    business_email: str = ""
    business_website: str = ""
    business_currency: str = "CAD"
    invoice_prefix: str = "INV"
    invoice_due_days: int = 14
    # Tax is never assumed: leave blank/0 and set it per invoice, or give a studio-wide default.
    invoice_default_tax_name: str = ""
    invoice_default_tax_rate: float = 0
    default_invoice_notes: str = "Thank you for choosing us to capture your special moments."
    default_invoice_terms: str = ""
    # "Letter" (Canada/US) or "A4"
    invoice_page_size: str = "Letter"

    @model_validator(mode="after")
    def _normalize_and_validate(self) -> "Settings":
        self.database_url = normalize_database_url(self.database_url)
        self.r2_public_url = self.r2_public_url.rstrip("/")
        self.local_media_url = self.local_media_url.rstrip("/")
        self.frontend_url = self.frontend_url.rstrip("/")
        self.storage_backend = self.storage_backend.lower()
        try:
            ZoneInfo(self.app_timezone)
        except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
            raise ValueError("APP_TIMEZONE must be a valid IANA timezone, e.g. America/Toronto") from exc
        if self.token_encryption_key:
            try:
                Fernet(self.token_encryption_key.encode())
            except ValueError as exc:
                raise ValueError("TOKEN_ENCRYPTION_KEY must be a valid Fernet key") from exc
        # Env vars are single-line; allow a literal "\n" for line breaks in the address.
        self.business_address = self.business_address.replace("\\n", "\n").strip()
        self.business_currency = self.business_currency.strip().upper() or "CAD"
        if len(self.business_currency) != 3 or not self.business_currency.isalpha():
            raise ValueError("BUSINESS_CURRENCY must be a 3-letter ISO 4217 code, e.g. CAD")
        self.invoice_prefix = self.invoice_prefix.strip().upper() or "INV"
        if self.invoice_page_size not in ("Letter", "A4"):
            raise ValueError("INVOICE_PAGE_SIZE must be 'Letter' or 'A4'")
        if not 0 <= self.invoice_default_tax_rate <= 100:
            raise ValueError("INVOICE_DEFAULT_TAX_RATE must be between 0 and 100")
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
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.app_timezone)

    @property
    def google_configured(self) -> bool:
        return all(
            (
                self.google_client_id,
                self.google_client_secret,
                self.google_redirect_uri,
                self.token_encryption_key,
            )
        )

    @property
    def google_login_allowed_set(self) -> frozenset[str]:
        return frozenset(e.strip().lower() for e in self.google_login_allowed_emails.split(",") if e.strip())

    @property
    def google_login_enabled(self) -> bool:
        return bool(
            self.google_client_id
            and self.google_client_secret
            and self.google_login_redirect_uri
            and self.google_login_allowed_set
        )

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
