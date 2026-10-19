from fastapi import APIRouter, Depends, Response

from app.core.config import Settings, get_settings
from app.dependencies.booking import get_booking_service
from app.schemas.share import SharedEvent
from app.services.booking_service import BookingService
from app.services.ics import build_ics

# Public (no auth): access is by the unguessable token only.
router = APIRouter(prefix="/shared-events", tags=["shared-events"])

_NO_STORE = {"Cache-Control": "private, no-store", "X-Robots-Tag": "noindex, nofollow"}


@router.get("/{token}", response_model=SharedEvent)
async def get_shared_event(
    token: str,
    response: Response,
    service: BookingService = Depends(get_booking_service),
    settings: Settings = Depends(get_settings),
) -> SharedEvent:
    booking = await service.get_shared(token)
    response.headers.update(_NO_STORE)
    event = SharedEvent.model_validate(booking)
    event.timezone = settings.app_timezone
    return event


@router.get("/{token}/calendar.ics")
async def get_shared_event_ics(
    token: str,
    service: BookingService = Depends(get_booking_service),
    settings: Settings = Depends(get_settings),
) -> Response:
    booking = await service.get_shared(token)
    return Response(
        content=build_ics(booking, settings.app_timezone),
        media_type="text/calendar; charset=utf-8",
        headers={**_NO_STORE, "Content-Disposition": f'attachment; filename="{booking.booking_number}.ics"'},
    )
