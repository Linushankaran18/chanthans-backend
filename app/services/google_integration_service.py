"""Google OAuth connect/callback/status/disconnect for the studio calendar."""
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from uuid import UUID

import jwt
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.crypto import TokenCipher
from app.core.exceptions import ServiceUnavailableError
from app.models.google_calendar_integration import GoogleCalendarIntegration
from app.models.user import User
from app.repositories.booking_repository import BookingRepository
from app.repositories.integration_repository import IntegrationRepository
from app.repositories.user_repository import UserRepository
from app.schemas.integration import GoogleCalendarStatus
from app.services.google_calendar_client import GoogleCalendarClient, GoogleClientError

logger = logging.getLogger(__name__)

NOT_CONFIGURED_MESSAGE = "Google Calendar is not configured."
STATE_PURPOSE = "google_calendar_connect"
STATE_TTL = timedelta(minutes=10)


class GoogleIntegrationService:
    def __init__(
        self,
        session: AsyncSession,
        integrations: IntegrationRepository,
        bookings: BookingRepository,
        users: UserRepository,
        client: GoogleCalendarClient,
        cipher: TokenCipher | None,
        settings: Settings,
    ) -> None:
        self.session = session
        self.integrations = integrations
        self.bookings = bookings
        self.users = users
        self.client = client
        self.cipher = cipher
        self.settings = settings

    # --- status ------------------------------------------------------------------

    async def status(self) -> GoogleCalendarStatus:
        if not self.settings.google_configured:
            return GoogleCalendarStatus(configured=False, connected=False)
        integration = await self.integrations.get_current()
        if integration is None or not integration.refresh_token_encrypted:
            return GoogleCalendarStatus(configured=True, connected=False)
        connected = integration.is_connected
        calendar_id = integration.calendar_id
        return GoogleCalendarStatus(
            configured=True,
            connected=connected,
            google_email=integration.google_email,
            calendar_id=calendar_id,
            # Reading calendar metadata needs a broader scope than we request, so the
            # primary calendar is named after its owner (as Google does).
            calendar_name=integration.google_email if calendar_id == "primary" else calendar_id,
            last_synced_at=await self.bookings.last_synced_at(),
            needs_reconnect=not connected,
        )

    # --- connect -------------------------------------------------------------------

    def authorization_url(self, user: User) -> str:
        if not self.settings.google_configured:
            raise ServiceUnavailableError(NOT_CONFIGURED_MESSAGE)
        return self.client.build_authorization_url(self._sign_state(user.id))

    def _sign_state(self, user_id: UUID) -> str:
        now = datetime.now(timezone.utc)
        payload = {"sub": str(user_id), "purpose": STATE_PURPOSE, "iat": now, "exp": now + STATE_TTL}
        return jwt.encode(payload, self.settings.jwt_secret, algorithm=self.settings.jwt_algorithm)

    def _read_state(self, state: str | None) -> UUID | None:
        if not state:
            return None
        try:
            payload = jwt.decode(
                state,
                self.settings.jwt_secret,
                algorithms=[self.settings.jwt_algorithm],
                options={"require": ["exp", "sub"]},
            )
            if payload.get("purpose") != STATE_PURPOSE:
                return None
            return UUID(payload["sub"])
        except (jwt.PyJWTError, ValueError, KeyError):
            return None

    def redirect_url(self, error_reason: str | None) -> str:
        base = f"{self.settings.frontend_url}/dashboard/integrations"
        if error_reason is None:
            return f"{base}?google=connected"
        return f"{base}?{urlencode({'google': 'error', 'reason': error_reason})}"

    async def handle_callback(self, code: str | None, state: str | None, error: str | None) -> str | None:
        """Returns None on success, otherwise a short reason code for the frontend."""
        if not self.settings.google_configured or self.cipher is None:
            return "not_configured"
        user_id = self._read_state(state)
        if user_id is None or await self.users.get_by_id(user_id) is None:
            return "invalid_state"
        if error or not code:
            return "denied" if error else "exchange_failed"
        try:
            await self._connect(user_id, code)
        except GoogleClientError as exc:
            logger.warning("Google code exchange failed: %s", type(exc).__name__)
            return "exchange_failed"
        return None

    async def _connect(self, user_id: UUID, code: str) -> None:
        assert self.cipher is not None
        tokens = await self.client.exchange_code(code)
        email = await self.client.get_user_email(tokens.access_token)
        integration = await self.integrations.get_for_user(user_id)
        if tokens.refresh_token:
            refresh_encrypted = self.cipher.encrypt(tokens.refresh_token)
        elif integration is not None and integration.refresh_token_encrypted:
            refresh_encrypted = integration.refresh_token_encrypted
        else:
            raise GoogleClientError("Google did not return a refresh token")
        values = {
            "google_email": email,
            "calendar_id": self.settings.google_calendar_default_id,
            "access_token_encrypted": self.cipher.encrypt(tokens.access_token),
            "refresh_token_encrypted": refresh_encrypted,
            "token_expiry": datetime.now(timezone.utc) + timedelta(seconds=tokens.expires_in),
            "scope": tokens.scope,
            "is_connected": True,
        }
        if integration is None:
            await self.integrations.add(GoogleCalendarIntegration(user_id=user_id, **values))
        else:
            for name, value in values.items():
                setattr(integration, name, value)
        await self.session.commit()

    # --- disconnect ----------------------------------------------------------------

    async def disconnect(self) -> None:
        integration = await self.integrations.get_current()
        if integration is None:
            return
        if self.cipher is not None and integration.refresh_token_encrypted:
            try:
                await self.client.revoke(self.cipher.decrypt(integration.refresh_token_encrypted))
            except Exception as exc:  # noqa: BLE001 - revocation is best effort
                logger.warning("Could not revoke Google token: %s", type(exc).__name__)
        integration.access_token_encrypted = ""
        integration.refresh_token_encrypted = ""
        integration.token_expiry = None
        integration.is_connected = False
        await self.session.commit()
