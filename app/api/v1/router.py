from fastapi import APIRouter

from app.api.v1 import auth, hero_images

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(hero_images.public_router)
api_router.include_router(hero_images.admin_router)
