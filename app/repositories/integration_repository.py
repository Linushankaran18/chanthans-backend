from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.google_calendar_integration import GoogleCalendarIntegration


class IntegrationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_current(self) -> GoogleCalendarIntegration | None:
        """The studio has a single calendar; use the most recently updated row."""
        stmt = select(GoogleCalendarIntegration).order_by(GoogleCalendarIntegration.updated_at.desc()).limit(1)
        return (await self.session.scalars(stmt)).first()

    async def get_for_user(self, user_id: UUID) -> GoogleCalendarIntegration | None:
        stmt = select(GoogleCalendarIntegration).where(GoogleCalendarIntegration.user_id == user_id)
        return (await self.session.scalars(stmt)).first()

    async def add(self, integration: GoogleCalendarIntegration) -> GoogleCalendarIntegration:
        self.session.add(integration)
        await self.session.flush()
        return integration
