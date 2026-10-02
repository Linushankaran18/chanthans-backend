from fastapi import APIRouter, Depends

from app.dependencies.auth import get_auth_service
from app.schemas.auth import LoginRequest, TokenResponse
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, auth: AuthService = Depends(get_auth_service)) -> TokenResponse:
    return TokenResponse(access_token=await auth.login(body.email, body.password))
