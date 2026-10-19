from __future__ import annotations

import logging
import math
import secrets
from collections.abc import Sequence
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi.exceptions import RequestValidationError
from pydantic_core import PydanticCustomError
from sqlalchemy import delete as sql_delete
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import AppError, BadRequestError, ConflictError, NotFoundError
from app.models.booking import ALLOWED_TRANSITIONS, Booking, BookingStatus
from app.models.invoice import Invoice, InvoiceStatus
from app.repositories.booking_repository import BookingFilters, BookingRepository
from app.schemas.booking import (
    BookingConflict,
    BookingCreate,
    BookingUpdate,
    validate_schedule_and_money,
)
from app.services.calendar_sync_service import CalendarSyncService

logger = logging.getLogger(__name__)

_NUMBER_ATTEMPTS = 8
_SCHEDULE_FIELDS = ("event_date", "start_time", "end_time")


class BookingConflictError(AppError):
    status_code = 409
    detail = "Another confirmed booking overlaps with this time."


def _label(status: BookingStatus | str) -> str:
    return str(status).replace("_", " ").capitalize()


class BookingService:
    def __init__(
        self,
        session: AsyncSession,
        repo: BookingRepository,
        calendar: CalendarSyncService,
        settings: Settings,
    ) -> None:
        self.session = session
        self.repo = repo
        self.calendar = calendar
        self.settings = settings

    # --- reads -----------------------------------------------------------------

    async def list(
        self, filters: BookingFilters, sort: str, page: int, page_size: int
    ) -> tuple[Sequence[Booking], int, int]:
        items, total = await self.repo.list(filters, sort, page, page_size)
        return items, total, max(1, math.ceil(total / page_size))

    async def service_types(self) -> list[str]:
        return await self.repo.service_types()

    async def get(self, booking_id: UUID) -> Booking:
        booking = await self.repo.get(booking_id)
        if booking is None:
            raise NotFoundError("Booking not found")
        return booking

    # --- writes ----------------------------------------------------------------

    async def create(self, data: BookingCreate) -> Booking:
        if data.status == BookingStatus.CONFIRMED and not data.allow_overlap:
            await self._ensure_no_conflict(data.event_date, data.start_time, data.end_time)
        booking = Booking(**data.model_dump(exclude={"status", "allow_overlap"}), status=data.status.value)
        await self._add_with_number(booking)
        await self.session.commit()
        await self.calendar.sync(booking)
        return booking

    async def update(self, booking_id: UUID, data: BookingUpdate) -> Booking:
        booking = await self.get(booking_id)
        changes = data.model_dump(exclude_unset=True, exclude={"allow_overlap"})
        self._validate_merged(booking, changes)
        schedule_changed = any(
            name in changes and changes[name] != getattr(booking, name) for name in _SCHEDULE_FIELDS
        )
        merged = {name: changes.get(name, getattr(booking, name)) for name in _SCHEDULE_FIELDS}
        if schedule_changed and booking.status == BookingStatus.CONFIRMED and not data.allow_overlap:
            await self._ensure_no_conflict(
                merged["event_date"], merged["start_time"], merged["end_time"], exclude_id=booking.id
            )
        for name, value in changes.items():
            setattr(booking, name, value)
        await self.session.commit()
        await self.calendar.sync(booking)
        return booking

    async def set_status(
        self, booking_id: UUID, new_status: BookingStatus, allow_overlap: bool = False
    ) -> Booking:
        booking = await self.get(booking_id)
        current = BookingStatus(booking.status)
        if new_status not in ALLOWED_TRANSITIONS[current]:
            raise BadRequestError(f"A {_label(current)} booking cannot be moved to {_label(new_status)}.")
        if new_status == BookingStatus.CONFIRMED and not allow_overlap:
            await self._ensure_no_conflict(
                booking.event_date, booking.start_time, booking.end_time, exclude_id=booking.id
            )
        booking.status = new_status.value
        await self.session.commit()
        if new_status in (BookingStatus.CONFIRMED, BookingStatus.CANCELLED):
            await self.calendar.sync(booking)
        return booking

    async def delete(self, booking_id: UUID, delete_invoice: bool = False) -> None:
        booking = await self.get(booking_id)
        invoices = (await self.session.scalars(select(Invoice).where(Invoice.booking_id == booking.id))).all()
        if invoices:
            # Only drafts and cancelled invoices may disappear; issued/paid ones are financial records.
            deletable = {InvoiceStatus.DRAFT.value, InvoiceStatus.CANCELLED.value}
            if not delete_invoice or any(inv.status not in deletable for inv in invoices):
                raise ConflictError(
                    "This booking has an invoice. Cancel and delete the invoice first.",
                    extra={"code": "booking_has_invoice"},
                )
            await self.session.execute(sql_delete(Invoice).where(Invoice.booking_id == booking.id))
        event_id = booking.google_calendar_event_id
        await self.repo.delete(booking)
        await self.session.commit()
        await self.calendar.delete_event_best_effort(event_id)

    # --- team share link -------------------------------------------------------

    async def enable_share(self, booking_id: UUID) -> Booking:
        """Idempotent: keeps the existing link so one that was already sent keeps working."""
        booking = await self.get(booking_id)
        if not booking.share_token:
            booking.share_token = secrets.token_urlsafe(24)
            await self.session.commit()
        return booking

    async def revoke_share(self, booking_id: UUID) -> Booking:
        booking = await self.get(booking_id)
        if booking.share_token:
            booking.share_token = None
            await self.session.commit()
        return booking

    async def get_shared(self, token: str) -> Booking:
        """Public lookup. Unknown, revoked and expired links are indistinguishable (404)."""
        booking = await self.repo.get_by_share_token(token)
        if booking is None:
            raise NotFoundError("This link is no longer available.")
        # The link stops working the day after the event (studio-local).
        if datetime.now(self.settings.timezone).date() > booking.event_date + timedelta(days=1):
            raise NotFoundError("This link is no longer available.")
        return booking

    async def sync_calendar(self, booking_id: UUID) -> Booking:
        booking = await self.get(booking_id)
        await self.calendar.sync_manually(booking)
        return booking

    # --- helpers ---------------------------------------------------------------

    async def _add_with_number(self, booking: Booking) -> None:
        """Assign the next BK-YYYY-NNNN number; retry if a concurrent create took it."""
        prefix = f"BK-{datetime.now(self.settings.timezone).year}-"
        for _ in range(_NUMBER_ATTEMPTS):
            last = await self.repo.last_booking_number(prefix)
            sequence = int(last.rsplit("-", 1)[1]) + 1 if last else 1
            booking.booking_number = f"{prefix}{sequence:04d}"
            try:
                async with self.session.begin_nested():
                    await self.repo.add(booking)
                return
            except IntegrityError:
                logger.info("Booking number collision, retrying")
        raise AppError("Could not generate a booking number. Please try again.")

    async def _ensure_no_conflict(
        self, event_date: date, start: time, end: time, exclude_id: UUID | None = None
    ) -> None:
        conflicts = await self.repo.find_conflicts(event_date, start, end, exclude_id)
        if conflicts:
            raise BookingConflictError(
                extra={
                    "code": "booking_conflict",
                    "conflicts": [
                        BookingConflict.model_validate(c).model_dump(mode="json") for c in conflicts
                    ],
                }
            )

    @staticmethod
    def _validate_merged(booking: Booking, changes: dict[str, Any]) -> None:
        def pick(name: str) -> Any:
            return changes[name] if name in changes else getattr(booking, name)

        total: Decimal | None = pick("total_amount")
        deposit: Decimal | None = pick("deposit_amount")
        try:
            validate_schedule_and_money(pick("start_time"), pick("end_time"), total, deposit)
        except PydanticCustomError as exc:
            field = "end_time" if "time" in exc.message_template else "deposit_amount"
            raise RequestValidationError(
                [{"type": "value_error", "loc": ("body", field), "msg": exc.message_template, "input": None}]
            ) from exc
