from datetime import date, timedelta
from zoneinfo import ZoneInfo
from datetime import datetime

import pytest
from sqlalchemy import select

from app.models.booking import Booking
from tests.conftest import booking_payload

URL = "/api/v1/admin/bookings"
INVOICES = "/api/v1/admin/invoices"
SHARE_URL = "/api/v1/shared-events"


def _today() -> date:
    return datetime.now(ZoneInfo("America/Toronto")).date()


# --- overlap override -----------------------------------------------------------


async def _confirmed(make_booking, **kw):
    return await make_booking(status="CONFIRMED", start_time="09:00", end_time="12:00", **kw)


async def test_create_confirmed_with_allow_overlap(client, admin_headers, make_booking):
    await _confirmed(make_booking)
    blocked = await client.post(URL, json=booking_payload(status="CONFIRMED", start_time="10:00", end_time="11:00"), headers=admin_headers)
    assert blocked.status_code == 409 and blocked.json()["code"] == "booking_conflict"
    ok = await client.post(
        URL,
        json=booking_payload(status="CONFIRMED", start_time="10:00", end_time="11:00", allow_overlap=True),
        headers=admin_headers,
    )
    assert ok.status_code == 201 and ok.json()["status"] == "CONFIRMED"
    assert "allow_overlap" not in ok.json()


async def test_confirm_transition_with_allow_overlap(client, admin_headers, make_booking):
    await _confirmed(make_booking)
    other = await make_booking(start_time="10:00", end_time="11:00")
    blocked = await client.patch(f"{URL}/{other['id']}/status", json={"status": "CONFIRMED"}, headers=admin_headers)
    assert blocked.status_code == 409
    ok = await client.patch(
        f"{URL}/{other['id']}/status", json={"status": "CONFIRMED", "allow_overlap": True}, headers=admin_headers
    )
    assert ok.status_code == 200 and ok.json()["status"] == "CONFIRMED"


async def test_update_confirmed_with_allow_overlap(client, admin_headers, make_booking):
    await _confirmed(make_booking)
    later = await make_booking(status="CONFIRMED", start_time="13:00", end_time="14:00")
    blocked = await client.put(f"{URL}/{later['id']}", json={"start_time": "11:00", "end_time": "13:00"}, headers=admin_headers)
    assert blocked.status_code == 409
    ok = await client.put(
        f"{URL}/{later['id']}",
        json={"start_time": "11:00", "end_time": "13:00", "allow_overlap": True},
        headers=admin_headers,
    )
    assert ok.status_code == 200 and ok.json()["start_time"] == "11:00:00"


async def test_unconfirmed_bookings_never_conflict(client, admin_headers, make_booking):
    await _confirmed(make_booking)
    for status in ("INQUIRY", "PENDING"):
        resp = await client.post(URL, json=booking_payload(status=status, start_time="10:00", end_time="11:00"), headers=admin_headers)
        assert resp.status_code == 201


# --- delete booking (+ invoice) -------------------------------------------------


async def _invoice(client, headers, booking, **kw):
    resp = await client.post(INVOICES, json={"booking_id": booking["id"], **kw}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_delete_booking_with_draft_invoice_requires_flag(client, admin_headers, make_booking):
    booking = await make_booking()
    invoice = await _invoice(client, admin_headers, booking)
    blocked = await client.delete(f"{URL}/{booking['id']}", headers=admin_headers)
    assert blocked.status_code == 409 and blocked.json()["code"] == "booking_has_invoice"
    ok = await client.delete(f"{URL}/{booking['id']}?delete_invoice=true", headers=admin_headers)
    assert ok.status_code == 204
    assert (await client.get(f"{URL}/{booking['id']}", headers=admin_headers)).status_code == 404
    assert (await client.get(f"{INVOICES}/{invoice['id']}", headers=admin_headers)).status_code == 404


async def test_delete_booking_never_removes_an_issued_invoice(client, admin_headers, make_booking):
    booking = await make_booking()
    invoice = await _invoice(client, admin_headers, booking, status="ISSUED")
    resp = await client.delete(f"{URL}/{booking['id']}?delete_invoice=true", headers=admin_headers)
    assert resp.status_code == 409 and resp.json()["code"] == "booking_has_invoice"
    assert (await client.get(f"{URL}/{booking['id']}", headers=admin_headers)).status_code == 200
    assert (await client.get(f"{INVOICES}/{invoice['id']}", headers=admin_headers)).status_code == 200


async def test_delete_booking_without_invoice_unchanged(client, admin_headers, make_booking):
    booking = await make_booking()
    assert (await client.delete(f"{URL}/{booking['id']}", headers=admin_headers)).status_code == 204


# --- team share link ------------------------------------------------------------


async def _share(client, headers, booking) -> str:
    resp = await client.post(f"{URL}/{booking['id']}/share", headers=headers)
    assert resp.status_code == 200
    url = resp.json()["url"]
    assert url.startswith("http://localhost:5173/share/")
    return url.rsplit("/", 1)[1]


async def test_share_requires_admin(client, make_booking):
    booking = await make_booking()
    for method in ("post", "get", "delete"):
        assert (await getattr(client, method)(f"{URL}/{booking['id']}/share")).status_code in (401, 403)


async def test_share_is_idempotent_and_flagged(client, admin_headers, make_booking):
    booking = await make_booking()
    assert booking["is_shared"] is False
    first = await _share(client, admin_headers, booking)
    assert await _share(client, admin_headers, booking) == first
    got = (await client.get(f"{URL}/{booking['id']}", headers=admin_headers)).json()
    assert got["is_shared"] is True and "share_token" not in got
    assert (await client.get(f"{URL}/{booking['id']}/share", headers=admin_headers)).json()["url"].endswith(first)


async def test_public_event_hides_sensitive_data(client, admin_headers, make_booking):
    booking = await make_booking(notes="gate code 1234")
    token = await _share(client, admin_headers, booking)
    resp = await client.get(f"{SHARE_URL}/{token}")  # no auth header
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "service_type": "Wedding",
        "customer_name": "Priya Nair",
        "event_date": "2026-11-15",
        "start_time": "09:00:00",
        "end_time": "17:00:00",
        "location": "Toronto",
        "status": "INQUIRY",
        "timezone": "America/Toronto",
    }
    text = resp.text
    for secret in ("555", "priya@example.com", "2500", "500", "gate code"):
        assert secret not in text
    assert "no-store" in resp.headers["cache-control"] and "noindex" in resp.headers["x-robots-tag"]


async def test_ics_download(client, admin_headers, make_booking):
    booking = await make_booking(location="Venue, Hall; Toronto")
    token = await _share(client, admin_headers, booking)
    resp = await client.get(f"{SHARE_URL}/{token}/calendar.ics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/calendar")
    assert ".ics" in resp.headers["content-disposition"]
    ics = resp.text
    assert "BEGIN:VCALENDAR" in ics and "END:VCALENDAR" in ics
    assert "DTSTART;TZID=America/Toronto:20261115T090000" in ics
    assert "DTEND;TZID=America/Toronto:20261115T170000" in ics
    assert r"LOCATION:Venue\, Hall\; Toronto" in ics
    for secret in ("555", "priya@example.com", "2500"):
        assert secret not in ics
    assert all(len(line.encode()) <= 75 for line in ics.split("\r\n"))


async def test_revoked_unknown_links_are_404(client, admin_headers, make_booking):
    booking = await make_booking()
    token = await _share(client, admin_headers, booking)
    assert (await client.delete(f"{URL}/{booking['id']}/share", headers=admin_headers)).status_code == 204
    assert (await client.get(f"{SHARE_URL}/{token}")).status_code == 404
    assert (await client.get(f"{SHARE_URL}/{token}/calendar.ics")).status_code == 404
    assert (await client.get(f"{SHARE_URL}/not-a-real-token")).status_code == 404
    assert (await client.get(f"{URL}/{booking['id']}/share", headers=admin_headers)).json() is None
    # Re-enabling issues a fresh token; the old one stays dead.
    assert await _share(client, admin_headers, booking) != token
    assert (await client.get(f"{SHARE_URL}/{token}")).status_code == 404


@pytest.mark.parametrize(("days_ago", "alive"), [(0, True), (1, True), (2, False), (-5, True)])
async def test_link_expires_after_the_event(client, admin_headers, make_booking, days_ago, alive):
    event_day = _today() - timedelta(days=days_ago)
    booking = await make_booking(event_date=event_day.isoformat())
    token = await _share(client, admin_headers, booking)
    assert (await client.get(f"{SHARE_URL}/{token}")).status_code == (200 if alive else 404)


async def test_deleting_booking_kills_link(client, admin_headers, make_booking):
    booking = await make_booking()
    token = await _share(client, admin_headers, booking)
    await client.delete(f"{URL}/{booking['id']}", headers=admin_headers)
    assert (await client.get(f"{SHARE_URL}/{token}")).status_code == 404


async def test_share_url_uses_share_base_url_when_set(client, admin_headers, make_booking, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("SHARE_BASE_URL", "https://chanthans.com/event/")
    get_settings.cache_clear()
    try:
        booking = await make_booking()
        url = (await client.post(f"{URL}/{booking['id']}/share", headers=admin_headers)).json()["url"]
        assert url.startswith("https://chanthans.com/event/") and "//event" not in url
        assert url.count("/event/") == 1
    finally:
        monkeypatch.delenv("SHARE_BASE_URL")
        get_settings.cache_clear()
