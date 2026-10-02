from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time
from uuid import UUID

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.models.booking import Booking, BookingStatus

_PHONE_NOISE = (" ", "-", "(", ")", ".")


def normalize_phone(value: str) -> str:
    for char in _PHONE_NOISE:
        value = value.replace(char, "")
    return value


@dataclass
class BookingFilters:
    statuses: list[BookingStatus] = field(default_factory=list)
    search: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    service_type: str | None = None


def _phone_expression():
    expr = Booking.customer_phone
    for char in _PHONE_NOISE:
        expr = func.replace(expr, char, "")
    return expr


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _apply_filters(stmt: Select, filters: BookingFilters) -> Select:
    if filters.statuses:
        stmt = stmt.where(Booking.status.in_([s.value for s in filters.statuses]))
    if filters.date_from:
        stmt = stmt.where(Booking.event_date >= filters.date_from)
    if filters.date_to:
        stmt = stmt.where(Booking.event_date <= filters.date_to)
    if filters.service_type:
        stmt = stmt.where(func.lower(Booking.service_type) == filters.service_type.lower())
    if filters.search and filters.search.strip():
        term = _escape_like(filters.search.strip().lower())
        clauses = [
            func.lower(Booking.customer_name).like(f"%{term}%", escape="\\"),
            func.lower(func.coalesce(Booking.customer_email, "")).like(f"%{term}%", escape="\\"),
            func.lower(Booking.booking_number).like(f"%{term}%", escape="\\"),
            Booking.customer_phone.like(f"%{term}%", escape="\\"),
        ]
        digits = normalize_phone(filters.search.strip())
        if digits:
            clauses.append(_phone_expression().like(f"%{_escape_like(digits)}%", escape="\\"))
        stmt = stmt.where(or_(*clauses))
    return stmt


def _order_by(sort: str) -> list:
    if sort == "date_asc":
        return [Booking.event_date.asc(), Booking.start_time.asc(), Booking.id.asc()]
    if sort == "created_desc":
        return [Booking.created_at.desc(), Booking.id.desc()]
    return [Booking.event_date.desc(), Booking.start_time.desc(), Booking.id.desc()]


def _not_finished(today: date, now_time: time):
    return or_(
        Booking.event_date > today,
        and_(Booking.event_date == today, Booking.end_time > now_time),
    )


class BookingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, booking_id: UUID) -> Booking | None:
        return await self.session.get(Booking, booking_id)

    async def add(self, booking: Booking) -> Booking:
        self.session.add(booking)
        await self.session.flush()
        return booking

    async def delete(self, booking: Booking) -> None:
        await self.session.execute(delete(Booking).where(Booking.id == booking.id))

    async def list(
        self, filters: BookingFilters, sort: str, page: int, page_size: int
    ) -> tuple[Sequence[Booking], int]:
        total = await self.session.scalar(_apply_filters(select(func.count(Booking.id)), filters))
        stmt = (
            _apply_filters(select(Booking), filters)
            .order_by(*_order_by(sort))
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
        rows = (await self.session.scalars(stmt)).all()
        return rows, int(total or 0)

    async def service_types(self) -> list[str]:
        stmt = select(Booking.service_type).distinct().order_by(func.lower(Booking.service_type))
        return list((await self.session.scalars(stmt)).all())

    async def last_booking_number(self, prefix: str) -> str | None:
        # Order by length first so the sequence keeps working past 9999 ("...-10000" > "...-9999").
        stmt = (
            select(Booking.booking_number)
            .where(Booking.booking_number.like(f"{prefix}%"))
            .order_by(func.length(Booking.booking_number).desc(), Booking.booking_number.desc())
            .limit(1)
        )
        return await self.session.scalar(stmt)

    async def find_conflicts(
        self, event_date: date, start: time, end: time, exclude_id: UUID | None = None
    ) -> Sequence[Booking]:
        stmt = select(Booking).where(
            Booking.event_date == event_date,
            Booking.status == BookingStatus.CONFIRMED.value,
            Booking.start_time < end,
            Booking.end_time > start,
        )
        if exclude_id is not None:
            stmt = stmt.where(Booking.id != exclude_id)
        return (await self.session.scalars(stmt.order_by(Booking.start_time))).all()

    async def last_synced_at(self) -> datetime | None:
        return await self.session.scalar(select(func.max(Booking.calendar_synced_at)))

    # --- dashboard queries -------------------------------------------------

    async def status_counts(self) -> dict[str, int]:
        rows = await self.session.execute(select(Booking.status, func.count(Booking.id)).group_by(Booking.status))
        return {status: count for status, count in rows.all()}

    async def non_cancelled_on(self, day: date) -> Sequence[Booking]:
        stmt = (
            select(Booking)
            .where(Booking.event_date == day, Booking.status != BookingStatus.CANCELLED.value)
            .order_by(Booking.start_time, Booking.id)
        )
        return (await self.session.scalars(stmt)).all()

    async def upcoming(
        self, today: date, now_time: time, statuses: Sequence[BookingStatus], limit: int | None
    ) -> Sequence[Booking]:
        stmt = (
            select(Booking)
            .where(Booking.status.in_([s.value for s in statuses]), _not_finished(today, now_time))
            .order_by(Booking.event_date, Booking.start_time, Booking.id)
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        return (await self.session.scalars(stmt)).all()

    async def count_upcoming(self, today: date, now_time: time, status: BookingStatus) -> int:
        stmt = select(func.count(Booking.id)).where(
            Booking.status == status.value, _not_finished(today, now_time)
        )
        return int(await self.session.scalar(stmt) or 0)
