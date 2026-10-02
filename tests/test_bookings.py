import re
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from tests.conftest import booking_payload

URL = "/api/v1/admin/bookings"


async def test_create_returns_full_booking(client, admin_headers):
    resp = await client.post(URL, json=booking_payload(), headers=admin_headers)
    assert resp.status_code == 201
    body = resp.json()
    assert re.fullmatch(r"BK-\d{4}-0001", body["booking_number"])
    assert body["status"] == "INQUIRY"
    assert body["start_time"] == "09:00:00" and body["end_time"] == "17:00:00"
    assert body["event_date"] == "2026-11-15"
    assert body["total_amount"] == 2500 and body["deposit_amount"] == 500 and body["balance_amount"] == 2000
    assert isinstance(body["total_amount"], (int, float))
    assert body["allowed_next_statuses"] == ["PENDING", "CONFIRMED", "CANCELLED"]
    assert body["calendar_sync_status"] == "NOT_SYNCED"
    assert body["google_calendar_event_id"] is None and body["notes"] is None


async def test_booking_number_year_uses_app_timezone_and_increments(client, admin_headers, make_booking):
    first = await make_booking()
    second = await make_booking(customer_name="Second")
    year = datetime.now(ZoneInfo("America/Toronto")).year
    assert first["booking_number"] == f"BK-{year}-0001"
    assert first["booking_number"][-4:] == "0001" and second["booking_number"][-4:] == "0002"
    assert first["booking_number"] != second["booking_number"]


async def test_booking_numbers_are_unique_across_many_creates(make_booking):
    numbers = {(await make_booking(customer_name=f"C{i}"))["booking_number"] for i in range(6)}
    assert len(numbers) == 6


async def test_balance_null_without_total(make_booking):
    body = await make_booking(total_amount=None, deposit_amount=None)
    assert body["total_amount"] is None and body["balance_amount"] is None


async def test_accepts_hh_mm_ss_and_blank_optionals(client, admin_headers):
    resp = await client.post(
        URL,
        json=booking_payload(start_time="10:30:00", end_time="11:45:30", customer_email="  ", notes="", location=""),
        headers=admin_headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["start_time"] == "10:30:00" and body["end_time"] == "11:45:30"
    assert body["customer_email"] is None and body["location"] is None


def _msgs(resp) -> str:
    return " ".join(e["msg"] for e in resp.json()["detail"])


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"start_time": "10:00", "end_time": "09:00"}, "End time must be later than the start time."),
        ({"start_time": "10:00", "end_time": "10:00"}, "End time must be later than the start time."),
        ({"total_amount": 100, "deposit_amount": 200}, "Deposit cannot be greater than the total amount."),
        ({"customer_name": "   "}, "Customer name is required."),
        ({"customer_phone": ""}, "Phone number is required."),
        ({"total_amount": -5}, "greater than or equal to 0"),
        ({"customer_email": "not-an-email"}, "email"),
        ({"status": "WHATEVER"}, "Input should be"),
    ],
)
async def test_create_validation(client, admin_headers, overrides, message):
    resp = await client.post(URL, json=booking_payload(**overrides), headers=admin_headers)
    assert resp.status_code == 422
    assert message in _msgs(resp)
    assert "Value error" not in _msgs(resp)


async def test_deposit_without_total_is_allowed(client, admin_headers):
    resp = await client.post(
        URL, json=booking_payload(total_amount=None, deposit_amount=100), headers=admin_headers
    )
    assert resp.status_code == 201


async def test_get_and_404(client, admin_headers, make_booking):
    created = await make_booking()
    got = await client.get(f"{URL}/{created['id']}", headers=admin_headers)
    assert got.status_code == 200 and got.json()["id"] == created["id"]
    assert got.json()["booking_number"] == created["booking_number"]
    missing = await client.get(f"{URL}/00000000-0000-0000-0000-000000000000", headers=admin_headers)
    assert missing.status_code == 404 and missing.json() == {"detail": "Booking not found"}


async def test_update_partial_keeps_other_fields_and_ignores_status(client, admin_headers, make_booking):
    created = await make_booking()
    resp = await client.put(
        f"{URL}/{created['id']}",
        json={"location": "Mississauga", "status": "CANCELLED", "notes": "Bring props"},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["location"] == "Mississauga" and body["notes"] == "Bring props"
    assert body["status"] == "INQUIRY" and body["customer_name"] == "Priya Nair"
    assert body["booking_number"] == created["booking_number"]


async def test_update_validates_against_existing_values(client, admin_headers, make_booking):
    created = await make_booking()
    late_start = await client.put(f"{URL}/{created['id']}", json={"start_time": "18:00"}, headers=admin_headers)
    assert late_start.status_code == 422
    assert "End time must be later than the start time." in _msgs(late_start)
    big_deposit = await client.put(f"{URL}/{created['id']}", json={"deposit_amount": 9999}, headers=admin_headers)
    assert big_deposit.status_code == 422
    assert "Deposit cannot be greater" in _msgs(big_deposit)
    null_name = await client.put(f"{URL}/{created['id']}", json={"customer_name": None}, headers=admin_headers)
    assert null_name.status_code == 422


async def test_update_missing_booking(client, admin_headers):
    resp = await client.put(f"{URL}/00000000-0000-0000-0000-000000000000", json={"notes": "x"}, headers=admin_headers)
    assert resp.status_code == 404


async def test_delete(client, admin_headers, make_booking):
    created = await make_booking()
    assert (await client.delete(f"{URL}/{created['id']}", headers=admin_headers)).status_code == 204
    assert (await client.get(f"{URL}/{created['id']}", headers=admin_headers)).status_code == 404
    assert (await client.delete(f"{URL}/{created['id']}", headers=admin_headers)).status_code == 404


@pytest.fixture
async def seeded(make_booking):
    await make_booking(customer_name="Alice Smith", customer_phone="416-555-1111", service_type="Wedding",
                       event_date="2026-11-10", customer_email="alice@example.com")
    await make_booking(customer_name="Bob Jones", customer_phone="(905) 555 2222", service_type="Portrait",
                       event_date="2026-11-12", customer_email=None)
    await make_booking(customer_name="Carol White", customer_phone="647 555 3333", service_type="portrait",
                       event_date="2026-11-14", status="PENDING")
    await make_booking(customer_name="Dan Brown", customer_phone="+1 289 555 4444", service_type="Family",
                       event_date="2026-11-16", status="CANCELLED")


async def _names(client, headers, **params) -> list[str]:
    resp = await client.get(URL, params=params, headers=headers)
    assert resp.status_code == 200, resp.text
    return [i["customer_name"] for i in resp.json()["items"]]


async def test_list_shape_default_sort_and_no_internal_fields(client, admin_headers, seeded):
    resp = await client.get(URL, headers=admin_headers)
    body = resp.json()
    assert (body["total"], body["page"], body["page_size"], body["pages"]) == (4, 1, 20, 1)
    assert [i["customer_name"] for i in body["items"]] == ["Dan Brown", "Carol White", "Bob Jones", "Alice Smith"]
    assert "notes" not in body["items"][0] and "google_calendar_event_id" not in body["items"][0]
    assert "allowed_next_statuses" in body["items"][0] and "balance_amount" in body["items"][0]


async def test_list_sort_options(client, admin_headers, seeded):
    assert (await _names(client, admin_headers, sort="date_asc"))[0] == "Alice Smith"
    assert (await _names(client, admin_headers, sort="created_desc"))[0] == "Dan Brown"
    resp = await client.get(URL, params={"sort": "bogus"}, headers=admin_headers)
    assert resp.status_code == 422


async def test_list_status_filter_repeated_and_comma(client, admin_headers, seeded):
    assert set(await _names(client, admin_headers, status="PENDING")) == {"Carol White"}
    repeated = await client.get(URL + "?status=PENDING&status=CANCELLED", headers=admin_headers)
    assert {i["customer_name"] for i in repeated.json()["items"]} == {"Carol White", "Dan Brown"}
    assert set(await _names(client, admin_headers, status="PENDING,CANCELLED")) == {"Carol White", "Dan Brown"}
    assert (await client.get(URL, params={"status": "NOPE"}, headers=admin_headers)).status_code == 422


async def test_list_search_name_email_number_phone(client, admin_headers, seeded):
    assert await _names(client, admin_headers, search="ALICE") == ["Alice Smith"]
    assert await _names(client, admin_headers, search="bob jo") == ["Bob Jones"]
    assert await _names(client, admin_headers, search="alice@example") == ["Alice Smith"]
    assert await _names(client, admin_headers, search="BK-") and len(await _names(client, admin_headers, search="bk-")) == 4
    assert await _names(client, admin_headers, search="0002") == ["Bob Jones"]
    # phone matching ignores spaces, dashes and parentheses on both sides
    assert await _names(client, admin_headers, search="9055552222") == ["Bob Jones"]
    assert await _names(client, admin_headers, search="(416) 555-1111") == ["Alice Smith"]
    assert await _names(client, admin_headers, search="647-555") == ["Carol White"]
    assert await _names(client, admin_headers, search="zzz") == []
    assert len(await _names(client, admin_headers, search="%")) == 0


async def test_list_date_range_inclusive_and_service_type(client, admin_headers, seeded):
    assert set(await _names(client, admin_headers, date_from="2026-11-12", date_to="2026-11-14")) == {"Bob Jones", "Carol White"}
    assert set(await _names(client, admin_headers, date_from="2026-11-14")) == {"Carol White", "Dan Brown"}
    assert set(await _names(client, admin_headers, date_to="2026-11-10")) == {"Alice Smith"}
    assert set(await _names(client, admin_headers, service_type="portrait")) == {"Bob Jones", "Carol White"}
    assert await _names(client, admin_headers, service_type="Family", status="CANCELLED") == ["Dan Brown"]


async def test_list_pagination(client, admin_headers, seeded):
    page1 = (await client.get(URL, params={"page": 1, "page_size": 3}, headers=admin_headers)).json()
    page2 = (await client.get(URL, params={"page": 2, "page_size": 3}, headers=admin_headers)).json()
    assert (page1["total"], page1["pages"], len(page1["items"]), len(page2["items"])) == (4, 2, 3, 1)
    assert not {i["id"] for i in page1["items"]} & {i["id"] for i in page2["items"]}
    beyond = (await client.get(URL, params={"page": 9, "page_size": 3}, headers=admin_headers)).json()
    assert beyond["items"] == [] and beyond["total"] == 4
    assert (await client.get(URL, params={"page_size": 101}, headers=admin_headers)).status_code == 422
    assert (await client.get(URL, params={"page": 0}, headers=admin_headers)).status_code == 422


async def test_service_types_distinct_sorted(client, admin_headers, make_booking):
    assert (await client.get(f"{URL}/service-types", headers=admin_headers)).json() == []
    for name in ("Wedding", "Family", "Wedding", "Birthday"):
        await make_booking(service_type=name)
    resp = await client.get(f"{URL}/service-types", headers=admin_headers)
    assert resp.status_code == 200 and resp.json() == ["Birthday", "Family", "Wedding"]


async def test_empty_list(client, admin_headers):
    body = (await client.get(URL, headers=admin_headers)).json()
    assert body == {"items": [], "total": 0, "page": 1, "page_size": 20, "pages": 1}


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/admin/bookings"),
        ("get", "/admin/bookings/service-types"),
        ("post", "/admin/bookings"),
        ("get", "/admin/bookings/00000000-0000-0000-0000-000000000000"),
        ("put", "/admin/bookings/00000000-0000-0000-0000-000000000000"),
        ("patch", "/admin/bookings/00000000-0000-0000-0000-000000000000/status"),
        ("delete", "/admin/bookings/00000000-0000-0000-0000-000000000000"),
        ("post", "/admin/bookings/00000000-0000-0000-0000-000000000000/calendar-sync"),
        ("get", "/admin/dashboard/summary"),
        ("get", "/admin/integrations/google-calendar/status"),
        ("get", "/admin/integrations/google-calendar/connect"),
        ("post", "/admin/integrations/google-calendar/disconnect"),
        ("get", "/auth/me"),
    ],
)
async def test_new_routes_require_auth(client, method, path):
    resp = await getattr(client, method)(f"/api/v1{path}")
    assert resp.status_code == 401
    assert resp.json() == {"detail": "Not authenticated"}
