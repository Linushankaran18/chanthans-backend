from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse

from app.dependencies.auth import require_admin
from app.dependencies.google import get_google_integration_service
from app.models.user import User
from app.schemas.integration import GoogleCalendarConnect, GoogleCalendarDisconnect, GoogleCalendarStatus
from app.services.google_integration_service import GoogleIntegrationService

admin_router = APIRouter(
    prefix="/admin/integrations/google-calendar",
    tags=["admin: integrations"],
    dependencies=[Depends(require_admin)],
)
# Called by Google's redirect, so no bearer token: the signed `state` authenticates the request.
public_router = APIRouter(prefix="/integrations/google-calendar", tags=["integrations"])


@admin_router.get("/status", response_model=GoogleCalendarStatus)
async def status(
    service: GoogleIntegrationService = Depends(get_google_integration_service),
) -> GoogleCalendarStatus:
    return await service.status()


@admin_router.get("/connect", response_model=GoogleCalendarConnect)
async def connect(
    user: User = Depends(require_admin),
    service: GoogleIntegrationService = Depends(get_google_integration_service),
) -> GoogleCalendarConnect:
    return GoogleCalendarConnect(authorization_url=service.authorization_url(user))


@admin_router.post("/disconnect", response_model=GoogleCalendarDisconnect)
async def disconnect(
    service: GoogleIntegrationService = Depends(get_google_integration_service),
) -> GoogleCalendarDisconnect:
    await service.disconnect()
    return GoogleCalendarDisconnect()


@public_router.get("/callback", include_in_schema=False)
async def callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    service: GoogleIntegrationService = Depends(get_google_integration_service),
) -> RedirectResponse:
    reason = await service.handle_callback(code, state, error)
    return RedirectResponse(service.redirect_url(reason), status_code=302)
