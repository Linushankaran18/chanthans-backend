from fastapi import APIRouter, Depends

from app.dependencies.auth import get_auth_service, get_current_user
from app.models.user import User
from app.schemas.auth import LoginRequest, MeResponse, TokenResponse
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, auth: AuthService = Depends(get_auth_service)) -> TokenResponse:
    return TokenResponse(access_token=await auth.login(body.email, body.password))


@router.get("/me", response_model=MeResponse)
async def me(user: User = Depends(get_current_user)) -> User:
    return user
