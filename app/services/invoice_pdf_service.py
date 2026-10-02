from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import unquote, urlparse

from app.core.config import Settings
from app.core.exceptions import AppError
from app.models.invoice import Invoice
from app.services.invoice_render import FONTS_DIR, build_view, load_logo, render_invoice_html, safe_filename

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RenderedInvoice:
    filename: str
    content: bytes


class PdfGenerationError(AppError):
    status_code = 500
    detail = "Unable to generate the PDF. Please try again."


def html_to_pdf(html: str) -> bytes:
    from weasyprint import HTML, URLFetcher  # imported lazily: needs the Pango system libraries

    class BundledAssetsOnly(URLFetcher):
        """Only data: URIs (the logo) and the bundled fonts may be loaded while rendering."""

        def fetch(self, url, headers=None):
            if not url.startswith("data:"):
                path = Path(unquote(urlparse(url).path)).resolve()
                if not (path.is_file() and FONTS_DIR.resolve() in path.parents):
                    raise ValueError("Resource loading is disabled while rendering invoices.")
            return super().fetch(url, headers)

    fetcher = BundledAssetsOnly(allowed_protocols={"data", "file"})
    return HTML(string=html, base_url=FONTS_DIR.as_uri(), url_fetcher=fetcher).write_pdf()


class InvoicePdfService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def render_html(self, invoice: Invoice, today: date) -> str:
        view = build_view(invoice, self.settings, today, load_logo(self.settings))
        return render_invoice_html(view)

    async def render(self, invoice: Invoice, today: date) -> RenderedInvoice:
        # Everything touching the network, the filesystem or the PDF engine runs off the event loop.
        def work() -> bytes:
            return html_to_pdf(self.render_html(invoice, today))

        try:
            content = await asyncio.to_thread(work)
        except Exception as exc:  # noqa: BLE001 - any engine/library failure maps to one friendly error
            logger.exception("PDF generation failed for invoice %s", invoice.invoice_number)
            raise PdfGenerationError() from exc
        return RenderedInvoice(safe_filename(invoice.invoice_number, invoice.customer_name), content)
