import pytest

from app.models.booking import ALLOWED_TRANSITIONS, BookingStatus
from tests.conftest import booking_payload

URL = "/api/v1/admin/bookings"
ALL = [s.value for s in BookingStatus]
# Walk to each status through legal transitions only.
PATHS = {
    "INQUIRY": [],
    "PENDING": ["PENDING"],
    "CONFIRMED": ["CONFIRMED"],
    "SHOOT_COMPLETED": ["CONFIRMED", "SHOOT_COMPLETED"],
    "EDITING": ["CONFIRMED", "SHOOT_COMPLETED", "EDITING"],
    "READY": ["CONFIRMED", "SHOOT_COMPLETED", "EDITING", "READY"],
    "DELIVERED": ["CONFIRMED", "SHOOT_COMPLETED", "EDITING", "READY", "DELIVERED"],
    "CANCELLED": ["CANCELLED"],
}


async def _patch(client, headers, booking_id, status):
    return await client.patch(f"{URL}/{booking_id}/status", json={"status": status}, headers=headers)


async def _booking_in(client, headers, make_booking, status, **overrides):
    booking = await make_booking(**overrides)
    for step in PATHS[status]:
        assert (await _patch(client, headers, booking["id"], step)).status_code == 200
    return booking


def _cases():
    for current in ALL:
        for target in ALL:
            yield current, target, target in [s.value for s in ALLOWED_TRANSITIONS[BookingStatus(current)]]


@pytest.mark.parametrize(("current", "target", "allowed"), list(_cases()))
async def test_transition_matrix(client, admin_headers, make_booking, current, target, allowed):
    booking = await _booking_in(client, admin_headers, make_booking, current)
    resp = await _patch(client, admin_headers, booking["id"], target)
    if allowed:
        assert resp.status_code == 200
        assert resp.json()["status"] == target
    else:
        assert resp.status_code == 400
        assert resp.json()["detail"].startswith("A ") and "cannot be moved to" in resp.json()["detail"]
        got = (await client.get(f"{URL}/{booking['id']}", headers=admin_headers)).json()
        assert got["status"] == current


async def test_allowed_next_statuses_in_responses(client, admin_headers, make_booking):
    for day, current in enumerate(ALL, start=1):
        expected = [s.value for s in ALLOWED_TRANSITIONS[BookingStatus(current)]]
        booking = await _booking_in(client, admin_headers, make_booking, current, event_date=f"2026-12-{day:02d}")
        got = (await client.get(f"{URL}/{booking['id']}", headers=admin_headers)).json()
        assert got["allowed_next_statuses"] == expected


async def test_unknown_status_is_422(client, admin_headers, make_booking):
    booking = await make_booking()
    assert (await _patch(client, admin_headers, booking["id"], "NOPE")).status_code == 422
    assert (await _patch(client, admin_headers, "00000000-0000-0000-0000-000000000000", "PENDING")).status_code == 404


async def _confirmed(make_booking, start="09:00", end="12:00", **kw):
    return await make_booking(status="CONFIRMED", start_time=start, end_time=end, **kw)


def _assert_conflict(resp, other):
    assert resp.status_code == 409
    body = resp.json()
    assert body["detail"] == "Another confirmed booking overlaps with this time."
    assert body["code"] == "booking_conflict"
    assert [c["id"] for c in body["conflicts"]] == [other["id"]]
    assert set(body["conflicts"][0]) == {
        "id", "booking_number", "customer_name", "service_type", "event_date", "start_time", "end_time", "status",
    }
    assert body["conflicts"][0]["start_time"] == "09:00:00" and body["conflicts"][0]["status"] == "CONFIRMED"


async def test_conflict_on_create_confirmed(client, admin_headers, make_booking):
    existing = await _confirmed(make_booking)
    resp = await client.post(
        URL, json=booking_payload(status="CONFIRMED", start_time="11:00", end_time="13:00"), headers=admin_headers
    )
    _assert_conflict(resp, existing)
    listing = (await client.get(URL, headers=admin_headers)).json()
    assert listing["total"] == 1  # nothing was created


@pytest.mark.parametrize(
    ("start", "end", "conflict"),
    [
        ("09:00", "12:00", True),   # identical
        ("10:00", "11:00", True),   # contained
        ("08:00", "13:00", True),   # containing
        ("08:00", "09:01", True),   # overlaps by a minute at the start
        ("11:59", "14:00", True),   # overlaps by a minute at the end
        ("12:00", "14:00", False),  # adjacent after
        ("07:00", "09:00", False),  # adjacent before
    ],
)
async def test_overlap_rule(client, admin_headers, make_booking, start, end, conflict):
    await _confirmed(make_booking)
    resp = await client.post(
        URL, json=booking_payload(status="CONFIRMED", start_time=start, end_time=end), headers=admin_headers
    )
    assert resp.status_code == (409 if conflict else 201)


async def test_other_day_does_not_conflict(client, admin_headers, make_booking):
    await _confirmed(make_booking)
    resp = await client.post(
        URL, json=booking_payload(status="CONFIRMED", event_date="2026-11-16"), headers=admin_headers
    )
    assert resp.status_code == 201


async def test_unconfirmed_bookings_never_block_and_can_overlap(client, admin_headers, make_booking):
    await make_booking(status="PENDING", start_time="09:00", end_time="12:00")
    await make_booking(status="INQUIRY", start_time="09:00", end_time="12:00")
    assert (await _confirmed(make_booking))["status"] == "CONFIRMED"


async def test_cancelled_booking_is_ignored(client, admin_headers, make_booking):
    existing = await _confirmed(make_booking)
    assert (await _patch(client, admin_headers, existing["id"], "CANCELLED")).status_code == 200
    resp = await client.post(URL, json=booking_payload(status="CONFIRMED"), headers=admin_headers)
    assert resp.status_code == 201


async def test_conflict_on_confirm_transition(client, admin_headers, make_booking):
    existing = await _confirmed(make_booking)
    pending = await make_booking(status="PENDING", start_time="10:00", end_time="11:00")
    resp = await _patch(client, admin_headers, pending["id"], "CONFIRMED")
    _assert_conflict(resp, existing)
    still = (await client.get(f"{URL}/{pending['id']}", headers=admin_headers)).json()
    assert still["status"] == "PENDING"


async def test_conflict_on_update_of_confirmed(client, admin_headers, make_booking):
    first = await _confirmed(make_booking)
    second = await _confirmed(make_booking, start="13:00", end="15:00")
    resp = await client.put(f"{URL}/{second['id']}", json={"start_time": "11:00"}, headers=admin_headers)
    _assert_conflict(resp, first)
    moved = await client.put(f"{URL}/{second['id']}", json={"start_time": "12:00"}, headers=admin_headers)
    assert moved.status_code == 200  # adjacent is fine


async def test_update_excludes_self_and_skips_check_when_time_unchanged(client, admin_headers, make_booking):
    booking = await _confirmed(make_booking)
    same = await client.put(
        f"{URL}/{booking['id']}", json={"start_time": "09:00", "end_time": "12:30", "notes": "x"}, headers=admin_headers
    )
    assert same.status_code == 200  # overlaps only with itself
    notes_only = await client.put(f"{URL}/{booking['id']}", json={"notes": "y"}, headers=admin_headers)
    assert notes_only.status_code == 200


async def test_update_of_unconfirmed_booking_not_checked(client, admin_headers, make_booking):
    await _confirmed(make_booking)
    pending = await make_booking(status="PENDING", start_time="13:00", end_time="14:00")
    resp = await client.put(f"{URL}/{pending['id']}", json={"start_time": "10:00"}, headers=admin_headers)
    assert resp.status_code == 200


async def test_conflict_lists_every_overlapping_booking(client, admin_headers, make_booking):
    await _confirmed(make_booking, start="09:00", end="10:00")
    await _confirmed(make_booking, start="10:00", end="11:00")
    resp = await client.post(
        URL, json=booking_payload(status="CONFIRMED", start_time="09:30", end_time="10:30"), headers=admin_headers
    )
    assert resp.status_code == 409 and len(resp.json()["conflicts"]) == 2
