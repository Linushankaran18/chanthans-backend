import logging
from uuid import UUID

import jwt

from app.core.exceptions import InvalidCredentialsError, InvalidTokenError
from app.core.security import (
    DUMMY_HASH,
    create_access_token,
    decode_access_token,
    verify_password,
)
from app.models.user import User
from app.repositories.user_repository import UserRepository

logger = logging.getLogger(__name__)


class AuthService:
    def __init__(self, users: UserRepository) -> None:
        self.users = users

    async def login(self, email: str, password: str) -> str:
        user = await self.users.get_by_email(email)
        # Always verify (against a dummy hash if unknown) to keep timing similar.
        valid = verify_password(password, user.password_hash if user else DUMMY_HASH)
        if user is None or not valid:
            logger.warning("Failed login attempt")
            raise InvalidCredentialsError()
        return create_access_token(str(user.id), user.role)

    async def get_user_from_token(self, token: str) -> User:
        try:
            payload = decode_access_token(token)
            user_id = UUID(payload["sub"])
        except (jwt.PyJWTError, ValueError, KeyError):
            logger.warning("Rejected invalid access token")
            raise InvalidTokenError()
        user = await self.users.get_by_id(user_id)
        if user is None:
            logger.warning("Token refers to unknown user")
            raise InvalidTokenError()
        return user
