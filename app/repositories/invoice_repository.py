from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from uuid import UUID

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.models.booking import Booking
from app.models.invoice import Invoice, InvoiceStatus


@dataclass
class InvoiceFilters:
    statuses: list[InvoiceStatus] = field(default_factory=list)
    search: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    overdue: bool = False
    today: date | None = None


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _apply_filters(stmt: Select, filters: InvoiceFilters) -> Select:
    stmt = stmt.join(Booking, Booking.id == Invoice.booking_id)
    if filters.statuses:
        stmt = stmt.where(Invoice.status.in_([s.value for s in filters.statuses]))
    if filters.date_from:
        stmt = stmt.where(Invoice.issue_date >= filters.date_from)
    if filters.date_to:
        stmt = stmt.where(Invoice.issue_date <= filters.date_to)
    if filters.overdue and filters.today:
        stmt = stmt.where(
            Invoice.status.in_([InvoiceStatus.ISSUED.value, InvoiceStatus.PARTIALLY_PAID.value]),
            Invoice.balance_due > 0,
            Invoice.due_date < filters.today,
        )
    if filters.search and filters.search.strip():
        term = _escape_like(filters.search.strip().lower())
        stmt = stmt.where(
            or_(
                func.lower(Invoice.invoice_number).like(f"%{term}%", escape="\\"),
                func.lower(Invoice.customer_name).like(f"%{term}%", escape="\\"),
                func.lower(Booking.booking_number).like(f"%{term}%", escape="\\"),
            )
        )
    return stmt


class InvoiceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, invoice_id: UUID) -> Invoice | None:
        return await self.session.get(Invoice, invoice_id)

    async def add(self, invoice: Invoice) -> Invoice:
        self.session.add(invoice)
        await self.session.flush()
        return invoice

    async def delete(self, invoice: Invoice) -> None:
        # Items go with it (ON DELETE CASCADE); a bulk delete avoids loading/orphaning them.
        await self.session.execute(delete(Invoice).where(Invoice.id == invoice.id))

    async def active_for_booking(self, booking_id: UUID) -> Invoice | None:
        stmt = (
            select(Invoice)
            .where(Invoice.booking_id == booking_id, Invoice.status != InvoiceStatus.CANCELLED.value)
            .order_by(Invoice.created_at.desc())
            .limit(1)
        )
        return await self.session.scalar(stmt)

    async def for_booking(self, booking_id: UUID) -> Invoice | None:
        """The invoice to show on a booking: the active one, else the most recent."""
        stmt = (
            select(Invoice)
            .where(Invoice.booking_id == booking_id)
            .order_by(
                (Invoice.status == InvoiceStatus.CANCELLED.value).asc(),
                Invoice.created_at.desc(),
            )
            .limit(1)
        )
        return await self.session.scalar(stmt)

    async def count_for_booking(self, booking_id: UUID) -> int:
        stmt = select(func.count(Invoice.id)).where(Invoice.booking_id == booking_id)
        return int(await self.session.scalar(stmt) or 0)

    async def list(
        self, filters: InvoiceFilters, page: int, page_size: int
    ) -> tuple[Sequence[Invoice], int]:
        total = await self.session.scalar(_apply_filters(select(func.count(Invoice.id)), filters))
        stmt = (
            _apply_filters(select(Invoice), filters)
            .order_by(Invoice.issue_date.desc(), Invoice.created_at.desc(), Invoice.id.desc())
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
        return (await self.session.scalars(stmt)).unique().all(), int(total or 0)

    async def last_invoice_number(self, prefix: str) -> str | None:
        stmt = (
            select(Invoice.invoice_number)
            .where(Invoice.invoice_number.like(f"{prefix}%"))
            .order_by(func.length(Invoice.invoice_number).desc(), Invoice.invoice_number.desc())
            .limit(1)
        )
        return await self.session.scalar(stmt)

