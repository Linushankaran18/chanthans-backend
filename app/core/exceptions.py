"""Domain exceptions and their FastAPI handlers. All errors render as {"detail": ...}."""
import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class AppError(Exception):
    status_code = 500
    detail = "Internal server error"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail or self.detail)
        if detail:
            self.detail = detail


class InvalidCredentialsError(AppError):
    status_code = 401
    detail = "Invalid credentials"


class InvalidTokenError(AppError):
    status_code = 401
    detail = "Invalid or expired token"


class ForbiddenError(AppError):
    status_code = 403
    detail = "Not enough permissions"


class NotFoundError(AppError):
    status_code = 404
    detail = "Resource not found"


class InvalidImageError(AppError):
    status_code = 400
    detail = "Invalid image"


class FileTooLargeError(AppError):
    status_code = 413
    detail = "File too large"


class StorageError(AppError):
    status_code = 502
    detail = "Storage service error"


class DatabaseError(AppError):
    status_code = 500
    detail = "Database error"


async def _app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=headers)


async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse({"detail": "Internal server error"}, status_code=500)


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, _unhandled_handler)
