from datetime import date, datetime, time
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    PlainSerializer,
    computed_field,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

from app.models.booking import BookingStatus, CalendarSyncStatus, allowed_next_statuses

Money = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)]
# Money is stored as Numeric but serialised as a JSON number.
MoneyOut = Annotated[
    Decimal | None,
    PlainSerializer(lambda v: None if v is None else float(v), return_type=float | None, when_used="json"),
]

_REQUIRED_LABELS = {
    "customer_name": "Customer name",
    "customer_phone": "Phone number",
    "service_type": "Service type",
    "event_date": "Event date",
    "start_time": "Start time",
    "end_time": "End time",
}
_BLANK_TO_NONE = ("customer_email", "location", "notes")


def _friendly(message: str) -> PydanticCustomError:
    return PydanticCustomError("value_error", message)


def validate_schedule_and_money(
    start: time | None, end: time | None, total: Decimal | None, deposit: Decimal | None
) -> None:
    """Shared by request validation and the service (which re-checks merged updates)."""
    if start is not None and end is not None and end <= start:
        raise _friendly("End time must be later than the start time.")
    if total is not None and deposit is not None and deposit > total:
        raise _friendly("Deposit cannot be greater than the total amount.")


class _BookingInput(BaseModel):
    """Common cleaning for create/update payloads."""

    @field_validator(*_REQUIRED_LABELS, mode="before", check_fields=False)
    @classmethod
    def _required_not_blank(cls, value: Any, info: Any) -> Any:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise _friendly(f"{_REQUIRED_LABELS[info.field_name]} is required.")
        return value

    @field_validator(*_BLANK_TO_NONE, "delivery_due_date", mode="before", check_fields=False)
    @classmethod
    def _blank_to_none(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value


class BookingCreate(_BookingInput):
    customer_name: str = Field(max_length=255)
    customer_email: EmailStr | None = None
    customer_phone: str = Field(max_length=50)
    service_type: str = Field(max_length=100)
    event_date: date
    start_time: time
    end_time: time
    location: str | None = Field(None, max_length=500)
    status: BookingStatus = BookingStatus.INQUIRY
    total_amount: Money | None = None
    deposit_amount: Money | None = None
    delivery_due_date: date | None = None
    notes: str | None = Field(None, max_length=5000)

    @model_validator(mode="after")
    def _check(self) -> "BookingCreate":
        validate_schedule_and_money(self.start_time, self.end_time, self.total_amount, self.deposit_amount)
        return self


class BookingUpdate(_BookingInput):
    """Partial update; omitted fields are untouched. Status is changed via PATCH /status."""

    customer_name: str | None = Field(None, max_length=255)
    customer_email: EmailStr | None = None
    customer_phone: str | None = Field(None, max_length=50)
    service_type: str | None = Field(None, max_length=100)
    event_date: date | None = None
    start_time: time | None = None
    end_time: time | None = None
    location: str | None = Field(None, max_length=500)
    total_amount: Money | None = None
    deposit_amount: Money | None = None
    delivery_due_date: date | None = None
    notes: str | None = Field(None, max_length=5000)

    @model_validator(mode="after")
    def _check(self) -> "BookingUpdate":
        for name, label in _REQUIRED_LABELS.items():
            if name in self.model_fields_set and getattr(self, name) is None:
                raise _friendly(f"{label} is required.")
        validate_schedule_and_money(self.start_time, self.end_time, self.total_amount, self.deposit_amount)
        return self


class BookingStatusUpdate(BaseModel):
    status: BookingStatus


class BookingSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    booking_number: str
    customer_name: str
    customer_email: str | None
    customer_phone: str
    service_type: str
    event_date: date
    start_time: time
    end_time: time
    location: str | None
    status: BookingStatus
    total_amount: MoneyOut
    deposit_amount: MoneyOut
    delivery_due_date: date | None
    calendar_sync_status: CalendarSyncStatus
    calendar_synced_at: datetime | None
    calendar_sync_error: str | None
    created_at: datetime
    updated_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def balance_amount(self) -> MoneyOut:
        if self.total_amount is None:
            return None
        return Decimal(self.total_amount) - Decimal(self.deposit_amount or 0)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def allowed_next_statuses(self) -> list[BookingStatus]:
        return allowed_next_statuses(self.status)


class BookingDetail(BookingSummary):
    notes: str | None
    google_calendar_event_id: str | None


class BookingPage(BaseModel):
    items: list[BookingSummary]
    total: int
    page: int
    page_size: int
    pages: int


class BookingConflict(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    booking_number: str
    customer_name: str
    service_type: str
    event_date: date
    start_time: time
    end_time: time
    status: BookingStatus
