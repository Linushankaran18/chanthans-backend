"""Thin httpx wrapper around Google's OAuth2 and Calendar REST endpoints.

Kept small and dependency-injected so tests can substitute a fake. Raw Google payloads,
tokens and PII are never logged here.
"""
import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode

import httpx

from app.core.config import Settings

logger = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
CALENDAR_API = "https://www.googleapis.com/calendar/v3"
_TIMEOUT = httpx.Timeout(15.0)


class GoogleClientError(Exception):
    """Base error; messages are safe to log (no payloads)."""


class GoogleAuthError(GoogleClientError):
    """The token/refresh token was rejected (e.g. invalid_grant): user must reconnect."""


class GoogleApiError(GoogleClientError):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"Google API returned HTTP {status_code}")
        self.status_code = status_code


class GoogleNetworkError(GoogleClientError):
    pass


@dataclass(frozen=True)
class TokenSet:
    access_token: str
    expires_in: int
    refresh_token: str | None = None
    scope: str = ""


class GoogleCalendarClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def build_authorization_url(self, state: str) -> str:
        params = {
            "client_id": self.settings.google_client_id,
            "redirect_uri": self.settings.google_redirect_uri,
            "response_type": "code",
            "scope": self.settings.google_calendar_scopes,
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": state,
        }
        return f"{AUTH_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str) -> TokenSet:
        return await self._token_request(
            {
                "code": code,
                "client_id": self.settings.google_client_id,
                "client_secret": self.settings.google_client_secret,
                "redirect_uri": self.settings.google_redirect_uri,
                "grant_type": "authorization_code",
            }
        )

    async def refresh_access_token(self, refresh_token: str) -> TokenSet:
        return await self._token_request(
            {
                "refresh_token": refresh_token,
                "client_id": self.settings.google_client_id,
                "client_secret": self.settings.google_client_secret,
                "grant_type": "refresh_token",
            }
        )

    async def get_user_email(self, access_token: str) -> str:
        response = await self._request("GET", USERINFO_URL, access_token=access_token)
        email = response.json().get("email")
        if not email:
            raise GoogleApiError(response.status_code)
        return str(email)

    async def revoke(self, token: str) -> None:
        await self._request("POST", REVOKE_URL, data={"token": token})

    async def create_event(self, access_token: str, calendar_id: str, event: dict[str, Any]) -> str:
        response = await self._request(
            "POST", self._events_url(calendar_id), access_token=access_token, json=event
        )
        return str(response.json()["id"])

    async def update_event(
        self, access_token: str, calendar_id: str, event_id: str, event: dict[str, Any]
    ) -> None:
        await self._request(
            "PUT", self._events_url(calendar_id, event_id), access_token=access_token, json=event
        )

    async def delete_event(self, access_token: str, calendar_id: str, event_id: str) -> None:
        """Deleting an already-removed event (404/410) counts as success."""
        try:
            await self._request("DELETE", self._events_url(calendar_id, event_id), access_token=access_token)
        except GoogleApiError as exc:
            if exc.status_code not in (404, 410):
                raise

    # --- internals ---------------------------------------------------------

    @staticmethod
    def _events_url(calendar_id: str, event_id: str | None = None) -> str:
        url = f"{CALENDAR_API}/calendars/{quote(calendar_id, safe='')}/events"
        return f"{url}/{quote(event_id, safe='')}" if event_id else url

    async def _token_request(self, data: dict[str, str]) -> TokenSet:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
                response = await http.post(TOKEN_URL, data=data)
        except httpx.HTTPError as exc:
            raise GoogleNetworkError("Could not reach Google") from exc
        if response.status_code >= 400:
            error = _error_code(response)
            if error in ("invalid_grant", "invalid_client", "unauthorized_client"):
                raise GoogleAuthError(error)
            raise GoogleApiError(response.status_code)
        body = response.json()
        return TokenSet(
            access_token=body["access_token"],
            expires_in=int(body.get("expires_in", 3600)),
            refresh_token=body.get("refresh_token"),
            scope=body.get("scope", ""),
        )

    async def _request(
        self,
        method: str,
        url: str,
        *,
        access_token: str | None = None,
        json: dict[str, Any] | None = None,
        data: dict[str, str] | None = None,
    ) -> httpx.Response:
        headers = {"Authorization": f"Bearer {access_token}"} if access_token else None
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
                response = await http.request(method, url, headers=headers, json=json, data=data)
        except httpx.HTTPError as exc:
            raise GoogleNetworkError("Could not reach Google") from exc
        if response.status_code == 401:
            raise GoogleAuthError("unauthorized")
        if response.status_code >= 400:
            raise GoogleApiError(response.status_code)
        return response


def _error_code(response: httpx.Response) -> str:
    try:
        error = response.json().get("error", "")
    except ValueError:
        return ""
    return error if isinstance(error, str) else ""
