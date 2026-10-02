"""Google "Continue with Google" sign-in for the admin dashboard (allow-listed accounts only)."""
import logging
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import jwt
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.security import create_access_token, hash_password
from app.models.user import ADMIN_ROLE, User
from app.repositories.user_repository import UserRepository
from app.services.google_calendar_client import GoogleCalendarClient, GoogleClientError

logger = logging.getLogger(__name__)

STATE_PURPOSE = "google_login"
STATE_TTL = timedelta(minutes=10)


def mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"


class GoogleLoginService:
    def __init__(
        self, session: AsyncSession, users: UserRepository, client: GoogleCalendarClient, settings: Settings
    ) -> None:
        self.session = session
        self.users = users
        self.client = client
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return self.settings.google_login_enabled

    def authorization_url(self) -> str | None:
        if not self.enabled:
            return None
        return self.client.build_login_url(self._sign_state())

    def _sign_state(self) -> str:
        now = datetime.now(timezone.utc)
        payload = {
            "purpose": STATE_PURPOSE,
            "nonce": secrets.token_urlsafe(16),
            "iat": now,
            "exp": now + STATE_TTL,
        }
        return jwt.encode(payload, self.settings.jwt_secret, algorithm=self.settings.jwt_algorithm)

    def _state_valid(self, state: str | None) -> bool:
        if not state:
            return False
        try:
            payload = jwt.decode(
                state,
                self.settings.jwt_secret,
                algorithms=[self.settings.jwt_algorithm],
                options={"require": ["exp", "nonce"]},
            )
        except jwt.PyJWTError:
            return False
        return payload.get("purpose") == STATE_PURPOSE

    def error_redirect(self, code: str) -> str:
        return f"{self.settings.frontend_url}/login?{urlencode({'google_error': code})}"

    def success_redirect(self, token: str) -> str:
        return f"{self.settings.frontend_url}/auth/google/complete#token={token}"

    async def handle_callback(self, code: str | None, state: str | None, error: str | None) -> tuple[str | None, str | None]:
        """Returns (jwt, None) on success or (None, error_code) on failure."""
        reason = await self._authenticate(code, state, error)
        if isinstance(reason, str) and reason.startswith("err:"):
            code_name = reason[4:]
            logger.warning("Google login failed: %s", code_name)
            return None, code_name
        assert isinstance(reason, User)
        logger.info("Google login succeeded for %s", mask_email(reason.email))
        return create_access_token(str(reason.id), reason.role), None

    async def _authenticate(self, code: str | None, state: str | None, error: str | None) -> "User | str":
        if not self.enabled:
            return "err:not_configured"
        if not self._state_valid(state):
            return "err:invalid_state"
        if error:
            return "err:denied" if error == "access_denied" else "err:exchange_failed"
        if not code:
            return "err:exchange_failed"
        try:
            tokens = await self.client.exchange_login_code(code)
            profile = await self.client.get_user_profile(tokens.access_token)
        except GoogleClientError as exc:
            logger.warning("Google login exchange error: %s", type(exc).__name__)
            return "err:exchange_failed"
        email = profile.get("email")
        if not isinstance(email, str) or not email.strip():
            return "err:exchange_failed"
        email = email.strip().lower()
        if profile.get("email_verified") is not True:
            return "err:not_verified"
        if email not in self.settings.google_login_allowed_set:
            return "err:not_allowed"
        name = profile.get("name")
        name = name.strip() if isinstance(name, str) and name.strip() else None
        user = await self.users.get_by_email(email)
        if user is None:
            user = await self.users.add(
                User(
                    email=email,
                    password_hash=hash_password(secrets.token_urlsafe(48)),
                    full_name=name,
                    role=ADMIN_ROLE,
                )
            )
        elif not user.full_name and name:
            user.full_name = name
        await self.session.commit()
        return user
