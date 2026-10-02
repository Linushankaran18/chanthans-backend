from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.exceptions import RequestValidationError

from app.core.config import Settings, get_settings
from app.dependencies.auth import require_admin
from app.dependencies.invoice import get_invoice_pdf_service, get_invoice_service
from app.models.invoice import PERSISTED_STATUSES, InvoiceStatus
from app.repositories.invoice_repository import InvoiceFilters
from app.schemas.invoice import (
    InvoiceCreate,
    InvoiceDetail,
    InvoiceDraft,
    InvoicePage,
    InvoicePaymentUpdate,
    InvoiceSettings,
    InvoiceStatusUpdate,
    InvoiceSummary,
    InvoiceUpdate,
)
from app.services.invoice_pdf_service import InvoicePdfService
from app.services.invoice_service import InvoiceService, invoice_settings

router = APIRouter(
    prefix="/admin/invoices",
    tags=["admin: invoices"],
    dependencies=[Depends(require_admin)],
)


def _parse_statuses(raw: list[str] | None) -> tuple[list[InvoiceStatus], bool]:
    """Repeated or comma-separated ?status= values. OVERDUE is derived, so it becomes a flag."""
    statuses: list[InvoiceStatus] = []
    overdue = False
    for value in raw or []:
        for part in value.split(","):
            part = part.strip().upper()
            if not part:
                continue
            try:
                parsed = InvoiceStatus(part)
            except ValueError:
                raise RequestValidationError(
                    [{"type": "enum", "loc": ("query", "status"), "msg": f"Unknown status '{part}'.", "input": part}]
                ) from None
            if parsed in PERSISTED_STATUSES:
                statuses.append(parsed)
            else:
                overdue = True
    return statuses, overdue


@router.get("", response_model=InvoicePage)
async def list_invoices(
    status_: list[str] | None = Query(None, alias="status"),
    search: str | None = Query(None, max_length=200),
    date_from: date | None = None,
    date_to: date | None = None,
    overdue: bool = False,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    service: InvoiceService = Depends(get_invoice_service),
) -> InvoicePage:
    statuses, status_overdue = _parse_statuses(status_)
    filters = InvoiceFilters(
        statuses=statuses, search=search, date_from=date_from, date_to=date_to, overdue=overdue or status_overdue
    )
    items, total, pages = await service.list(filters, page, page_size)
    return InvoicePage(
        items=[service.summary(i) for i in items], total=total, page=page, page_size=page_size, pages=pages
    )


# Fixed paths are declared before "/{invoice_id}" so they are never parsed as an id.
@router.get("/settings", response_model=InvoiceSettings)
async def get_settings_for_invoices(settings: Settings = Depends(get_settings)) -> InvoiceSettings:
    return invoice_settings(settings)


@router.get("/prefill", response_model=InvoiceDraft)
async def prefill_invoice(booking_id: UUID, service: InvoiceService = Depends(get_invoice_service)) -> InvoiceDraft:
    return await service.prefill(booking_id)


@router.get("/by-booking/{booking_id}", response_model=InvoiceSummary | None)
async def invoice_for_booking(
    booking_id: UUID, service: InvoiceService = Depends(get_invoice_service)
) -> InvoiceSummary | None:
    invoice = await service.for_booking(booking_id)
    return service.summary(invoice) if invoice else None


@router.post("", response_model=InvoiceDetail, status_code=status.HTTP_201_CREATED)
async def create_invoice(body: InvoiceCreate, service: InvoiceService = Depends(get_invoice_service)) -> InvoiceDetail:
    return service.detail(await service.create(body))


@router.get("/{invoice_id}", response_model=InvoiceDetail)
async def get_invoice(invoice_id: UUID, service: InvoiceService = Depends(get_invoice_service)) -> InvoiceDetail:
    return service.detail(await service.get(invoice_id))


@router.put("/{invoice_id}", response_model=InvoiceDetail)
async def update_invoice(
    invoice_id: UUID, body: InvoiceUpdate, service: InvoiceService = Depends(get_invoice_service)
) -> InvoiceDetail:
    return service.detail(await service.update(invoice_id, body))


@router.patch("/{invoice_id}/status", response_model=InvoiceDetail)
async def set_invoice_status(
    invoice_id: UUID, body: InvoiceStatusUpdate, service: InvoiceService = Depends(get_invoice_service)
) -> InvoiceDetail:
    return service.detail(await service.set_status(invoice_id, body.status))


@router.patch("/{invoice_id}/payment", response_model=InvoiceDetail)
async def set_invoice_payment(
    invoice_id: UUID, body: InvoicePaymentUpdate, service: InvoiceService = Depends(get_invoice_service)
) -> InvoiceDetail:
    return service.detail(await service.set_payment(invoice_id, body.amount_paid, body.mark_as_paid))


@router.delete("/{invoice_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_invoice(invoice_id: UUID, service: InvoiceService = Depends(get_invoice_service)) -> Response:
    await service.delete(invoice_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{invoice_id}/pdf")
async def invoice_pdf(
    invoice_id: UUID,
    disposition: Literal["attachment", "inline"] = "attachment",
    service: InvoiceService = Depends(get_invoice_service),
    pdf: InvoicePdfService = Depends(get_invoice_pdf_service),
) -> Response:
    invoice = await service.get(invoice_id)
    rendered = await pdf.render(invoice, service.today())
    return Response(
        content=rendered.content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'{disposition}; filename="{rendered.filename}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
