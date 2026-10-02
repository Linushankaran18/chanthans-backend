from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, PlainSerializer, field_validator, model_validator
from pydantic_core import PydanticCustomError

from app.models.invoice import InvoiceStatus
from app.schemas.booking import MoneyOut

Money = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)]
Quantity = Annotated[Decimal, Field(gt=0, max_digits=10, decimal_places=2)]
TaxRate = Annotated[Decimal, Field(ge=0, le=100, max_digits=7, decimal_places=4)]
# Quantities and rates are plain JSON numbers, like money, so the browser never handles Decimal strings.
NumberOut = Annotated[
    Decimal | None,
    PlainSerializer(lambda v: None if v is None else float(v), return_type=float | None, when_used="json"),
]


def _friendly(message: str) -> PydanticCustomError:
    return PydanticCustomError("value_error", message)


def _strip(value: Any) -> Any:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


class InvoiceItemIn(BaseModel):
    description: str = Field(max_length=500)
    quantity: Quantity = Decimal(1)
    unit_price: Money

    @field_validator("description", mode="before")
    @classmethod
    def _description_required(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise _friendly("Description is required.")
        return value


class _InvoiceInput(BaseModel):
    @field_validator(
        "customer_name", "customer_email", "customer_phone", "billing_address",
        "tax_name", "notes", "terms", "currency", "issue_date", "due_date",
        mode="before", check_fields=False,
    )
    @classmethod
    def _blank_to_none(cls, value: Any) -> Any:
        return _strip(value)


class InvoiceCreate(_InvoiceInput):
    """Anything omitted is pre-filled from the booking and the business defaults."""

    booking_id: UUID
    customer_name: str | None = Field(None, max_length=255)
    customer_email: EmailStr | None = None
    customer_phone: str | None = Field(None, max_length=50)
    billing_address: str | None = Field(None, max_length=1000)
    issue_date: date | None = None
    due_date: date | None = None
    currency: str | None = Field(None, min_length=3, max_length=3)
    items: list[InvoiceItemIn] | None = Field(None, max_length=100)
    discount_amount: Money | None = None
    tax_name: str | None = Field(None, max_length=50)
    tax_rate: TaxRate | None = None
    amount_paid: Money | None = None
    notes: str | None = Field(None, max_length=5000)
    terms: str | None = Field(None, max_length=5000)
    status: Literal[InvoiceStatus.DRAFT, InvoiceStatus.ISSUED] = InvoiceStatus.DRAFT


class InvoiceUpdate(_InvoiceInput):
    """Partial update. `items`, when sent, replaces the whole list."""

    customer_name: str | None = Field(None, max_length=255)
    customer_email: EmailStr | None = None
    customer_phone: str | None = Field(None, max_length=50)
    billing_address: str | None = Field(None, max_length=1000)
    issue_date: date | None = None
    due_date: date | None = None
    currency: str | None = Field(None, min_length=3, max_length=3)
    items: list[InvoiceItemIn] | None = Field(None, max_length=100)
    discount_amount: Money | None = None
    tax_name: str | None = Field(None, max_length=50)
    tax_rate: TaxRate | None = None
    amount_paid: Money | None = None
    notes: str | None = Field(None, max_length=5000)
    terms: str | None = Field(None, max_length=5000)

    @model_validator(mode="after")
    def _required(self) -> "InvoiceUpdate":
        for name, label in (
            ("customer_name", "Customer name"),
            ("issue_date", "Issue date"),
            ("due_date", "Due date"),
            ("currency", "Currency"),
            ("items", "At least one line item"),
            ("discount_amount", "Discount"),
            ("amount_paid", "Amount paid"),
        ):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise _friendly(f"{label} is required.")
        if self.items is not None and not self.items:
            raise _friendly("Add at least one line item.")
        return self


class InvoiceStatusUpdate(BaseModel):
    status: Literal[InvoiceStatus.ISSUED, InvoiceStatus.CANCELLED]


class InvoicePaymentUpdate(BaseModel):
    amount_paid: Money | None = None
    mark_as_paid: bool = False

    @model_validator(mode="after")
    def _one_of(self) -> "InvoicePaymentUpdate":
        if not self.mark_as_paid and self.amount_paid is None:
            raise _friendly("Enter the amount paid.")
        return self


class InvoiceItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    description: str
    quantity: NumberOut
    unit_price: MoneyOut
    line_total: MoneyOut
    display_order: int


class InvoiceSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    invoice_number: str
    booking_id: UUID
    booking_number: str
    customer_name: str
    issue_date: date
    due_date: date
    currency: str
    total_amount: MoneyOut
    amount_paid: MoneyOut
    balance_due: MoneyOut
    status: InvoiceStatus
    # Filled by the service. OVERDUE is derived (past due with a balance); `status` is what is stored.
    display_status: InvoiceStatus | None = None
    is_overdue: bool = False
    created_at: datetime
    updated_at: datetime


class InvoiceDetail(InvoiceSummary):
    customer_email: str | None
    customer_phone: str | None
    billing_address: str | None
    service_type: str
    event_date: date
    subtotal: MoneyOut
    discount_amount: MoneyOut
    tax_name: str | None
    tax_rate: NumberOut
    tax_amount: MoneyOut
    notes: str | None
    terms: str | None
    items: list[InvoiceItemOut]


class InvoiceDraftItem(BaseModel):
    description: str
    quantity: NumberOut
    unit_price: MoneyOut


class InvoiceDraft(BaseModel):
    """Pre-filled, unsaved invoice returned for the create form."""

    booking_id: UUID
    booking_number: str
    service_type: str
    event_date: date
    customer_name: str
    customer_email: str | None
    customer_phone: str | None
    billing_address: str | None
    issue_date: date
    due_date: date
    currency: str
    items: list[InvoiceDraftItem]
    discount_amount: MoneyOut
    tax_name: str | None
    tax_rate: NumberOut
    amount_paid: MoneyOut
    notes: str | None
    terms: str | None
    next_invoice_number: str
    existing_invoice_id: UUID | None = None


class InvoiceSettings(BaseModel):
    business_name: str
    business_logo_url: str
    business_address: str
    business_phone: str
    business_email: str
    business_website: str
    currency: str
    invoice_due_days: int
    tax_name: str | None
    tax_rate: float | None
    default_notes: str | None
    default_terms: str


class InvoicePage(BaseModel):
    items: list[InvoiceSummary]
    total: int
    page: int
    page_size: int
    pages: int
