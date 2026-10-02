from collections.abc import Callable
from datetime import datetime

from app.core.config import Settings
from app.models.booking import BookingStatus
from app.repositories.booking_repository import BookingRepository
from app.schemas.booking import BookingSummary
from app.schemas.dashboard import DashboardSummary

Clock = Callable[[], datetime]

_UPCOMING_LIMIT = 5
_NEXT_CANDIDATES = tuple(s for s in BookingStatus if s != BookingStatus.CANCELLED)


def utc_clock() -> datetime:
    return datetime.now().astimezone()


class DashboardService:
    def __init__(self, repo: BookingRepository, settings: Settings, clock: Clock = utc_clock) -> None:
        self.repo = repo
        self.settings = settings
        self.clock = clock

    async def summary(self) -> DashboardSummary:
        now = self.clock().astimezone(self.settings.timezone)
        today, now_time = now.date(), now.time().replace(tzinfo=None)

        counts = {status.value: 0 for status in BookingStatus}
        counts.update(await self.repo.status_counts())
        today_bookings = await self.repo.non_cancelled_on(today)
        next_up = await self.repo.upcoming(today, now_time, _NEXT_CANDIDATES, limit=1)
        upcoming = await self.repo.upcoming(
            today, now_time, (BookingStatus.CONFIRMED, BookingStatus.PENDING), limit=_UPCOMING_LIMIT
        )
        upcoming_count = await self.repo.count_upcoming(today, now_time, BookingStatus.CONFIRMED)

        return DashboardSummary(
            today=today.isoformat(),
            timezone=self.settings.app_timezone,
            today_count=len(today_bookings),
            today_bookings=[BookingSummary.model_validate(b) for b in today_bookings],
            next_booking=BookingSummary.model_validate(next_up[0]) if next_up else None,
            upcoming_count=upcoming_count,
            upcoming=[BookingSummary.model_validate(b) for b in upcoming],
            status_counts=counts,
            ready_for_delivery_count=counts[BookingStatus.READY.value],
        )
