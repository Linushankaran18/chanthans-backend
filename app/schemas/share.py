from datetime import date, time

from pydantic import BaseModel, ConfigDict

from app.models.booking import BookingStatus


class SharedEvent(BaseModel):
    """What a team member sees. Deliberately excludes amounts, phone, email and notes."""

    model_config = ConfigDict(from_attributes=True)

    service_type: str
    customer_name: str
    event_date: date
    start_time: time
    end_time: time
    location: str | None
    status: BookingStatus
    timezone: str = ""


class ShareLink(BaseModel):
    url: str
