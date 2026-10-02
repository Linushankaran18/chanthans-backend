from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class HeroImagePublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    image_url: str
    title: str | None
    subtitle: str | None
    alt_text: str | None
    display_order: int


class HeroImageAdmin(HeroImagePublic):
    is_active: bool
    created_at: datetime
    updated_at: datetime


class HeroImageUpdate(BaseModel):
    """Editable metadata. Only supplied fields are changed."""

    title: str | None = Field(default=None, max_length=255)
    subtitle: str | None = Field(default=None, max_length=500)
    alt_text: str | None = Field(default=None, max_length=500)
    display_order: int | None = Field(default=None, ge=0)
    is_active: bool | None = None


class HeroImageStatusUpdate(BaseModel):
    is_active: bool


class HeroImageReorderItem(BaseModel):
    id: UUID
    display_order: int = Field(ge=0)
