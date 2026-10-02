import uuid
from datetime import date
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import Date, ForeignKey, Integer, Numeric, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.booking import Booking
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class InvoiceStatus(StrEnum):
    DRAFT = "DRAFT"
    ISSUED = "ISSUED"
    PARTIALLY_PAID = "PARTIALLY_PAID"
    PAID = "PAID"
    # Never persisted: derived from the due date and balance (see Invoice.is_overdue).
    OVERDUE = "OVERDUE"
    CANCELLED = "CANCELLED"


PERSISTED_STATUSES = frozenset(s for s in InvoiceStatus if s != InvoiceStatus.OVERDUE)

# Statuses an invoice can be moved to by hand. Payment statuses follow amount_paid.
ALLOWED_TRANSITIONS: dict[InvoiceStatus, tuple[InvoiceStatus, ...]] = {
    InvoiceStatus.DRAFT: (InvoiceStatus.ISSUED, InvoiceStatus.CANCELLED),
    InvoiceStatus.ISSUED: (InvoiceStatus.CANCELLED,),
    InvoiceStatus.PARTIALLY_PAID: (InvoiceStatus.CANCELLED,),
    InvoiceStatus.PAID: (),
    InvoiceStatus.CANCELLED: (),
}


class Invoice(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "invoices"

    invoice_number: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    # Not unique: one invoice per booking is enforced by the service, so deposit/final
    # invoices can be allowed later without a schema change.
    booking_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("bookings.id", name="fk_invoices_booking_id_bookings", ondelete="RESTRICT"), index=True
    )
    customer_name: Mapped[str] = mapped_column(String(255))
    customer_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    customer_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    billing_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    issue_date: Mapped[date] = mapped_column(Date, index=True)
    due_date: Mapped[date] = mapped_column(Date, index=True)
    currency: Mapped[str] = mapped_column(String(3))
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    tax_name: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Percentage, e.g. 13 means 13%.
    tax_rate: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    amount_paid: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    balance_due: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    status: Mapped[str] = mapped_column(String(16), default=InvoiceStatus.DRAFT.value, index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    terms: Mapped[str | None] = mapped_column(Text, nullable=True)

    booking: Mapped[Booking] = relationship(lazy="joined", viewonly=True)
    items: Mapped[list["InvoiceItem"]] = relationship(
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="InvoiceItem.display_order",
        lazy="selectin",
    )

    @property
    def booking_number(self) -> str:
        return self.booking.booking_number

    @property
    def service_type(self) -> str:
        return self.booking.service_type

    @property
    def event_date(self) -> date:
        return self.booking.event_date

    def overdue_on(self, today: date) -> bool:
        return (
            self.status in (InvoiceStatus.ISSUED.value, InvoiceStatus.PARTIALLY_PAID.value)
            and self.balance_due > 0
            and self.due_date < today
        )


class InvoiceItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "invoice_items"

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("invoices.id", name="fk_invoice_items_invoice_id_invoices", ondelete="CASCADE"), index=True
    )
    description: Mapped[str] = mapped_column(String(500))
    quantity: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    line_total: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    display_order: Mapped[int] = mapped_column(Integer, default=0)

    invoice: Mapped[Invoice] = relationship(back_populates="items")
