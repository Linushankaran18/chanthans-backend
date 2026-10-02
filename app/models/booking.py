from datetime import date, datetime, time
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import Date, DateTime, Numeric, String, Text, Time
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class BookingStatus(StrEnum):
    INQUIRY = "INQUIRY"
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    SHOOT_COMPLETED = "SHOOT_COMPLETED"
    EDITING = "EDITING"
    READY = "READY"
    DELIVERED = "DELIVERED"
    CANCELLED = "CANCELLED"


class CalendarSyncStatus(StrEnum):
    NOT_SYNCED = "NOT_SYNCED"
    SYNCED = "SYNCED"
    FAILED = "FAILED"


ALLOWED_TRANSITIONS: dict[BookingStatus, tuple[BookingStatus, ...]] = {
    BookingStatus.INQUIRY: (BookingStatus.PENDING, BookingStatus.CONFIRMED, BookingStatus.CANCELLED),
    BookingStatus.PENDING: (BookingStatus.CONFIRMED, BookingStatus.CANCELLED),
    BookingStatus.CONFIRMED: (BookingStatus.SHOOT_COMPLETED, BookingStatus.CANCELLED),
    BookingStatus.SHOOT_COMPLETED: (BookingStatus.EDITING,),
    BookingStatus.EDITING: (BookingStatus.READY,),
    BookingStatus.READY: (BookingStatus.DELIVERED,),
    BookingStatus.DELIVERED: (),
    BookingStatus.CANCELLED: (),
}

# Statuses whose bookings live on the Google Calendar (confirmed and later, not cancelled).
CALENDAR_STATUSES = frozenset(
    {
        BookingStatus.CONFIRMED,
        BookingStatus.SHOOT_COMPLETED,
        BookingStatus.EDITING,
        BookingStatus.READY,
        BookingStatus.DELIVERED,
    }
)


def allowed_next_statuses(status: str) -> list[BookingStatus]:
    return list(ALLOWED_TRANSITIONS.get(BookingStatus(status), ()))


class Booking(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "bookings"

    booking_number: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    customer_name: Mapped[str] = mapped_column(String(255))
    customer_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    customer_phone: Mapped[str] = mapped_column(String(50))
    service_type: Mapped[str] = mapped_column(String(100))
    # Studio-local wall-clock values (see APP_TIMEZONE); no timezone attached.
    event_date: Mapped[date] = mapped_column(Date, index=True)
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)
    location: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default=BookingStatus.INQUIRY.value, index=True)
    total_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    deposit_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    delivery_due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    google_calendar_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    calendar_sync_status: Mapped[str] = mapped_column(
        String(16), default=CalendarSyncStatus.NOT_SYNCED.value
    )
    calendar_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    calendar_sync_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
