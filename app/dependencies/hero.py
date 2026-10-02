from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.database import get_session
from app.repositories.hero_repository import HeroRepository
from app.services.hero_service import HeroService
from app.services.storage_service import StorageService, get_storage_service


def get_hero_service(
    session: AsyncSession = Depends(get_session),
    storage: StorageService = Depends(get_storage_service),
    settings: Settings = Depends(get_settings),
) -> HeroService:
    return HeroService(session, HeroRepository(session), storage, settings.max_upload_size_bytes)
