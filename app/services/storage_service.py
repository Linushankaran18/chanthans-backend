"""Cloudflare R2 access through the S3-compatible API (boto3, synchronous)."""
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import Settings, get_settings
from app.core.exceptions import StorageError

logger = logging.getLogger(__name__)


class StorageService:
    """Blocking methods; call from async code via run_in_threadpool."""

    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self.bucket = settings.r2_bucket_name
        self.public_url = settings.r2_public_url
        self._settings = settings
        self._injected_client = client
        self._lazy_client: Any | None = None

    @property
    def _client(self) -> Any:
        """Created on first use so read-only routes work without R2 configured."""
        if self._injected_client is not None:
            return self._injected_client
        if self._lazy_client is None:
            settings = self._settings
            self._lazy_client = boto3.client(
                "s3",
                endpoint_url=settings.r2_endpoint,
                aws_access_key_id=settings.r2_access_key_id,
                aws_secret_access_key=settings.r2_secret_access_key,
                region_name="auto",
                config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
            )
        return self._lazy_client

    def public_url_for(self, key: str) -> str:
        return f"{self.public_url}/{key}"

    def upload_image(self, key: str, data: bytes, content_type: str) -> str:
        """Upload bytes under ``key`` and return the public URL."""
        try:
            self._client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
                CacheControl="public, max-age=31536000, immutable",
            )
        except (BotoCoreError, ClientError, ValueError):
            logger.exception("R2 upload failed for key %s", key)
            raise StorageError("Failed to store image")
        return self.public_url_for(key)

    def delete_image(self, key: str) -> None:
        try:
            self._client.delete_object(Bucket=self.bucket, Key=key)
        except (BotoCoreError, ClientError, ValueError):
            logger.exception("R2 delete failed for key %s", key)
            raise StorageError("Failed to delete image from storage")


class LocalStorageService(StorageService):
    """Development-only storage: writes files under a local folder served at /media."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.public_url = settings.local_media_url
        self.root = Path(settings.local_media_dir).resolve()

    def _path_for(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):
            raise StorageError("Invalid storage key")
        return path

    def upload_image(self, key: str, data: bytes, content_type: str) -> str:
        path = self._path_for(key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        except OSError:
            logger.exception("Local upload failed for key %s", key)
            raise StorageError("Failed to store image")
        return self.public_url_for(key)

    def delete_image(self, key: str) -> None:
        try:
            self._path_for(key).unlink(missing_ok=True)
        except OSError:
            logger.exception("Local delete failed for key %s", key)
            raise StorageError("Failed to delete image from storage")


@lru_cache
def get_storage_service() -> StorageService:
    """FastAPI dependency; override in tests."""
    settings = get_settings()
    if settings.storage_backend == "local":
        return LocalStorageService(settings)
    return StorageService(settings)
