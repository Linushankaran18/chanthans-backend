from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.database import get_session
from app.dependencies.booking import get_clock
from app.repositories.booking_repository import BookingRepository
from app.repositories.invoice_repository import InvoiceRepository
from app.services.dashboard_service import Clock
from app.services.invoice_pdf_service import InvoicePdfService
from app.services.invoice_service import InvoiceService


def get_invoice_service(
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
) -> InvoiceService:
    return InvoiceService(session, InvoiceRepository(session), BookingRepository(session), settings, clock)


def get_invoice_pdf_service(settings: Settings = Depends(get_settings)) -> InvoicePdfService:
    return InvoicePdfService(settings)
