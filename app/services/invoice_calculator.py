"""Invoice arithmetic. Decimal only; the backend is the single source of truth for totals."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from app.models.invoice import InvoiceStatus

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


class InvoiceMathError(ValueError):
    """A calculation rule was violated. `field` names the offending input for friendly errors."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field
        self.message = message


def money(value: Decimal | int | str) -> Decimal:
    """Round to whole cents, half up (the convention accountants expect)."""
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Totals:
    line_totals: list[Decimal]
    subtotal: Decimal
    discount_amount: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    amount_paid: Decimal
    balance_due: Decimal


def line_total(quantity: Decimal, unit_price: Decimal) -> Decimal:
    return money(quantity * unit_price)


def compute_totals(
    items: Sequence[tuple[Decimal, Decimal]],
    discount_amount: Decimal,
    tax_rate: Decimal | None,
    amount_paid: Decimal,
) -> Totals:
    """items: (quantity, unit_price) pairs. `tax_rate` is a percentage (13 = 13%)."""
    for quantity, unit_price in items:
        if quantity <= 0:
            raise InvoiceMathError("items", "Quantity must be greater than zero.")
        if unit_price < 0:
            raise InvoiceMathError("items", "Unit price cannot be negative.")
    if discount_amount < 0:
        raise InvoiceMathError("discount_amount", "Discount cannot be negative.")
    if amount_paid < 0:
        raise InvoiceMathError("amount_paid", "Amount paid cannot be negative.")
    if tax_rate is not None and not (0 <= tax_rate <= 100):
        raise InvoiceMathError("tax_rate", "Tax rate must be between 0 and 100.")

    lines = [line_total(q, p) for q, p in items]
    subtotal = sum(lines, ZERO)
    discount = money(discount_amount)
    if discount > subtotal:
        raise InvoiceMathError("discount_amount", "Discount cannot be greater than the subtotal.")
    discounted = subtotal - discount
    tax = money(discounted * (tax_rate or Decimal(0)) / Decimal(100))
    total = discounted + tax
    paid = money(amount_paid)
    if paid > total:
        raise InvoiceMathError("amount_paid", "Amount paid cannot be greater than the invoice total.")
    return Totals(
        line_totals=lines,
        subtotal=subtotal,
        discount_amount=discount,
        tax_amount=tax,
        total_amount=total,
        amount_paid=paid,
        balance_due=total - paid,
    )


def settle_status(current: InvoiceStatus | str, total: Decimal, paid: Decimal) -> InvoiceStatus:
    """Payment status follows amount_paid. Drafts and cancelled invoices are left alone."""
    current = InvoiceStatus(current)
    if current in (InvoiceStatus.DRAFT, InvoiceStatus.CANCELLED):
        return current
    if total > 0 and paid >= total:
        return InvoiceStatus.PAID
    if paid > 0:
        return InvoiceStatus.PARTIALLY_PAID
    return InvoiceStatus.ISSUED
