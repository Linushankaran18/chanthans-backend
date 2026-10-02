from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.database import get_session
from app.dependencies.google import get_calendar_sync_service
from app.repositories.booking_repository import BookingRepository
from app.services.booking_service import BookingService
from app.services.calendar_sync_service import CalendarSyncService
from app.services.dashboard_service import Clock, DashboardService, utc_clock


def get_booking_service(
    session: AsyncSession = Depends(get_session),
    calendar: CalendarSyncService = Depends(get_calendar_sync_service),
    settings: Settings = Depends(get_settings),
) -> BookingService:
    return BookingService(session, BookingRepository(session), calendar, settings)


def get_clock() -> Clock:
    """Overridable in tests to freeze "now"."""
    return utc_clock


def get_dashboard_service(
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
) -> DashboardService:
    return DashboardService(BookingRepository(session), settings, clock)
