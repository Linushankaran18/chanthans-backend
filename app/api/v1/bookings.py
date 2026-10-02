from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.exceptions import RequestValidationError

from app.dependencies.auth import require_admin
from app.dependencies.booking import get_booking_service
from app.models.booking import Booking, BookingStatus
from app.repositories.booking_repository import BookingFilters
from app.schemas.booking import (
    BookingCreate,
    BookingDetail,
    BookingPage,
    BookingStatusUpdate,
    BookingSummary,
    BookingUpdate,
)
from app.services.booking_service import BookingService

router = APIRouter(
    prefix="/admin/bookings",
    tags=["admin: bookings"],
    dependencies=[Depends(require_admin)],
)


def _parse_statuses(raw: list[str] | None) -> list[BookingStatus]:
    """Accepts repeated (?status=A&status=B) and comma-separated (?status=A,B) values."""
    statuses: list[BookingStatus] = []
    for value in raw or []:
        for part in value.split(","):
            part = part.strip().upper()
            if not part:
                continue
            try:
                statuses.append(BookingStatus(part))
            except ValueError:
                raise RequestValidationError(
                    [{"type": "enum", "loc": ("query", "status"), "msg": f"Unknown status '{part}'.", "input": part}]
                ) from None
    return statuses


@router.get("", response_model=BookingPage)
async def list_bookings(
    status_: list[str] | None = Query(None, alias="status"),
    search: str | None = Query(None, max_length=200),
    date_from: date | None = None,
    date_to: date | None = None,
    service_type: str | None = Query(None, max_length=100),
    sort: Literal["date_desc", "date_asc", "created_desc"] = "date_desc",
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    service: BookingService = Depends(get_booking_service),
) -> BookingPage:
    filters = BookingFilters(
        statuses=_parse_statuses(status_),
        search=search,
        date_from=date_from,
        date_to=date_to,
        service_type=service_type,
    )
    items, total, pages = await service.list(filters, sort, page, page_size)
    return BookingPage(
        items=[BookingSummary.model_validate(b) for b in items],
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )


# Declared before "/{booking_id}" so "service-types" is never parsed as an id.
@router.get("/service-types", response_model=list[str])
async def service_types(service: BookingService = Depends(get_booking_service)) -> list[str]:
    return await service.service_types()


@router.post("", response_model=BookingDetail, status_code=status.HTTP_201_CREATED)
async def create_booking(body: BookingCreate, service: BookingService = Depends(get_booking_service)) -> Booking:
    return await service.create(body)


@router.get("/{booking_id}", response_model=BookingDetail)
async def get_booking(booking_id: UUID, service: BookingService = Depends(get_booking_service)) -> Booking:
    return await service.get(booking_id)


@router.put("/{booking_id}", response_model=BookingDetail)
async def update_booking(
    booking_id: UUID, body: BookingUpdate, service: BookingService = Depends(get_booking_service)
) -> Booking:
    return await service.update(booking_id, body)


@router.patch("/{booking_id}/status", response_model=BookingDetail)
async def set_booking_status(
    booking_id: UUID, body: BookingStatusUpdate, service: BookingService = Depends(get_booking_service)
) -> Booking:
    return await service.set_status(booking_id, body.status)


@router.post("/{booking_id}/calendar-sync", response_model=BookingDetail)
async def sync_booking_calendar(
    booking_id: UUID, service: BookingService = Depends(get_booking_service)
) -> Booking:
    return await service.sync_calendar(booking_id)


@router.delete("/{booking_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_booking(booking_id: UUID, service: BookingService = Depends(get_booking_service)) -> Response:
    await service.delete(booking_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
