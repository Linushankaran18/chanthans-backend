from typing import Any

from app.core.config import Settings
from app.services.google_calendar_client import GoogleApiError, GoogleCalendarClient, TokenSet


class FakeGoogleClient(GoogleCalendarClient):
    """In-memory Google: records calls, never touches the network."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.events: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, ...]] = []
        self.counter = 0
        self.fail_with: Exception | None = None
        self.update_status: int | None = None  # make update_event raise GoogleApiError(status)
        self.refresh_error: Exception | None = None
        self.exchange_error: Exception | None = None
        self.exchange_refresh_token: str | None = "refresh-new"
        self.email = "studio@example.com"
        self.revoked: list[str] = []

    def _maybe_fail(self) -> None:
        if self.fail_with is not None:
            raise self.fail_with

    async def exchange_code(self, code: str) -> TokenSet:
        self.calls.append(("exchange", code))
        if self.exchange_error:
            raise self.exchange_error
        return TokenSet("access-new", 3600, self.exchange_refresh_token, "openid email calendar.events")

    async def refresh_access_token(self, refresh_token: str) -> TokenSet:
        self.calls.append(("refresh", refresh_token))
        if self.refresh_error:
            raise self.refresh_error
        return TokenSet("access-refreshed", 3600)

    async def get_user_email(self, access_token: str) -> str:
        return self.email

    async def revoke(self, token: str) -> None:
        self.revoked.append(token)

    async def create_event(self, access_token: str, calendar_id: str, event: dict[str, Any]) -> str:
        self._maybe_fail()
        self.counter += 1
        event_id = f"evt-{self.counter}"
        self.events[event_id] = event
        self.calls.append(("create", event_id))
        return event_id

    async def update_event(self, access_token: str, calendar_id: str, event_id: str, event: dict[str, Any]) -> None:
        self._maybe_fail()
        self.calls.append(("update", event_id))
        if self.update_status:
            raise GoogleApiError(self.update_status)
        if event_id not in self.events:
            raise GoogleApiError(404)
        self.events[event_id] = event

    async def delete_event(self, access_token: str, calendar_id: str, event_id: str) -> None:
        self._maybe_fail()
        self.calls.append(("delete", event_id))
        self.events.pop(event_id, None)

