from fastapi import APIRouter

from app.api.v1 import auth, bookings, dashboard, google_login, hero_images, integrations, invoices

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(google_login.router)
api_router.include_router(hero_images.public_router)
api_router.include_router(hero_images.admin_router)
api_router.include_router(bookings.router)
api_router.include_router(invoices.router)
api_router.include_router(dashboard.router)
api_router.include_router(integrations.admin_router)
api_router.include_router(integrations.public_router)
