from decimal import Decimal as D

import pytest

from app.models.invoice import InvoiceStatus
from app.services.invoice_calculator import InvoiceMathError, compute_totals, line_total, money, settle_status


def test_money_rounds_half_up_to_cents():
    assert money(D("2.005")) == D("2.01")
    assert money(D("2.004")) == D("2.00")
    assert money(D("0.125")) == D("0.13")


def test_line_total_is_quantity_times_price():
    assert line_total(D("2"), D("50.00")) == D("100.00")
    assert line_total(D("1.5"), D("33.33")) == D("50.00")  # 49.995 rounds half up


def test_totals_match_the_worked_example():
    totals = compute_totals(
        [(D("1"), D("2200")), (D("1"), D("450")), (D("1"), D("100")), (D("2"), D("50"))],
        discount_amount=D("100"),
        tax_rate=D("13"),
        amount_paid=D("500"),
    )
    assert totals.subtotal == D("2850.00")
    assert totals.tax_amount == D("357.50")
    assert totals.total_amount == D("3107.50")
    assert totals.balance_due == D("2607.50")


def test_tax_is_applied_after_discount():
    totals = compute_totals([(D("1"), D("1000"))], D("200"), D("10"), D("0"))
    assert totals.tax_amount == D("80.00") and totals.total_amount == D("880.00")


def test_no_tax_rate_means_no_tax():
    totals = compute_totals([(D("1"), D("100"))], D("0"), None, D("0"))
    assert totals.tax_amount == D("0.00") and totals.total_amount == D("100.00")


def test_no_floating_point_drift():
    totals = compute_totals([(D("3"), D("0.10")), (D("1"), D("0.20"))], D("0"), None, D("0"))
    assert totals.subtotal == D("0.50")


@pytest.mark.parametrize(
    ("items", "discount", "rate", "paid", "field"),
    [
        ([(D("0"), D("10"))], D("0"), None, D("0"), "items"),
        ([(D("1"), D("-1"))], D("0"), None, D("0"), "items"),
        ([(D("1"), D("100"))], D("100.01"), None, D("0"), "discount_amount"),
        ([(D("1"), D("100"))], D("-1"), None, D("0"), "discount_amount"),
        ([(D("1"), D("100"))], D("0"), None, D("-1"), "amount_paid"),
        ([(D("1"), D("100"))], D("0"), None, D("100.01"), "amount_paid"),
        ([(D("1"), D("100"))], D("0"), D("101"), D("0"), "tax_rate"),
    ],
)
def test_rules_are_enforced(items, discount, rate, paid, field):
    with pytest.raises(InvoiceMathError) as exc:
        compute_totals(items, discount, rate, paid)
    assert exc.value.field == field


@pytest.mark.parametrize(
    ("current", "total", "paid", "expected"),
    [
        (InvoiceStatus.ISSUED, D("100"), D("0"), InvoiceStatus.ISSUED),
        (InvoiceStatus.ISSUED, D("100"), D("40"), InvoiceStatus.PARTIALLY_PAID),
        (InvoiceStatus.PARTIALLY_PAID, D("100"), D("100"), InvoiceStatus.PAID),
        (InvoiceStatus.PAID, D("100"), D("20"), InvoiceStatus.PARTIALLY_PAID),
        (InvoiceStatus.PAID, D("100"), D("0"), InvoiceStatus.ISSUED),
        (InvoiceStatus.ISSUED, D("0"), D("0"), InvoiceStatus.ISSUED),
        (InvoiceStatus.DRAFT, D("100"), D("100"), InvoiceStatus.DRAFT),
        (InvoiceStatus.CANCELLED, D("100"), D("100"), InvoiceStatus.CANCELLED),
    ],
)
def test_status_follows_payments(current, total, paid, expected):
    assert settle_status(current, total, paid) == expected
