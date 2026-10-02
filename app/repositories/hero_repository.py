from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.hero_image import HeroImage


class HeroRepository:
    """Data access only; transactions are committed by the service layer."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list(self, active_only: bool = False) -> Sequence[HeroImage]:
        stmt = select(HeroImage).order_by(HeroImage.display_order.asc(), HeroImage.created_at.asc())
        if active_only:
            stmt = stmt.where(HeroImage.is_active.is_(True))
        return (await self.session.execute(stmt)).scalars().all()

    async def get(self, image_id: UUID) -> HeroImage | None:
        return await self.session.get(HeroImage, image_id)

    async def get_many(self, ids: Sequence[UUID]) -> dict[UUID, HeroImage]:
        result = await self.session.execute(select(HeroImage).where(HeroImage.id.in_(ids)))
        return {h.id: h for h in result.scalars().all()}

    async def add(self, image: HeroImage) -> HeroImage:
        self.session.add(image)
        await self.session.flush()
        return image

    async def delete(self, image: HeroImage) -> None:
        await self.session.delete(image)
        await self.session.flush()
