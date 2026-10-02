import re
from datetime import date, timedelta
from decimal import Decimal as D

import httpx
import pytest

from app.core.config import get_settings
from app.models.booking import Booking
from app.models.invoice import Invoice, InvoiceItem
from app.services import invoice_pdf_service, invoice_render
from app.services.invoice_render import (
    build_view,
    format_money,
    load_logo,
    render_invoice_html,
    safe_filename,
)

URL = "/api/v1/admin/invoices"


def _pdf_engine_available() -> bool:
    try:
        return invoice_pdf_service.html_to_pdf("<p>x</p>").startswith(b"%PDF")
    except Exception:  # missing Pango/GObject system libraries
        return False


needs_pdf_engine = pytest.mark.skipif(not _pdf_engine_available(), reason="WeasyPrint system libraries not installed")


@pytest.fixture(autouse=True)
def _fresh_logo_cache():
    invoice_render._logo_cache.clear()
    yield
    invoice_render._logo_cache.clear()


def make_invoice(**overrides) -> Invoice:
    invoice = Invoice(
        invoice_number="INV-2026-0001",
        customer_name="Priya Nair",
        customer_email="priya@example.com",
        customer_phone="(416) 555-0134",
        billing_address="48 Maple Avenue\nMississauga, ON",
        issue_date=date(2026, 10, 2),
        due_date=date(2026, 10, 16),
        currency="CAD",
        subtotal=D("2850"),
        discount_amount=D("100"),
        tax_name="HST",
        tax_rate=D("13"),
        tax_amount=D("357.50"),
        total_amount=D("3107.50"),
        amount_paid=D("500"),
        balance_due=D("2607.50"),
        status="ISSUED",
        notes="Thank you!",
        terms="Due in 14 days.",
    )
    invoice.booking = Booking(booking_number="BK-2026-0012", service_type="Wedding", event_date=date(2026, 11, 15))
    invoice.items = [
        InvoiceItem(description="Wedding Package", quantity=D("1"), unit_price=D("2200"), line_total=D("2200"), display_order=0),
        InvoiceItem(description="Extra editing", quantity=D("2.5"), unit_price=D("50"), line_total=D("125"), display_order=1),
    ]
    for key, value in overrides.items():
        setattr(invoice, key, value)
    return invoice


def html_for(invoice: Invoice, today: date = date(2026, 10, 2), settings=None) -> str:
    settings = settings or get_settings()
    return render_invoice_html(build_view(invoice, settings, today, load_logo(settings)))


# --- formatting ---------------------------------------------------------------


def test_money_format_is_consistent():
    assert format_money(D("2500"), "CAD") == "$2,500.00"
    assert format_money(D("0.5"), "CAD") == "$0.50"
    assert format_money(D("1234567.891"), "CAD") == "$1,234,567.89"
    assert format_money(D("10"), "USD") == "US$10.00"
    assert format_money(D("10"), "CHF") == "CHF 10.00"


@pytest.mark.parametrize(
    ("number", "name", "expected"),
    [
        ("INV-2026-0001", "John Silva", "INV-2026-0001-John-Silva.pdf"),
        ("INV-2026-0001", "José Müller", "INV-2026-0001-Jose-Muller.pdf"),
        ("INV-2026-0001", '../../etc/passwd"; rm -rf', "INV-2026-0001-etc-passwd-rm-rf.pdf"),
        ("INV-2026-0001", "李雷", "INV-2026-0001.pdf"),
        ("INV-2026-0001", "x" * 200, "INV-2026-0001-" + "x" * 40 + ".pdf"),
    ],
)
def test_safe_filename(number, name, expected):
    assert safe_filename(number, name) == expected
    assert re.fullmatch(r"[A-Za-z0-9.\-]+", expected)


# --- html ---------------------------------------------------------------------


def test_html_contains_the_invoice_content():
    html = html_for(make_invoice())
    for text in ("INV-2026-0001", "2 Oct 2026", "16 Oct 2026", "Priya Nair", "48 Maple Avenue", "BK-2026-0012",
                 "15 Nov 2026", "Wedding Package", "$2,200.00", "2.5", "$125.00", "$2,850.00",
                 "HST (13%)", "$357.50", "$3,107.50", "$500.00", "$2,607.50", "Thank you!", "Due in 14 days."):
        assert text in html, text
    assert "−$100.00" in html  # discount shown as a deduction
    assert "pill-issued" in html and "data:image/png;base64," in html


def test_user_text_is_escaped_and_cannot_inject_html():
    evil = '<script>alert(1)</script><img src="http://evil.test/x.png">'
    html = html_for(make_invoice(customer_name=evil, notes=evil, terms=evil, billing_address=evil))
    assert "<script>" not in html and "evil.test/x.png\">" not in html
    assert "&lt;script&gt;" in html
    # line breaks in notes survive as <br> without allowing markup
    assert "<br>" in html_for(make_invoice(notes="one\ntwo"))


def test_optional_sections_are_omitted():
    invoice = make_invoice(discount_amount=D("0"), tax_name=None, tax_rate=None, tax_amount=D("0"), notes=None, terms=None)
    html = html_for(invoice)
    assert "Discount" not in html and "HST" not in html and "Payment terms" not in html


def test_watermarks_and_derived_overdue_label():
    assert 'class="watermark"' in html_for(make_invoice(status="DRAFT"))
    assert ">CANCELLED<" in html_for(make_invoice(status="CANCELLED"))
    assert 'class="watermark"' not in html_for(make_invoice(status="ISSUED"))
    overdue = html_for(make_invoice(), today=date(2026, 10, 2) + timedelta(days=30))
    assert "pill-overdue" in overdue and ">Overdue<" in overdue
    assert "pill-paid" in html_for(make_invoice(status="PAID", balance_due=D("0")), today=date(2027, 1, 1))


def test_rendering_never_references_remote_resources():
    html = html_for(make_invoice())
    assert not re.search(r"""(src|href)=["']https?://""", html)
    assert "url(\"http" not in html


# --- logo ---------------------------------------------------------------------


def _png_bytes() -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGBA", (4, 4), (10, 20, 30, 255)).save(buf, format="PNG")
    return buf.getvalue()


def _mock_http(monkeypatch, handler):
    real = httpx.Client
    monkeypatch.setattr(invoice_render.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


def _with_logo_url(url: str):
    return get_settings().model_copy(update={"business_logo_url": url})


def test_bundled_logo_is_used_by_default():
    assert load_logo(get_settings()).startswith("data:image/png;base64,")


def test_configured_logo_is_fetched_and_embedded(monkeypatch):
    png = _png_bytes()
    _mock_http(monkeypatch, lambda request: httpx.Response(200, content=png))
    uri = load_logo(_with_logo_url("https://media.test/logo.png"))
    assert uri is not None and uri.startswith("data:image/png;base64,")
    import base64

    assert base64.b64decode(uri.split(",", 1)[1])  # valid payload, not a remote reference


@pytest.mark.parametrize(
    "handler",
    [
        lambda request: httpx.Response(404),
        lambda request: httpx.Response(200, content=b"<html>not an image</html>"),
        lambda request: httpx.Response(200, content=b"x" * (3 * 1024 * 1024 + 1)),
        lambda request: (_ for _ in ()).throw(httpx.ConnectError("boom")),
    ],
)
def test_unusable_logo_falls_back_to_the_bundled_logo(monkeypatch, handler):
    _mock_http(monkeypatch, handler)
    assert load_logo(_with_logo_url("https://media.test/missing.png")).startswith("data:image/png;base64,")


def test_without_any_logo_the_business_name_is_shown(monkeypatch):
    monkeypatch.setattr(invoice_render, "ASSETS_DIR", invoice_render.ASSETS_DIR / "does-not-exist")
    html = html_for(make_invoice())
    assert "<img" not in html and "Chanthans" in html


# --- endpoint -----------------------------------------------------------------


async def _make_invoice(client, headers, make_booking, **payload) -> dict:
    booking = await make_booking()
    resp = await client.post(URL, json={"booking_id": booking["id"], "status": "ISSUED", **payload}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


@needs_pdf_engine
async def test_pdf_endpoint_returns_a_real_pdf(client, admin_headers, make_booking):
    invoice = await _make_invoice(client, admin_headers, make_booking)
    resp = await client.get(f"{URL}/{invoice['id']}/pdf", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.headers["content-disposition"] == f'attachment; filename="{invoice["invoice_number"]}-Priya-Nair.pdf"'
    assert resp.headers["cache-control"] == "private, no-store"
    assert resp.content.startswith(b"%PDF") and len(resp.content) > 5_000


@needs_pdf_engine
async def test_pdf_inline_disposition(client, admin_headers, make_booking):
    invoice = await _make_invoice(client, admin_headers, make_booking)
    resp = await client.get(f"{URL}/{invoice['id']}/pdf", params={"disposition": "inline"}, headers=admin_headers)
    assert resp.headers["content-disposition"].startswith("inline; filename=")
    bad = await client.get(f"{URL}/{invoice['id']}/pdf", params={"disposition": "../x"}, headers=admin_headers)
    assert bad.status_code == 422


@needs_pdf_engine
async def test_long_invoices_paginate_without_failing(client, admin_headers, make_booking):
    rows = [{"description": f"Line item number {i} with a reasonably descriptive title", "quantity": 1, "unit_price": 10 + i} for i in range(60)]
    invoice = await _make_invoice(client, admin_headers, make_booking, items=rows, amount_paid=0)
    resp = await client.get(f"{URL}/{invoice['id']}/pdf", headers=admin_headers)
    assert resp.status_code == 200 and resp.content.startswith(b"%PDF")


@needs_pdf_engine
def test_long_invoices_flow_onto_extra_pages_with_repeated_header():
    from weasyprint import HTML

    invoice = make_invoice()
    invoice.items = [
        InvoiceItem(description=f"Line {i}", quantity=D("1"), unit_price=D("10"), line_total=D("10"), display_order=i)
        for i in range(60)
    ]
    document = HTML(string=html_for(invoice), base_url=invoice_render.FONTS_DIR.as_uri()).render()
    assert len(document.pages) >= 2


@needs_pdf_engine
async def test_pdf_still_renders_when_the_configured_logo_is_unreachable(client, admin_headers, make_booking, monkeypatch):
    from app.main import app

    _mock_http(monkeypatch, lambda request: httpx.Response(500))
    app.dependency_overrides[get_settings] = lambda: _with_logo_url("https://media.test/gone.png")
    invoice = await _make_invoice(client, admin_headers, make_booking)
    resp = await client.get(f"{URL}/{invoice['id']}/pdf", headers=admin_headers)
    assert resp.status_code == 200 and resp.content.startswith(b"%PDF")


async def test_pdf_failure_is_a_friendly_error_without_internals(client, admin_headers, make_booking, monkeypatch):
    invoice = await _make_invoice(client, admin_headers, make_booking)

    def boom(html: str) -> bytes:
        raise OSError("cannot load library '/usr/lib/libpango.so': secret path")

    monkeypatch.setattr(invoice_pdf_service, "html_to_pdf", boom)
    resp = await client.get(f"{URL}/{invoice['id']}/pdf", headers=admin_headers)
    assert resp.status_code == 500
    assert resp.json() == {"detail": "Unable to generate the PDF. Please try again."}


async def test_pdf_unknown_invoice_is_404(client, admin_headers):
    resp = await client.get(f"{URL}/11111111-1111-1111-1111-111111111111/pdf", headers=admin_headers)
    assert resp.status_code == 404


@needs_pdf_engine
def test_renderer_cannot_load_arbitrary_files_or_urls():
    html = '<html><body><img src="file:///etc/hosts"><img src="http://127.0.0.1:9/x.png"><p>ok</p></body></html>'
    assert invoice_pdf_service.html_to_pdf(html).startswith(b"%PDF")  # blocked resources are skipped, not fatal
