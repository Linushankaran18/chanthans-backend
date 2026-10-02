from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.dependencies.booking import get_clock
from app.main import app

URL = "/api/v1/admin/dashboard/summary"
TORONTO = ZoneInfo("America/Toronto")


@pytest.fixture
def freeze(client):
    def _freeze(moment: datetime) -> None:
        app.dependency_overrides[get_clock] = lambda: lambda: moment

    return _freeze


async def _summary(client, headers) -> dict:
    resp = await client.get(URL, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_empty_dashboard(client, admin_headers, freeze):
    freeze(datetime(2026, 6, 10, 9, 0, tzinfo=TORONTO))
    body = await _summary(client, admin_headers)
    assert body["today"] == "2026-06-10" and body["timezone"] == "America/Toronto"
    assert body["today_count"] == 0 and body["today_bookings"] == [] and body["next_booking"] is None
    assert body["upcoming_count"] == 0 and body["upcoming"] == []
    assert body["ready_for_delivery_count"] == 0
    assert body["status_counts"] == {
        k: 0 for k in
        ["INQUIRY", "PENDING", "CONFIRMED", "SHOOT_COMPLETED", "EDITING", "READY", "DELIVERED", "CANCELLED"]
    }


async def test_summary_with_frozen_now(client, admin_headers, make_booking, freeze):
    # Wed 10 June 2026, 11:00 in Toronto.
    freeze(datetime(2026, 6, 10, 11, 0, tzinfo=TORONTO))
    morning = await make_booking(customer_name="Morning", event_date="2026-06-10", start_time="08:00", end_time="10:00", status="CONFIRMED")
    ongoing = await make_booking(customer_name="Ongoing", event_date="2026-06-10", start_time="10:30", end_time="12:00", status="CONFIRMED")
    evening = await make_booking(customer_name="Evening", event_date="2026-06-10", start_time="18:00", end_time="20:00", status="CONFIRMED")
    cancelled = await make_booking(customer_name="Cancelled", event_date="2026-06-10", start_time="13:00", end_time="14:00", status="CANCELLED")
    tomorrow = await make_booking(customer_name="Tomorrow", event_date="2026-06-11", status="PENDING")
    past = await make_booking(customer_name="Past", event_date="2026-06-09", status="CONFIRMED")
    await make_booking(customer_name="Ready", event_date="2026-05-01", status="CONFIRMED")
    ready = await make_booking(customer_name="Ready2", event_date="2026-05-02", status="CONFIRMED")
    for step in ("SHOOT_COMPLETED", "EDITING", "READY"):
        await client.patch(f"/api/v1/admin/bookings/{ready['id']}/status", json={"status": step}, headers=admin_headers)

    body = await _summary(client, admin_headers)
    assert body["today_count"] == 3
    assert [b["customer_name"] for b in body["today_bookings"]] == ["Morning", "Ongoing", "Evening"]
    assert cancelled["id"] not in [b["id"] for b in body["today_bookings"]]
    # Morning already ended, so the next booking is the one in progress.
    assert body["next_booking"]["id"] == ongoing["id"]
    assert [b["id"] for b in body["upcoming"]] == [ongoing["id"], evening["id"], tomorrow["id"]]
    assert body["upcoming_count"] == 2  # CONFIRMED and not finished: Ongoing, Evening
    assert body["status_counts"]["CONFIRMED"] == 5 and body["status_counts"]["PENDING"] == 1
    assert body["status_counts"]["CANCELLED"] == 1 and body["status_counts"]["READY"] == 1
    assert body["ready_for_delivery_count"] == 1
    assert {morning["id"], past["id"]}.isdisjoint({b["id"] for b in body["upcoming"]})
    assert "notes" not in body["next_booking"]


async def test_upcoming_limited_to_five_and_ordered(client, admin_headers, make_booking, freeze):
    freeze(datetime(2026, 6, 10, 9, 0, tzinfo=TORONTO))
    for day in range(11, 19):
        await make_booking(customer_name=f"D{day}", event_date=f"2026-06-{day}", status="PENDING")
    body = await _summary(client, admin_headers)
    assert [b["customer_name"] for b in body["upcoming"]] == ["D11", "D12", "D13", "D14", "D15"]


async def test_utc_instant_late_evening_is_still_today_in_toronto(client, admin_headers, make_booking, freeze):
    # 2026-06-11 02:30 UTC is 22:30 on 10 June in Toronto (EDT, UTC-4).
    freeze(datetime(2026, 6, 11, 2, 30, tzinfo=timezone.utc))
    await make_booking(customer_name="Tonight", event_date="2026-06-10", start_time="23:00", end_time="23:30", status="CONFIRMED")
    await make_booking(customer_name="UtcTomorrow", event_date="2026-06-11", status="CONFIRMED")
    body = await _summary(client, admin_headers)
    assert body["today"] == "2026-06-10"
    assert [b["customer_name"] for b in body["today_bookings"]] == ["Tonight"]
    assert body["next_booking"]["customer_name"] == "Tonight"


async def test_dst_spring_forward_boundary(client, admin_headers, make_booking, freeze):
    # DST starts 2026-03-08 at 02:00 local (clocks jump to 03:00). 06:59Z is 01:59 EST, 07:00Z is 03:00 EDT.
    await make_booking(customer_name="Before", event_date="2026-03-08", start_time="01:00", end_time="02:00", status="CONFIRMED")
    await make_booking(customer_name="After", event_date="2026-03-08", start_time="03:00", end_time="04:00", status="CONFIRMED")
    freeze(datetime(2026, 3, 8, 6, 59, tzinfo=timezone.utc))
    body = await _summary(client, admin_headers)
    assert body["today"] == "2026-03-08"
    assert body["next_booking"]["customer_name"] == "Before"  # 01:59 EST: still in progress
    freeze(datetime(2026, 3, 8, 7, 0, tzinfo=timezone.utc))
    body = await _summary(client, admin_headers)
    assert body["today"] == "2026-03-08"
    assert body["next_booking"]["customer_name"] == "After"  # 03:00 EDT: first one is over
    # 04:30Z on 9 March is 00:30 EDT on the 9th, past the DST jump: it is the next local day.
    freeze(datetime(2026, 3, 9, 4, 30, tzinfo=timezone.utc))
    assert (await _summary(client, admin_headers))["today"] == "2026-03-09"


async def test_dst_fall_back_boundary(client, admin_headers, freeze):
    # DST ends 2026-11-01 at 02:00 EDT (back to 01:00 EST). 05:30Z is 01:30 EDT; 06:30Z is 01:30 EST.
    for moment in (datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc), datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc)):
        freeze(moment)
        assert (await _summary(client, admin_headers))["today"] == "2026-11-01"
    freeze(datetime(2026, 11, 2, 4, 59, tzinfo=timezone.utc))  # 23:59 EST on 1 Nov
    assert (await _summary(client, admin_headers))["today"] == "2026-11-01"
    freeze(datetime(2026, 11, 2, 5, 0, tzinfo=timezone.utc))  # midnight EST
    assert (await _summary(client, admin_headers))["today"] == "2026-11-02"
