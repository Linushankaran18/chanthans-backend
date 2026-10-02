import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class GoogleCalendarIntegration(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "google_calendar_integrations"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", name="fk_google_calendar_integrations_user_id_users"), unique=True
    )
    google_email: Mapped[str] = mapped_column(String(320))
    calendar_id: Mapped[str] = mapped_column(String(255))
    # Fernet-encrypted; blank once disconnected. Never exposed through the API or logs.
    access_token_encrypted: Mapped[str] = mapped_column(Text, default="")
    refresh_token_encrypted: Mapped[str] = mapped_column(Text, default="")
    token_expiry: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scope: Mapped[str] = mapped_column(Text, default="")
    is_connected: Mapped[bool] = mapped_column(Boolean, default=False)
