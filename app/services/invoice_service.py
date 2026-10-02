from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import AppError, BadRequestError, ConflictError, NotFoundError
from app.models.booking import Booking
from app.models.invoice import ALLOWED_TRANSITIONS, Invoice, InvoiceItem, InvoiceStatus
from app.repositories.booking_repository import BookingRepository
from app.repositories.invoice_repository import InvoiceFilters, InvoiceRepository
from app.schemas.invoice import (
    InvoiceCreate,
    InvoiceDetail,
    InvoiceDraft,
    InvoiceDraftItem,
    InvoiceItemIn,
    InvoiceSettings,
    InvoiceSummary,
    InvoiceUpdate,
)
from app.services.dashboard_service import Clock
from app.services.invoice_calculator import InvoiceMathError, Totals, compute_totals, money, settle_status

logger = logging.getLogger(__name__)

_NUMBER_ATTEMPTS = 8
_EDITABLE = (
    "customer_name", "customer_email", "customer_phone", "billing_address", "issue_date", "due_date",
    "currency", "discount_amount", "tax_name", "tax_rate", "amount_paid", "notes", "terms",
)
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


class InvoiceExistsError(ConflictError):
    detail = "This booking already has an invoice."


def _validation_error(field: str, message: str) -> RequestValidationError:
    return RequestValidationError(
        [{"type": "value_error", "loc": ("body", field), "msg": message, "input": None}]
    )


def default_terms(settings: Settings) -> str:
    return (
        settings.default_invoice_terms.strip()
        or f"Payment is due within {settings.invoice_due_days} days of the invoice date."
    )


def invoice_settings(settings: Settings) -> InvoiceSettings:
    rate = settings.invoice_default_tax_rate
    return InvoiceSettings(
        business_name=settings.business_name,
        business_logo_url=settings.business_logo_url,
        business_address=settings.business_address,
        business_phone=settings.business_phone,
        business_email=settings.business_email,
        business_website=settings.business_website,
        currency=settings.business_currency,
        invoice_due_days=settings.invoice_due_days,
        tax_name=settings.invoice_default_tax_name.strip() or None,
        tax_rate=rate if rate > 0 else None,
        default_notes=settings.default_invoice_notes.strip() or None,
        default_terms=default_terms(settings),
    )


class InvoiceService:
    def __init__(
        self,
        session: AsyncSession,
        repo: InvoiceRepository,
        bookings: BookingRepository,
        settings: Settings,
        clock: Clock,
    ) -> None:
        self.session = session
        self.repo = repo
        self.bookings = bookings
        self.settings = settings
        self.clock = clock

    def today(self) -> date:
        return self.clock().astimezone(self.settings.timezone).date()

    # --- presentation ----------------------------------------------------------

    def summary(self, invoice: Invoice) -> InvoiceSummary:
        return self._decorate(InvoiceSummary.model_validate(invoice), invoice)

    def detail(self, invoice: Invoice) -> InvoiceDetail:
        return self._decorate(InvoiceDetail.model_validate(invoice), invoice)

    def _decorate(self, out, invoice: Invoice):
        overdue = invoice.overdue_on(self.today())
        return out.model_copy(
            update={"is_overdue": overdue, "display_status": InvoiceStatus.OVERDUE if overdue else out.status}
        )

    # --- reads -----------------------------------------------------------------

    async def list(
        self, filters: InvoiceFilters, page: int, page_size: int
    ) -> tuple[Sequence[Invoice], int, int]:
        filters.today = self.today()
        items, total = await self.repo.list(filters, page, page_size)
        return items, total, max(1, math.ceil(total / page_size))

    async def get(self, invoice_id: UUID) -> Invoice:
        invoice = await self.repo.get(invoice_id)
        if invoice is None:
            raise NotFoundError("Invoice not found")
        return invoice

    async def for_booking(self, booking_id: UUID) -> Invoice | None:
        await self._booking(booking_id)
        return await self.repo.for_booking(booking_id)

    async def prefill(self, booking_id: UUID) -> InvoiceDraft:
        booking = await self._booking(booking_id)
        existing = await self.repo.active_for_booking(booking_id)
        today = self.today()
        settings = self.settings
        rate = settings.invoice_default_tax_rate
        items = (
            [
                InvoiceDraftItem(
                    description=self._service_line(booking),
                    quantity=Decimal(1),
                    unit_price=booking.total_amount,
                )
            ]
            if booking.total_amount
            else []
        )
        return InvoiceDraft(
            booking_id=booking.id,
            booking_number=booking.booking_number,
            service_type=booking.service_type,
            event_date=booking.event_date,
            customer_name=booking.customer_name,
            customer_email=booking.customer_email,
            customer_phone=booking.customer_phone,
            billing_address=None,
            issue_date=today,
            due_date=today + timedelta(days=settings.invoice_due_days),
            currency=settings.business_currency,
            items=items,
            discount_amount=Decimal(0),
            tax_name=settings.invoice_default_tax_name.strip() or None if rate > 0 else None,
            tax_rate=Decimal(str(rate)) if rate > 0 else None,
            amount_paid=booking.deposit_amount or Decimal(0),
            notes=settings.default_invoice_notes.strip() or None,
            terms=default_terms(settings),
            next_invoice_number=await self._next_number(),
            existing_invoice_id=existing.id if existing else None,
        )

    # --- writes ----------------------------------------------------------------

    async def create(self, data: InvoiceCreate) -> Invoice:
        draft = await self.prefill(data.booking_id)
        if draft.existing_invoice_id:
            raise InvoiceExistsError(extra={"code": "invoice_exists", "invoice_id": str(draft.existing_invoice_id)})

        def pick(name: str) -> Any:
            return getattr(data, name) if name in data.model_fields_set else getattr(draft, name)

        items = (
            data.items
            if data.items is not None
            else [InvoiceItemIn(description=i.description, quantity=i.quantity, unit_price=i.unit_price) for i in draft.items]
        )
        values = {name: pick(name) for name in _EDITABLE}
        values["currency"] = (values["currency"] or draft.currency).upper()
        values["discount_amount"] = values["discount_amount"] or Decimal(0)
        values["amount_paid"] = values["amount_paid"] or Decimal(0)
        if not values["customer_name"]:
            raise _validation_error("customer_name", "Customer name is required.")
        self._check_dates(values["issue_date"], values["due_date"])
        totals = self._totals(items, values)

        invoice = Invoice(**values, booking_id=data.booking_id, status=InvoiceStatus.DRAFT.value)
        self._apply_totals(invoice, items, totals)
        if data.status == InvoiceStatus.ISSUED:
            invoice.status = settle_status(InvoiceStatus.ISSUED, totals.total_amount, totals.amount_paid).value
        await self._add_with_number(invoice)
        await self.session.commit()
        await self.session.refresh(invoice, ["booking"])
        return invoice

    async def update(self, invoice_id: UUID, data: InvoiceUpdate) -> Invoice:
        invoice = await self.get(invoice_id)
        if invoice.status == InvoiceStatus.CANCELLED.value:
            raise BadRequestError("A cancelled invoice can't be edited.")
        changes = data.model_dump(exclude_unset=True, exclude={"items"})
        if "currency" in changes:
            changes["currency"] = changes["currency"].upper()
        values = {name: changes.get(name, getattr(invoice, name)) for name in _EDITABLE}
        items = data.items if data.items is not None else [
            InvoiceItemIn(description=i.description, quantity=i.quantity, unit_price=i.unit_price)
            for i in invoice.items
        ]
        self._check_dates(values["issue_date"], values["due_date"])
        totals = self._totals(items, values)
        for name, value in values.items():
            setattr(invoice, name, value)
        self._apply_totals(invoice, items, totals)
        invoice.status = settle_status(invoice.status, totals.total_amount, totals.amount_paid).value
        await self.session.commit()
        return invoice

    async def set_status(self, invoice_id: UUID, new_status: InvoiceStatus) -> Invoice:
        invoice = await self.get(invoice_id)
        current = InvoiceStatus(invoice.status)
        if new_status not in ALLOWED_TRANSITIONS[current]:
            raise BadRequestError(f"A {self._label(current)} invoice can't be moved to {self._label(new_status)}.")
        if new_status == InvoiceStatus.ISSUED:
            invoice.status = settle_status(InvoiceStatus.ISSUED, invoice.total_amount, invoice.amount_paid).value
        else:
            invoice.status = new_status.value
        await self.session.commit()
        return invoice

    async def set_payment(self, invoice_id: UUID, amount_paid: Decimal | None, mark_as_paid: bool) -> Invoice:
        invoice = await self.get(invoice_id)
        status = InvoiceStatus(invoice.status)
        if status == InvoiceStatus.DRAFT:
            raise BadRequestError("Issue the invoice before recording a payment.")
        if status == InvoiceStatus.CANCELLED:
            raise BadRequestError("A cancelled invoice can't take payments.")
        paid = invoice.total_amount if mark_as_paid else money(amount_paid or Decimal(0))
        if paid > invoice.total_amount:
            raise _validation_error("amount_paid", "Amount paid cannot be greater than the invoice total.")
        invoice.amount_paid = paid
        invoice.balance_due = invoice.total_amount - paid
        invoice.status = settle_status(status, invoice.total_amount, paid).value
        await self.session.commit()
        return invoice

    async def delete(self, invoice_id: UUID) -> None:
        invoice = await self.get(invoice_id)
        if invoice.status not in (InvoiceStatus.DRAFT.value, InvoiceStatus.CANCELLED.value):
            raise ConflictError("Only draft or cancelled invoices can be deleted. Cancel this invoice instead.")
        await self.repo.delete(invoice)
        await self.session.commit()

    # --- helpers ---------------------------------------------------------------

    async def _booking(self, booking_id: UUID) -> Booking:
        booking = await self.bookings.get(booking_id)
        if booking is None:
            raise NotFoundError("Booking not found")
        return booking

    @staticmethod
    def _service_line(booking: Booking) -> str:
        d = booking.event_date
        return f"{booking.service_type} ({d.day} {_MONTHS[d.month - 1]} {d.year})"

    @staticmethod
    def _label(status: InvoiceStatus) -> str:
        return status.value.replace("_", " ").lower()

    @staticmethod
    def _check_dates(issue: date, due: date) -> None:
        if due < issue:
            raise _validation_error("due_date", "Due date can't be before the issue date.")

    @staticmethod
    def _totals(items: Sequence[InvoiceItemIn], values: dict[str, Any]) -> Totals:
        if not items:
            raise _validation_error("items", "Add at least one line item.")
        try:
            return compute_totals(
                [(i.quantity, i.unit_price) for i in items],
                values["discount_amount"],
                values["tax_rate"],
                values["amount_paid"],
            )
        except InvoiceMathError as exc:
            raise _validation_error(exc.field, exc.message) from exc

    @staticmethod
    def _apply_totals(invoice: Invoice, items: Sequence[InvoiceItemIn], totals: Totals) -> None:
        invoice.subtotal = totals.subtotal
        invoice.discount_amount = totals.discount_amount
        invoice.tax_amount = totals.tax_amount
        invoice.total_amount = totals.total_amount
        invoice.amount_paid = totals.amount_paid
        invoice.balance_due = totals.balance_due
        invoice.items = [
            InvoiceItem(
                description=item.description,
                quantity=item.quantity,
                unit_price=item.unit_price,
                line_total=line,
                display_order=index,
            )
            for index, (item, line) in enumerate(zip(items, totals.line_totals, strict=True))
        ]

    def _number_prefix(self) -> str:
        year = self.clock().astimezone(self.settings.timezone).year
        return f"{self.settings.invoice_prefix}-{year}-"

    async def _next_number(self) -> str:
        prefix = self._number_prefix()
        last = await self.repo.last_invoice_number(prefix)
        return f"{prefix}{(int(last.rsplit('-', 1)[1]) + 1 if last else 1):04d}"

    async def _add_with_number(self, invoice: Invoice) -> None:
        """Assign the next INV-YYYY-NNNN number; retry if a concurrent create took it."""
        prefix = self._number_prefix()
        for _ in range(_NUMBER_ATTEMPTS):
            last = await self.repo.last_invoice_number(prefix)
            sequence = int(last.rsplit("-", 1)[1]) + 1 if last else 1
            invoice.invoice_number = f"{prefix}{sequence:04d}"
            try:
                async with self.session.begin_nested():
                    await self.repo.add(invoice)
                return
            except IntegrityError:
                logger.info("Invoice number collision, retrying")
        raise AppError("Could not generate an invoice number. Please try again.")
