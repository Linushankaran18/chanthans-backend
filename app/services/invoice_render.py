"""Turns an Invoice into the HTML that becomes the PDF. No PDF engine is imported here."""
from __future__ import annotations

import base64
import io
import logging
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape
from PIL import Image, UnidentifiedImageError

from app.core.config import Settings
from app.models.invoice import Invoice, InvoiceStatus

logger = logging.getLogger(__name__)

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
FONTS_DIR = ASSETS_DIR / "fonts"

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_SYMBOLS = {"CAD": "$", "USD": "US$", "EUR": "€", "GBP": "£", "AUD": "A$", "NZD": "NZ$"}
_LOGO_MAX_BYTES = 3 * 1024 * 1024
_LOGO_TTL_SECONDS = 3600
_logo_cache: dict[str, tuple[float, str | None]] = {}

STATUS_LABELS = {
    InvoiceStatus.DRAFT: "Draft",
    InvoiceStatus.ISSUED: "Issued",
    InvoiceStatus.PARTIALLY_PAID: "Partially paid",
    InvoiceStatus.PAID: "Paid",
    InvoiceStatus.OVERDUE: "Overdue",
    InvoiceStatus.CANCELLED: "Cancelled",
}


def format_money(amount: Decimal | int | float, currency: str) -> str:
    """$2,500.00 for CAD (the studio default), US$ / € / £ for others, 'CODE 1,234.00' otherwise."""
    value = Decimal(amount)
    sign = "-" if value < 0 else ""
    number = f"{abs(value):,.2f}"
    symbol = _SYMBOLS.get(currency)
    return f"{sign}{symbol}{number}" if symbol else f"{sign}{currency} {number}"


def format_date(value: date) -> str:
    return f"{value.day} {_MONTHS[value.month - 1]} {value.year}"


def format_quantity(value: Decimal) -> str:
    text = f"{value:f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def safe_filename(invoice_number: str, customer_name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", customer_name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^A-Za-z0-9]+", "-", ascii_name).strip("-")[:40].strip("-")
    number = re.sub(r"[^A-Za-z0-9-]+", "", invoice_number) or "invoice"
    return f"{number}-{slug}.pdf" if slug else f"{number}.pdf"


# --- logo ----------------------------------------------------------------------


def _to_data_uri(raw: bytes) -> str | None:
    """Validate a raster image and re-encode it as PNG/JPEG so a bad file never reaches the PDF."""
    try:
        with Image.open(io.BytesIO(raw)) as image:
            image.load()
            fmt = "JPEG" if image.format == "JPEG" else "PNG"
            if fmt == "PNG" and image.mode not in ("RGB", "RGBA", "L", "LA"):
                image = image.convert("RGBA")
            out = io.BytesIO()
            image.save(out, format=fmt)
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        return None
    mime = "image/jpeg" if fmt == "JPEG" else "image/png"
    return f"data:{mime};base64,{base64.b64encode(out.getvalue()).decode()}"


def _fetch_logo(url: str) -> str | None:
    try:
        with httpx.Client(timeout=5.0, follow_redirects=True) as client:
            response = client.get(url)
        response.raise_for_status()
        if len(response.content) > _LOGO_MAX_BYTES:
            logger.warning("Business logo at %s is larger than %d bytes; ignoring", url, _LOGO_MAX_BYTES)
            return None
        data_uri = _to_data_uri(response.content)
        if data_uri is None:
            logger.warning("Business logo at %s is not a usable PNG/JPEG/WebP image", url)
        return data_uri
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Could not load business logo from %s: %s", url, exc)
        return None


def load_logo(settings: Settings) -> str | None:
    """Logo as a data URI: BUSINESS_LOGO_URL, else the bundled logo, else None (name is shown)."""
    url = settings.business_logo_url.strip()
    key = url or "bundled"
    cached = _logo_cache.get(key)
    if cached and cached[0] > time.monotonic():
        return cached[1]

    data_uri = _fetch_logo(url) if url else None
    if data_uri is None:
        bundled = ASSETS_DIR / "logo.png"
        if bundled.is_file():
            data_uri = _to_data_uri(bundled.read_bytes())
    _logo_cache[key] = (time.monotonic() + _LOGO_TTL_SECONDS, data_uri)
    return data_uri


# --- view model ----------------------------------------------------------------


@dataclass(frozen=True)
class LineView:
    description: str
    quantity: str
    unit_price: str
    amount: str


@dataclass(frozen=True)
class InvoiceView:
    page_size: str
    business_name: str
    business_lines: list[str]
    logo: str | None
    footer: str
    invoice_number: str
    issue_date: str
    due_date: str
    status_label: str
    status_key: str
    watermark: str | None
    customer_name: str
    customer_lines: list[str]
    booking_number: str
    service_type: str
    event_date: str
    lines: list[LineView]
    subtotal: str
    discount: str | None
    tax_label: str | None
    tax: str | None
    total: str
    paid: str
    balance: str
    notes: str | None
    terms: str | None
    fonts_url: str


def _compact(*parts: str | None) -> list[str]:
    return [p.strip() for p in parts if p and p.strip()]


def build_view(invoice: Invoice, settings: Settings, today: date, logo: str | None) -> InvoiceView:
    cur = invoice.currency

    def money(v: Decimal) -> str:
        return format_money(v, cur)

    status = InvoiceStatus(invoice.status)
    display = InvoiceStatus.OVERDUE if invoice.overdue_on(today) else status
    tax_label = None
    if invoice.tax_amount and invoice.tax_amount > 0 or (invoice.tax_rate and invoice.tax_rate > 0):
        name = invoice.tax_name or "Tax"
        rate = format_quantity(invoice.tax_rate) if invoice.tax_rate is not None else None
        tax_label = f"{name} ({rate}%)" if rate else name

    business_lines = _compact(
        *(settings.business_address.splitlines()),
        settings.business_phone,
        settings.business_email,
        settings.business_website,
    )
    footer = "  ·  ".join(
        _compact(settings.business_name, settings.business_website, settings.business_email, settings.business_phone)
    )
    return InvoiceView(
        page_size=settings.invoice_page_size,
        business_name=settings.business_name,
        business_lines=business_lines,
        logo=logo,
        footer=footer,
        invoice_number=invoice.invoice_number,
        issue_date=format_date(invoice.issue_date),
        due_date=format_date(invoice.due_date),
        status_label=STATUS_LABELS[display],
        status_key=display.value.lower(),
        watermark={InvoiceStatus.DRAFT: "DRAFT", InvoiceStatus.CANCELLED: "CANCELLED"}.get(status),
        customer_name=invoice.customer_name,
        customer_lines=_compact(*(invoice.billing_address or "").splitlines(), invoice.customer_email, invoice.customer_phone),
        booking_number=invoice.booking_number,
        service_type=invoice.service_type,
        event_date=format_date(invoice.event_date),
        lines=[
            LineView(i.description, format_quantity(i.quantity), money(i.unit_price), money(i.line_total))
            for i in invoice.items
        ],
        subtotal=money(invoice.subtotal),
        discount=f"−{money(invoice.discount_amount)}" if invoice.discount_amount > 0 else None,
        tax_label=tax_label,
        tax=money(invoice.tax_amount) if tax_label else None,
        total=money(invoice.total_amount),
        paid=money(invoice.amount_paid),
        balance=money(invoice.balance_due),
        notes=(invoice.notes or "").strip() or None,
        terms=(invoice.terms or "").strip() or None,
        fonts_url=FONTS_DIR.as_uri(),
    )


# --- html ----------------------------------------------------------------------


def _nl2br(value: str | None) -> Markup:
    """Escape first, then turn line breaks into <br>: user text can never inject markup."""
    return Markup("<br>").join(escape(line) for line in (value or "").splitlines())


_env = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)
_env.filters["nl2br"] = _nl2br


def render_invoice_html(view: InvoiceView) -> str:
    return _env.get_template("invoice.html").render(v=view)
