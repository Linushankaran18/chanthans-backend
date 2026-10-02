"""Keeps Google Calendar events in step with bookings.

Sync is a side effect: every public method that runs as part of a booking operation
swallows errors, records them on the booking (friendly text only) and never raises.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.crypto import TokenCipher, TokenCipherError
from app.core.exceptions import ConflictError
from app.models.booking import CALENDAR_STATUSES, Booking, BookingStatus, CalendarSyncStatus
from app.models.google_calendar_integration import GoogleCalendarIntegration
from app.repositories.integration_repository import IntegrationRepository
from app.services.google_calendar_client import (
    GoogleApiError,
    GoogleAuthError,
    GoogleCalendarClient,
    GoogleNetworkError,
)

logger = logging.getLogger(__name__)

NOT_CONNECTED_MESSAGE = "Google Calendar is not connected."
_RECONNECT_MESSAGE = "Google Calendar needs to be reconnected."
_NETWORK_MESSAGE = "Could not reach Google Calendar. Please try again."
_GENERIC_MESSAGE = "Google Calendar could not be updated. Please try again."
_EXPIRY_MARGIN = timedelta(seconds=60)
_GONE = (404, 410)


class _NotConnectedError(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def build_event_payload(booking: Booking, timezone_name: str) -> dict[str, Any]:
    start = datetime.combine(booking.event_date, booking.start_time).replace(microsecond=0)
    end = datetime.combine(booking.event_date, booking.end_time).replace(microsecond=0)
    lines = [f"Booking: {booking.booking_number}", f"Phone: {booking.customer_phone}"]
    if booking.customer_email:
        lines.append(f"Email: {booking.customer_email}")
    if booking.total_amount is not None:
        balance = booking.total_amount - (booking.deposit_amount or 0)
        lines.append(f"Balance: {balance:.2f}")
    if booking.notes:
        lines.append(f"Notes: {booking.notes}")
    event: dict[str, Any] = {
        "summary": f"{booking.service_type} — {booking.customer_name}",
        "description": "\n".join(lines),
        "start": {"dateTime": start.isoformat(), "timeZone": timezone_name},
        "end": {"dateTime": end.isoformat(), "timeZone": timezone_name},
        "reminders": {"useDefault": True},
    }
    if booking.location:
        event["location"] = booking.location
    return event


class CalendarSyncService:
    def __init__(
        self,
        session: AsyncSession,
        integrations: IntegrationRepository,
        client: GoogleCalendarClient,
        cipher: TokenCipher | None,
        settings: Settings,
    ) -> None:
        self.session = session
        self.integrations = integrations
        self.client = client
        self.cipher = cipher
        self.settings = settings

    # --- public API ----------------------------------------------------------

    async def sync(self, booking: Booking) -> None:
        """Bring the calendar in line with the booking. Never raises."""
        try:
            await self._sync(booking)
        except _NotConnectedError:
            return
        except Exception as exc:  # noqa: BLE001 - sync must never break a booking operation
            await self._record_failure(booking, exc)

    async def sync_manually(self, booking: Booking) -> None:
        if await self._usable_integration() is None:
            raise ConflictError(NOT_CONNECTED_MESSAGE)
        await self.sync(booking)

    async def delete_event_best_effort(self, event_id: str | None) -> None:
        if not event_id:
            return
        try:
            integration, token = await self._credentials()
            await self.client.delete_event(token, integration.calendar_id, event_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not delete Google Calendar event: %s", type(exc).__name__)

    # --- internals -----------------------------------------------------------

    async def _usable_integration(self) -> GoogleCalendarIntegration | None:
        if not self.settings.google_configured or self.cipher is None:
            return None
        integration = await self.integrations.get_current()
        if integration is None or not integration.refresh_token_encrypted:
            return None
        return integration

    async def _credentials(self) -> tuple[GoogleCalendarIntegration, str]:
        integration = await self._usable_integration()
        if integration is None:
            raise _NotConnectedError()
        if not integration.is_connected:
            raise GoogleAuthError("needs_reconnect")
        return integration, await self._access_token(integration)

    async def _access_token(self, integration: GoogleCalendarIntegration) -> str:
        assert self.cipher is not None
        expiry = _aware(integration.token_expiry)
        if integration.access_token_encrypted and expiry and expiry > _now() + _EXPIRY_MARGIN:
            return self.cipher.decrypt(integration.access_token_encrypted)
        refresh_token = self.cipher.decrypt(integration.refresh_token_encrypted)
        try:
            tokens = await self.client.refresh_access_token(refresh_token)
        except GoogleAuthError:
            integration.is_connected = False  # refresh token kept: UI reports needs_reconnect
            await self.session.commit()
            raise
        integration.access_token_encrypted = self.cipher.encrypt(tokens.access_token)
        integration.token_expiry = _now() + timedelta(seconds=tokens.expires_in)
        await self.session.commit()
        return tokens.access_token

    async def _sync(self, booking: Booking) -> None:
        status = BookingStatus(booking.status)
        if status != BookingStatus.CANCELLED and status not in CALENDAR_STATUSES:
            return
        integration, token = await self._credentials()
        if status == BookingStatus.CANCELLED:
            await self._remove(booking, integration, token)
        else:
            await self._upsert(booking, integration, token)

    async def _upsert(self, booking: Booking, integration: GoogleCalendarIntegration, token: str) -> None:
        event = build_event_payload(booking, self.settings.app_timezone)
        event_id = booking.google_calendar_event_id
        calendar_id = integration.calendar_id
        if event_id:
            try:
                await self.client.update_event(token, calendar_id, event_id, event)
            except GoogleApiError as exc:
                if exc.status_code not in _GONE:
                    raise
                event_id = None  # deleted in Google: recreate instead of failing
        if not event_id:
            event_id = await self.client.create_event(token, calendar_id, event)
        booking.google_calendar_event_id = event_id
        booking.calendar_sync_status = CalendarSyncStatus.SYNCED.value
        booking.calendar_synced_at = _now()
        booking.calendar_sync_error = None
        await self.session.commit()

    async def _remove(self, booking: Booking, integration: GoogleCalendarIntegration, token: str) -> None:
        if booking.google_calendar_event_id:
            await self.client.delete_event(token, integration.calendar_id, booking.google_calendar_event_id)
        booking.google_calendar_event_id = None
        booking.calendar_sync_status = CalendarSyncStatus.NOT_SYNCED.value
        booking.calendar_synced_at = None
        booking.calendar_sync_error = None
        await self.session.commit()

    async def _record_failure(self, booking: Booking, exc: Exception) -> None:
        logger.warning("Google Calendar sync failed: %s", type(exc).__name__)
        try:
            booking.calendar_sync_status = CalendarSyncStatus.FAILED.value
            booking.calendar_sync_error = _friendly_error(exc)
            await self.session.commit()
        except Exception:  # noqa: BLE001
            logger.exception("Could not record Google Calendar sync failure")
            await self.session.rollback()


def _friendly_error(exc: Exception) -> str:
    if isinstance(exc, (GoogleAuthError, TokenCipherError)):
        return _RECONNECT_MESSAGE
    if isinstance(exc, GoogleNetworkError):
        return _NETWORK_MESSAGE
    return _GENERIC_MESSAGE
