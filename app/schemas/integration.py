from datetime import datetime

from pydantic import BaseModel


class GoogleCalendarStatus(BaseModel):
    configured: bool
    connected: bool
    google_email: str | None = None
    calendar_id: str | None = None
    calendar_name: str | None = None
    last_synced_at: datetime | None = None
    needs_reconnect: bool = False


class GoogleCalendarConnect(BaseModel):
    authorization_url: str


class GoogleCalendarDisconnect(BaseModel):
    connected: bool = False
