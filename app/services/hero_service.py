import logging
from collections.abc import Sequence
from uuid import UUID, uuid4

from fastapi import UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import DatabaseError, InvalidImageError, NotFoundError
from app.models.hero_image import HeroImage
from app.repositories.hero_repository import HeroRepository
from app.schemas.hero_image import HeroImageReorderItem, HeroImageUpdate
from app.services.image_validation import validate_upload
from app.services.storage_service import StorageService

logger = logging.getLogger(__name__)


class HeroService:
    def __init__(
        self,
        session: AsyncSession,
        repo: HeroRepository,
        storage: StorageService,
        max_upload_bytes: int,
    ) -> None:
        self.session = session
        self.repo = repo
        self.storage = storage
        self.max_upload_bytes = max_upload_bytes

    async def list_public(self) -> Sequence[HeroImage]:
        return await self.repo.list(active_only=True)

    async def list_all(self) -> Sequence[HeroImage]:
        return await self.repo.list()

    async def _get_or_404(self, image_id: UUID) -> HeroImage:
        image = await self.repo.get(image_id)
        if image is None:
            raise NotFoundError("Hero image not found")
        return image

    async def _commit(self) -> None:
        try:
            await self.session.commit()
        except SQLAlchemyError:
            logger.exception("Database commit failed")
            await self.session.rollback()
            raise DatabaseError()

    async def create(
        self,
        file: UploadFile,
        *,
        title: str | None,
        subtitle: str | None,
        alt_text: str | None,
        display_order: int,
        is_active: bool,
    ) -> HeroImage:
        try:
            validated = await validate_upload(file, self.max_upload_bytes)
        except InvalidImageError:
            logger.warning("Rejected hero image upload: invalid image")
            raise
        key = f"hero/{uuid4()}.{validated.extension}"
        url = await run_in_threadpool(
            self.storage.upload_image, key, validated.data, validated.content_type
        )
        try:
            image = await self.repo.add(
                HeroImage(
                    image_key=key,
                    image_url=url,
                    title=title,
                    subtitle=subtitle,
                    alt_text=alt_text,
                    display_order=display_order,
                    is_active=is_active,
                )
            )
            await self.session.commit()
        except SQLAlchemyError:
            logger.exception("Database save failed after upload; removing %s", key)
            await self.session.rollback()
            await self._cleanup_upload(key)
            raise DatabaseError()
        return image

    async def _cleanup_upload(self, key: str) -> None:
        try:
            await run_in_threadpool(self.storage.delete_image, key)
        except Exception:  # best effort
            logger.error("Could not remove orphaned object %s", key)

    async def update(self, image_id: UUID, data: HeroImageUpdate) -> HeroImage:
        image = await self._get_or_404(image_id)
        changes = data.model_dump(exclude_unset=True)
        # display_order / is_active are NOT NULL columns; ignore explicit nulls.
        for field in ("display_order", "is_active"):
            if changes.get(field, 0) is None:
                changes.pop(field)
        for field, value in changes.items():
            setattr(image, field, value)
        await self._commit()
        return image

    async def set_status(self, image_id: UUID, is_active: bool) -> HeroImage:
        image = await self._get_or_404(image_id)
        image.is_active = is_active
        await self._commit()
        return image

    async def reorder(self, items: Sequence[HeroImageReorderItem]) -> Sequence[HeroImage]:
        ids = [item.id for item in items]
        found = await self.repo.get_many(ids)
        missing = set(ids) - set(found)
        if missing:
            raise NotFoundError("One or more hero images were not found")
        for item in items:
            found[item.id].display_order = item.display_order
        await self._commit()
        return await self.repo.list()

    async def delete(self, image_id: UUID) -> None:
        image = await self._get_or_404(image_id)
        # Storage first: if it fails, the DB row stays so nothing is orphaned silently.
        await run_in_threadpool(self.storage.delete_image, image.image_key)
        try:
            await self.repo.delete(image)
        except SQLAlchemyError:
            logger.exception("DB delete failed after R2 delete for %s", image.image_key)
            await self.session.rollback()
            raise DatabaseError()
        await self._commit()
