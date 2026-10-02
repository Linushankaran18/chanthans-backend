from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.core.crypto import TokenCipher
from app.main import app
from app.models.google_calendar_integration import GoogleCalendarIntegration
from app.services.google_calendar_client import GoogleAuthError, GoogleNetworkError
from tests.conftest import booking_payload

BASE = "/api/v1/admin/integrations/google-calendar"
CALLBACK = "/api/v1/integrations/google-calendar/callback"
BOOKINGS = "/api/v1/admin/bookings"


async def _state(client, headers) -> str:
    url = (await client.get(f"{BASE}/connect", headers=headers)).json()["authorization_url"]
    return parse_qs(urlparse(url).query)["state"][0]


async def _integration(sessionmaker) -> GoogleCalendarIntegration | None:
    async with sessionmaker() as session:
        return (await session.scalars(select(GoogleCalendarIntegration))).first()


# --- configuration ---------------------------------------------------------------------


async def test_not_configured_reports_false_and_everything_else_works(client, admin_headers):
    unconfigured = Settings(
        _env_file=None, database_url="sqlite+aiosqlite://", jwt_secret=get_settings().jwt_secret,
        google_client_id="", google_client_secret="", google_redirect_uri="", token_encryption_key="",
    )
    app.dependency_overrides[get_settings] = lambda: unconfigured
    status = await client.get(f"{BASE}/status", headers=admin_headers)
    assert status.json() == {
        "configured": False, "connected": False, "google_email": None, "calendar_id": None,
        "calendar_name": None, "last_synced_at": None, "needs_reconnect": False,
    }
    connect = await client.get(f"{BASE}/connect", headers=admin_headers)
    assert connect.status_code == 503 and connect.json() == {"detail": "Google Calendar is not configured."}
    created = await client.post(BOOKINGS, json=booking_payload(status="CONFIRMED"), headers=admin_headers)
    assert created.status_code == 201 and created.json()["calendar_sync_status"] == "NOT_SYNCED"
    callback = await client.get(CALLBACK, params={"code": "c", "state": "s"}, follow_redirects=False)
    assert callback.status_code == 302 and "reason=not_configured" in callback.headers["location"]


async def test_invalid_encryption_key_rejected():
    with pytest.raises(ValueError):
        Settings(_env_file=None, database_url="sqlite+aiosqlite://", token_encryption_key="not-a-key")


async def test_invalid_timezone_rejected():
    with pytest.raises(ValueError):
        Settings(_env_file=None, database_url="sqlite+aiosqlite://", app_timezone="Mars/Olympus")


# --- connect / callback / status / disconnect -------------------------------------------


async def test_connect_url_contents(client, admin_headers):
    resp = await client.get(f"{BASE}/connect", headers=admin_headers)
    assert resp.status_code == 200
    parsed = urlparse(resp.json()["authorization_url"])
    q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    assert parsed.netloc == "accounts.google.com"
    assert q["client_id"] == get_settings().google_client_id
    assert q["redirect_uri"] == get_settings().google_redirect_uri
    assert q["response_type"] == "code" and q["access_type"] == "offline" and q["prompt"] == "consent"
    assert "https://www.googleapis.com/auth/calendar.events" in q["scope"].split()
    assert "client_secret" not in q
    payload = jwt.decode(q["state"], get_settings().jwt_secret, algorithms=["HS256"])
    assert payload["purpose"] == "google_calendar_connect"
    assert timedelta(0) < datetime.fromtimestamp(payload["exp"], timezone.utc) - datetime.now(timezone.utc) <= timedelta(minutes=10)


async def test_callback_success_stores_encrypted_tokens(client, admin_headers, sessionmaker, google):
    state = await _state(client, admin_headers)
    resp = await client.get(CALLBACK, params={"code": "auth-code", "state": state}, follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "http://localhost:5173/dashboard/integrations?google=connected"
    assert ("exchange", "auth-code") in google.calls
    row = await _integration(sessionmaker)
    assert row.is_connected and row.google_email == "studio@example.com" and row.calendar_id == "primary"
    assert "access-new" not in row.access_token_encrypted and "refresh-new" not in row.refresh_token_encrypted
    cipher = TokenCipher(get_settings().token_encryption_key)
    assert cipher.decrypt(row.refresh_token_encrypted) == "refresh-new"
    assert cipher.decrypt(row.access_token_encrypted) == "access-new"


async def test_callback_reconnect_updates_single_row(client, admin_headers, sessionmaker, connect_google):
    await connect_google()
    state = await _state(client, admin_headers)
    await client.get(CALLBACK, params={"code": "c", "state": state}, follow_redirects=False)
    async with sessionmaker() as session:
        assert len((await session.scalars(select(GoogleCalendarIntegration))).all()) == 1


async def test_callback_denied(client, admin_headers, sessionmaker):
    state = await _state(client, admin_headers)
    resp = await client.get(CALLBACK, params={"error": "access_denied", "state": state}, follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "http://localhost:5173/dashboard/integrations?google=error&reason=denied"
    assert await _integration(sessionmaker) is None


@pytest.mark.parametrize("state", [None, "garbage", "a.b.c"])
async def test_callback_invalid_state(client, admin_headers, sessionmaker, state):
    params = {"code": "c"} | ({"state": state} if state else {})
    resp = await client.get(CALLBACK, params=params, follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"].endswith("?google=error&reason=invalid_state")
    assert await _integration(sessionmaker) is None


async def test_callback_rejects_expired_and_wrong_purpose_state(client, admin_headers):
    me = (await client.get("/api/v1/auth/me", headers=admin_headers)).json()
    now = datetime.now(timezone.utc)
    secret = get_settings().jwt_secret
    expired = jwt.encode({"sub": me["id"], "purpose": "google_calendar_connect", "exp": now - timedelta(minutes=1)}, secret, "HS256")
    wrong = jwt.encode({"sub": me["id"], "purpose": "login", "exp": now + timedelta(minutes=5)}, secret, "HS256")
    login_token = admin_headers["Authorization"].split()[1]  # a real access token must not work as state
    for state in (expired, wrong, login_token):
        resp = await client.get(CALLBACK, params={"code": "c", "state": state}, follow_redirects=False)
        assert resp.headers["location"].endswith("reason=invalid_state")


async def test_callback_exchange_failure(client, admin_headers, google, sessionmaker):
    google.exchange_error = GoogleAuthError("invalid_grant")
    state = await _state(client, admin_headers)
    resp = await client.get(CALLBACK, params={"code": "c", "state": state}, follow_redirects=False)
    assert resp.headers["location"].endswith("?google=error&reason=exchange_failed")
    assert await _integration(sessionmaker) is None


async def test_callback_without_refresh_token_fails_on_first_connect(client, admin_headers, google, sessionmaker):
    google.exchange_refresh_token = None
    state = await _state(client, admin_headers)
    resp = await client.get(CALLBACK, params={"code": "c", "state": state}, follow_redirects=False)
    assert resp.headers["location"].endswith("reason=exchange_failed")
    assert await _integration(sessionmaker) is None


async def test_status_disconnected_then_connected_never_leaks_tokens(client, admin_headers, connect_google, make_booking):
    before = (await client.get(f"{BASE}/status", headers=admin_headers)).json()
    assert before["configured"] is True and before["connected"] is False and before["needs_reconnect"] is False
    await connect_google()
    await make_booking(status="CONFIRMED")
    resp = await client.get(f"{BASE}/status", headers=admin_headers)
    body = resp.json()
    assert set(body) == {
        "configured", "connected", "google_email", "calendar_id", "calendar_name", "last_synced_at", "needs_reconnect",
    }
    assert body["connected"] is True and body["google_email"] == "studio@example.com"
    assert body["calendar_id"] == "primary" and body["calendar_name"] == "studio@example.com"
    assert body["last_synced_at"] is not None and body["needs_reconnect"] is False
    for secret in ("access-1", "refresh-1", "token", "scope"):
        assert secret not in resp.text.replace("last_synced_at", "")


async def test_disconnect_revokes_blanks_tokens_and_keeps_event_ids(client, admin_headers, connect_google, make_booking, google, sessionmaker):
    await connect_google()
    booking = await make_booking(status="CONFIRMED")
    resp = await client.post(f"{BASE}/disconnect", headers=admin_headers)
    assert resp.status_code == 200 and resp.json() == {"connected": False}
    assert google.revoked == ["refresh-1"]
    row = await _integration(sessionmaker)
    assert row.is_connected is False and row.access_token_encrypted == "" and row.refresh_token_encrypted == ""
    status = (await client.get(f"{BASE}/status", headers=admin_headers)).json()
    assert status["connected"] is False and status["needs_reconnect"] is False
    kept = (await client.get(f"{BOOKINGS}/{booking['id']}", headers=admin_headers)).json()
    assert kept["google_calendar_event_id"] == "evt-1"
    assert (await client.post(f"{BASE}/disconnect", headers=admin_headers)).status_code == 200  # idempotent


# --- sync -----------------------------------------------------------------------------------


async def test_create_confirmed_creates_event(client, admin_headers, connect_google, google):
    await connect_google()
    resp = await client.post(BOOKINGS, json=booking_payload(status="CONFIRMED", notes="Bring props"), headers=admin_headers)
    body = resp.json()
    assert resp.status_code == 201
    assert body["calendar_sync_status"] == "SYNCED" and body["calendar_synced_at"] and body["calendar_sync_error"] is None
    assert body["google_calendar_event_id"] == "evt-1"
    event = google.events["evt-1"]
    assert event["summary"] == "Wedding — Priya Nair"
    assert event["location"] == "Toronto"
    assert event["start"] == {"dateTime": "2026-11-15T09:00:00", "timeZone": "America/Toronto"}
    assert event["end"] == {"dateTime": "2026-11-15T17:00:00", "timeZone": "America/Toronto"}
    assert event["reminders"] == {"useDefault": True}
    for part in (body["booking_number"], "(416) 555-0134", "priya@example.com", "Bring props", "Balance: 2000.00"):
        assert part in event["description"]


async def test_inquiry_is_not_synced_until_confirmed(client, admin_headers, connect_google, make_booking, google):
    await connect_google()
    booking = await make_booking()
    assert booking["calendar_sync_status"] == "NOT_SYNCED" and google.events == {}
    resp = await client.patch(f"{BOOKINGS}/{booking['id']}/status", json={"status": "CONFIRMED"}, headers=admin_headers)
    assert resp.json()["calendar_sync_status"] == "SYNCED" and len(google.events) == 1


async def test_update_updates_event_without_duplicates(client, admin_headers, connect_google, make_booking, google):
    await connect_google()
    booking = await make_booking(status="CONFIRMED")
    resp = await client.put(f"{BOOKINGS}/{booking['id']}", json={"start_time": "10:00", "location": "Oakville"}, headers=admin_headers)
    assert resp.json()["calendar_sync_status"] == "SYNCED"
    assert len(google.events) == 1
    assert ("update", "evt-1") in google.calls
    assert google.events["evt-1"]["location"] == "Oakville"
    assert google.events["evt-1"]["start"]["dateTime"] == "2026-11-15T10:00:00"


async def test_update_recreates_event_when_google_says_gone(client, admin_headers, connect_google, make_booking, google):
    await connect_google()
    booking = await make_booking(status="CONFIRMED")
    google.events.clear()  # someone deleted it in Google Calendar
    resp = await client.put(f"{BOOKINGS}/{booking['id']}", json={"notes": "n"}, headers=admin_headers)
    body = resp.json()
    assert body["calendar_sync_status"] == "SYNCED" and body["google_calendar_event_id"] == "evt-2"
    assert list(google.events) == ["evt-2"]


@pytest.mark.parametrize("gone", [404, 410])
async def test_recreate_on_404_and_410(client, admin_headers, connect_google, make_booking, google, gone):
    await connect_google()
    booking = await make_booking(status="CONFIRMED")
    google.update_status = gone
    resp = await client.put(f"{BOOKINGS}/{booking['id']}", json={"notes": "n"}, headers=admin_headers)
    assert resp.json()["google_calendar_event_id"] == "evt-2"


async def test_cancel_deletes_event_and_clears_id(client, admin_headers, connect_google, make_booking, google):
    await connect_google()
    booking = await make_booking(status="CONFIRMED")
    resp = await client.patch(f"{BOOKINGS}/{booking['id']}/status", json={"status": "CANCELLED"}, headers=admin_headers)
    body = resp.json()
    assert body["status"] == "CANCELLED" and body["google_calendar_event_id"] is None
    assert body["calendar_sync_status"] == "NOT_SYNCED" and google.events == {}


async def test_delete_booking_deletes_event(client, admin_headers, connect_google, make_booking, google):
    await connect_google()
    booking = await make_booking(status="CONFIRMED")
    assert (await client.delete(f"{BOOKINGS}/{booking['id']}", headers=admin_headers)).status_code == 204
    assert google.events == {}


async def test_delete_succeeds_even_if_google_fails(client, admin_headers, connect_google, make_booking, google):
    await connect_google()
    booking = await make_booking(status="CONFIRMED")
    google.fail_with = GoogleNetworkError("down")
    assert (await client.delete(f"{BOOKINGS}/{booking['id']}", headers=admin_headers)).status_code == 204
    assert (await client.get(f"{BOOKINGS}/{booking['id']}", headers=admin_headers)).status_code == 404


async def test_later_statuses_do_not_touch_calendar(client, admin_headers, connect_google, make_booking, google):
    await connect_google()
    booking = await make_booking(status="CONFIRMED")
    calls = len(google.calls)
    resp = await client.patch(f"{BOOKINGS}/{booking['id']}/status", json={"status": "SHOOT_COMPLETED"}, headers=admin_headers)
    assert resp.status_code == 200 and len(google.calls) == calls


async def test_google_failure_marks_failed_but_booking_operation_succeeds(client, admin_headers, connect_google, google):
    await connect_google()
    google.fail_with = GoogleNetworkError("boom with PII priya@example.com")
    resp = await client.post(BOOKINGS, json=booking_payload(status="CONFIRMED"), headers=admin_headers)
    body = resp.json()
    assert resp.status_code == 201 and body["status"] == "CONFIRMED"
    assert body["calendar_sync_status"] == "FAILED"
    assert body["calendar_sync_error"] == "Could not reach Google Calendar. Please try again."
    assert "priya" not in body["calendar_sync_error"] and body["google_calendar_event_id"] is None


async def test_generic_google_error_is_friendly(client, admin_headers, connect_google, google):
    from app.services.google_calendar_client import GoogleApiError

    await connect_google()
    google.fail_with = GoogleApiError(500)
    body = (await client.post(BOOKINGS, json=booking_payload(status="CONFIRMED"), headers=admin_headers)).json()
    assert body["calendar_sync_status"] == "FAILED"
    assert body["calendar_sync_error"] == "Google Calendar could not be updated. Please try again."


async def test_failure_on_status_change_still_changes_status(client, admin_headers, connect_google, make_booking, google):
    await connect_google()
    booking = await make_booking(status="CONFIRMED")
    google.fail_with = GoogleNetworkError("down")
    resp = await client.patch(f"{BOOKINGS}/{booking['id']}/status", json={"status": "CANCELLED"}, headers=admin_headers)
    body = resp.json()
    assert resp.status_code == 200 and body["status"] == "CANCELLED" and body["calendar_sync_status"] == "FAILED"
    assert body["google_calendar_event_id"] == "evt-1"  # kept so a retry can finish the delete


async def test_not_connected_leaves_not_synced(client, admin_headers, google):
    resp = await client.post(BOOKINGS, json=booking_payload(status="CONFIRMED"), headers=admin_headers)
    body = resp.json()
    assert resp.status_code == 201 and body["calendar_sync_status"] == "NOT_SYNCED"
    assert body["calendar_sync_error"] is None and google.events == {}


async def test_retry_after_failure_succeeds_without_duplicates(client, admin_headers, connect_google, google):
    await connect_google()
    google.fail_with = GoogleNetworkError("down")
    booking = (await client.post(BOOKINGS, json=booking_payload(status="CONFIRMED"), headers=admin_headers)).json()
    assert booking["calendar_sync_status"] == "FAILED"
    google.fail_with = None
    resp = await client.post(f"{BOOKINGS}/{booking['id']}/calendar-sync", headers=admin_headers)
    body = resp.json()
    assert resp.status_code == 200 and body["calendar_sync_status"] == "SYNCED" and body["calendar_sync_error"] is None
    again = await client.post(f"{BOOKINGS}/{booking['id']}/calendar-sync", headers=admin_headers)
    assert again.json()["google_calendar_event_id"] == body["google_calendar_event_id"] and len(google.events) == 1


async def test_manual_sync_not_connected_is_409(client, admin_headers, make_booking):
    booking = await make_booking(status="CONFIRMED")
    resp = await client.post(f"{BOOKINGS}/{booking['id']}/calendar-sync", headers=admin_headers)
    assert resp.status_code == 409 and resp.json() == {"detail": "Google Calendar is not connected."}
    missing = await client.post(f"{BOOKINGS}/00000000-0000-0000-0000-000000000000/calendar-sync", headers=admin_headers)
    assert missing.status_code == 404


async def test_manual_sync_of_cancelled_ensures_event_deleted(client, admin_headers, connect_google, make_booking, google):
    await connect_google()
    booking = await make_booking(status="CONFIRMED")
    google.fail_with = GoogleNetworkError("down")
    await client.patch(f"{BOOKINGS}/{booking['id']}/status", json={"status": "CANCELLED"}, headers=admin_headers)
    google.fail_with = None
    body = (await client.post(f"{BOOKINGS}/{booking['id']}/calendar-sync", headers=admin_headers)).json()
    assert body["calendar_sync_status"] == "NOT_SYNCED" and body["google_calendar_event_id"] is None
    assert google.events == {}


# --- tokens ---------------------------------------------------------------------------------


async def test_expired_token_is_refreshed_and_stored_encrypted(client, admin_headers, connect_google, google, sessionmaker):
    await connect_google(expired=True)
    body = (await client.post(BOOKINGS, json=booking_payload(status="CONFIRMED"), headers=admin_headers)).json()
    assert body["calendar_sync_status"] == "SYNCED"
    assert ("refresh", "refresh-1") in google.calls
    row = await _integration(sessionmaker)
    assert TokenCipher(get_settings().token_encryption_key).decrypt(row.access_token_encrypted) == "access-refreshed"
    assert row.token_expiry is not None


async def test_valid_token_is_not_refreshed(client, admin_headers, connect_google, google):
    await connect_google()
    await client.post(BOOKINGS, json=booking_payload(status="CONFIRMED"), headers=admin_headers)
    assert not [c for c in google.calls if c[0] == "refresh"]


async def test_invalid_grant_marks_needs_reconnect_and_fails_sync(client, admin_headers, connect_google, google):
    await connect_google(expired=True)
    google.refresh_error = GoogleAuthError("invalid_grant")
    body = (await client.post(BOOKINGS, json=booking_payload(status="CONFIRMED"), headers=admin_headers)).json()
    assert body["calendar_sync_status"] == "FAILED"
    assert body["calendar_sync_error"] == "Google Calendar needs to be reconnected."
    status = (await client.get(f"{BASE}/status", headers=admin_headers)).json()
    assert status["connected"] is False and status["needs_reconnect"] is True
    # Later bookings also report the reconnect problem instead of silently doing nothing.
    again = (await client.post(BOOKINGS, json=booking_payload(status="CONFIRMED", event_date="2026-12-01"), headers=admin_headers)).json()
    assert again["calendar_sync_status"] == "FAILED"
