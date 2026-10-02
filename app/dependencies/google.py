from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.crypto import TokenCipher
from app.core.database import get_session
from app.repositories.booking_repository import BookingRepository
from app.repositories.integration_repository import IntegrationRepository
from app.repositories.user_repository import UserRepository
from app.services.calendar_sync_service import CalendarSyncService
from app.services.google_calendar_client import GoogleCalendarClient
from app.services.google_integration_service import GoogleIntegrationService


def get_google_client(settings: Settings = Depends(get_settings)) -> GoogleCalendarClient:
    return GoogleCalendarClient(settings)


def get_token_cipher(settings: Settings = Depends(get_settings)) -> TokenCipher | None:
    return TokenCipher(settings.token_encryption_key) if settings.token_encryption_key else None


def get_calendar_sync_service(
    session: AsyncSession = Depends(get_session),
    client: GoogleCalendarClient = Depends(get_google_client),
    cipher: TokenCipher | None = Depends(get_token_cipher),
    settings: Settings = Depends(get_settings),
) -> CalendarSyncService:
    return CalendarSyncService(session, IntegrationRepository(session), client, cipher, settings)


def get_google_integration_service(
    session: AsyncSession = Depends(get_session),
    client: GoogleCalendarClient = Depends(get_google_client),
    cipher: TokenCipher | None = Depends(get_token_cipher),
    settings: Settings = Depends(get_settings),
) -> GoogleIntegrationService:
    return GoogleIntegrationService(
        session,
        IntegrationRepository(session),
        BookingRepository(session),
        UserRepository(session),
        client,
        cipher,
        settings,
    )
