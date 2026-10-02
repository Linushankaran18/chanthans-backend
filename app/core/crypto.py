"""Symmetric encryption for secrets stored at rest (Google OAuth tokens)."""
from cryptography.fernet import Fernet, InvalidToken

from app.core.exceptions import AppError


class TokenCipherError(AppError):
    status_code = 500
    detail = "Stored credentials could not be read"


class TokenCipher:
    def __init__(self, key: str) -> None:
        self._fernet = Fernet(key.encode())

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        try:
            return self._fernet.decrypt(value.encode()).decode()
        except (InvalidToken, ValueError) as exc:
            raise TokenCipherError() from exc
