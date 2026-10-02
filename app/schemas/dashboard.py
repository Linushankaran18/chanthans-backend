from pydantic import BaseModel

from app.schemas.booking import BookingSummary


class DashboardSummary(BaseModel):
    today: str
    timezone: str
    today_count: int
    today_bookings: list[BookingSummary]
    next_booking: BookingSummary | None
    upcoming_count: int
    upcoming: list[BookingSummary]
    status_counts: dict[str, int]
    ready_for_delivery_count: int
