from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse

from app.dependencies.google import get_google_login_service
from app.services.google_login_service import GoogleLoginService

# Browser-facing OAuth redirects: no bearer token; the signed `state` binds the round trip.
router = APIRouter(prefix="/auth/google", tags=["auth"])

_NO_STORE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


@router.get("/enabled")
async def enabled(service: GoogleLoginService = Depends(get_google_login_service)) -> dict[str, bool]:
    return {"enabled": service.enabled}


@router.get("/login", include_in_schema=False)
async def login(service: GoogleLoginService = Depends(get_google_login_service)) -> RedirectResponse:
    url = service.authorization_url() or service.error_redirect("not_configured")
    return RedirectResponse(url, status_code=302, headers=_NO_STORE)


@router.get("/callback", include_in_schema=False)
async def callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    service: GoogleLoginService = Depends(get_google_login_service),
) -> RedirectResponse:
    token, reason = await service.handle_callback(code, state, error)
    url = service.success_redirect(token) if token else service.error_redirect(reason or "exchange_failed")
    return RedirectResponse(url, status_code=302, headers=_NO_STORE)
