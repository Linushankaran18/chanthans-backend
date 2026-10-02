from collections.abc import Sequence
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile, status

from app.dependencies.auth import require_admin
from app.dependencies.hero import get_hero_service
from app.models.hero_image import HeroImage
from app.schemas.hero_image import (
    HeroImageAdmin,
    HeroImagePublic,
    HeroImageReorderItem,
    HeroImageStatusUpdate,
    HeroImageUpdate,
)
from app.services.hero_service import HeroService

public_router = APIRouter(prefix="/hero-images", tags=["hero-images"])
admin_router = APIRouter(
    prefix="/admin/hero-images",
    tags=["admin: hero-images"],
    dependencies=[Depends(require_admin)],
)


@public_router.get("", response_model=list[HeroImagePublic])
async def list_public(service: HeroService = Depends(get_hero_service)) -> Sequence[HeroImage]:
    return await service.list_public()


@admin_router.get("", response_model=list[HeroImageAdmin])
async def list_all(service: HeroService = Depends(get_hero_service)) -> Sequence[HeroImage]:
    return await service.list_all()


@admin_router.post("", response_model=HeroImageAdmin, status_code=status.HTTP_201_CREATED)
async def create(
    image: UploadFile = File(...),
    title: str | None = Form(None, max_length=255),
    subtitle: str | None = Form(None, max_length=500),
    alt_text: str | None = Form(None, max_length=500),
    display_order: int = Form(0, ge=0),
    is_active: bool = Form(True),
    service: HeroService = Depends(get_hero_service),
) -> HeroImage:
    return await service.create(
        image,
        title=title,
        subtitle=subtitle,
        alt_text=alt_text,
        display_order=display_order,
        is_active=is_active,
    )


# Declared before the "/{image_id}" routes so "reorder" is never parsed as an id.
@admin_router.patch("/reorder", response_model=list[HeroImageAdmin])
async def reorder(
    items: list[HeroImageReorderItem],
    service: HeroService = Depends(get_hero_service),
) -> Sequence[HeroImage]:
    return await service.reorder(items)


@admin_router.put("/{image_id}", response_model=HeroImageAdmin)
async def update(
    image_id: UUID, body: HeroImageUpdate, service: HeroService = Depends(get_hero_service)
) -> HeroImage:
    return await service.update(image_id, body)


@admin_router.patch("/{image_id}/status", response_model=HeroImageAdmin)
async def set_status(
    image_id: UUID, body: HeroImageStatusUpdate, service: HeroService = Depends(get_hero_service)
) -> HeroImage:
    return await service.set_status(image_id, body.is_active)


@admin_router.delete("/{image_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete(image_id: UUID, service: HeroService = Depends(get_hero_service)) -> Response:
    await service.delete(image_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
