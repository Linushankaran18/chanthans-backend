import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

URL = "/api/v1/admin/invoices"


def _today() -> date:
    return datetime.now(ZoneInfo("America/Toronto")).date()


def items(*rows):
    return [{"description": d, "quantity": q, "unit_price": p} for d, q, p in rows]


async def create(client, headers, booking, **overrides):
    payload = {"booking_id": booking["id"], **overrides}
    return await client.post(URL, json=payload, headers=headers)


@pytest.fixture
async def booking(make_booking):
    return await make_booking()  # total 2500, deposit 500


# --- auth ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", ""), ("POST", ""), ("GET", "/settings"), ("GET", "/prefill?booking_id=" + "0" * 32),
        ("GET", "/" + "0" * 32), ("PUT", "/" + "0" * 32), ("DELETE", "/" + "0" * 32),
        ("PATCH", "/" + "0" * 32 + "/status"), ("PATCH", "/" + "0" * 32 + "/payment"),
        ("GET", "/" + "0" * 32 + "/pdf"),
    ],
)
async def test_every_endpoint_requires_authentication(client, method, path):
    resp = await client.request(method, URL + path)
    assert resp.status_code == 401


# --- prefill / create ---------------------------------------------------------


async def test_prefill_uses_booking_and_business_defaults(client, admin_headers, booking):
    resp = await client.get(f"{URL}/prefill", params={"booking_id": booking["id"]}, headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["customer_name"] == "Priya Nair" and body["customer_email"] == "priya@example.com"
    assert body["customer_phone"] == "(416) 555-0134"
    assert body["booking_number"] == booking["booking_number"] and body["service_type"] == "Wedding"
    assert body["items"] == [{"description": "Wedding (15 Nov 2026)", "quantity": 1, "unit_price": 2500}]
    assert body["amount_paid"] == 500
    assert body["currency"] == "CAD"
    assert body["issue_date"] == _today().isoformat()
    assert body["due_date"] == (_today() + timedelta(days=14)).isoformat()
    assert body["tax_rate"] is None and body["tax_name"] is None  # never assumed
    assert re.fullmatch(r"INV-\d{4}-0001", body["next_invoice_number"])
    assert body["existing_invoice_id"] is None


async def test_create_with_only_a_booking_fills_everything(client, admin_headers, booking):
    resp = await create(client, admin_headers, booking)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert re.fullmatch(r"INV-\d{4}-0001", body["invoice_number"])
    assert body["status"] == "DRAFT"
    assert body["customer_name"] == "Priya Nair"
    assert body["booking_id"] == booking["id"] and body["booking_number"] == booking["booking_number"]
    assert body["subtotal"] == 2500 and body["total_amount"] == 2500
    assert body["amount_paid"] == 500 and body["balance_due"] == 2000
    assert len(body["items"]) == 1 and body["items"][0]["line_total"] == 2500


async def test_create_calculates_everything_on_the_server(client, admin_headers, booking):
    resp = await create(
        client, admin_headers, booking,
        items=items(
            ("Wedding Photography Package", 1, 2200), ("Printed Album", 1, 450),
            ("Travel Fee", 1, 100), ("Additional Editing", 2, 50),
        ),
        discount_amount=100, tax_name="HST", tax_rate=13, amount_paid=500,
        # A client cannot dictate totals: unknown fields are ignored.
        total_amount=1, balance_due=1,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert [i["line_total"] for i in body["items"]] == [2200, 450, 100, 100]
    assert [i["display_order"] for i in body["items"]] == [0, 1, 2, 3]
    assert body["subtotal"] == 2850 and body["discount_amount"] == 100
    assert body["tax_amount"] == 357.5 and body["tax_name"] == "HST" and body["tax_rate"] == 13
    assert body["total_amount"] == 3107.5 and body["amount_paid"] == 500 and body["balance_due"] == 2607.5


async def test_invoice_numbers_increment_and_are_unique(client, admin_headers, make_booking):
    numbers = []
    for i in range(3):
        b = await make_booking(customer_name=f"C{i}")
        numbers.append((await create(client, admin_headers, b)).json()["invoice_number"])
    assert [n[-4:] for n in numbers] == ["0001", "0002", "0003"]
    assert len(set(numbers)) == 3
    year = datetime.now(ZoneInfo("America/Toronto")).year
    assert numbers[0] == f"INV-{year}-0001"


async def test_one_invoice_per_booking(client, admin_headers, booking):
    first = (await create(client, admin_headers, booking)).json()
    again = await create(client, admin_headers, booking)
    assert again.status_code == 409
    assert again.json()["code"] == "invoice_exists" and again.json()["invoice_id"] == first["id"]
    prefill = await client.get(f"{URL}/prefill", params={"booking_id": booking["id"]}, headers=admin_headers)
    assert prefill.json()["existing_invoice_id"] == first["id"]


async def test_cancelled_invoice_allows_a_new_one(client, admin_headers, booking):
    first = (await create(client, admin_headers, booking)).json()
    await client.patch(f"{URL}/{first['id']}/status", json={"status": "CANCELLED"}, headers=admin_headers)
    second = await create(client, admin_headers, booking)
    assert second.status_code == 201 and second.json()["invoice_number"] != first["invoice_number"]


async def test_unknown_booking_is_404(client, admin_headers):
    resp = await client.post(URL, json={"booking_id": "11111111-1111-1111-1111-111111111111"}, headers=admin_headers)
    assert resp.status_code == 404


async def test_create_can_issue_immediately(client, admin_headers, booking, make_booking):
    body = (await create(client, admin_headers, booking, status="ISSUED", amount_paid=0)).json()
    assert body["status"] == "ISSUED"
    other = await make_booking(customer_name="Other")
    body = (await create(client, admin_headers, other, status="ISSUED")).json()
    assert body["status"] == "PARTIALLY_PAID"  # the 500 deposit is already paid


# --- validation ---------------------------------------------------------------


def _msgs(resp) -> str:
    return " ".join(e["msg"] for e in resp.json()["detail"])


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"items": items(("A", 0, 10))}, "greater than"),
        ({"items": items(("A", 1, -5))}, "greater than or equal to 0"),
        ({"items": items(("  ", 1, 5))}, "Description is required"),
        ({"items": []}, "at least one line item"),
        ({"items": items(("A", 1, 100)), "discount_amount": 150}, "Discount cannot be greater than the subtotal"),
        ({"items": items(("A", 1, 100)), "discount_amount": -1}, "greater than or equal to 0"),
        ({"items": items(("A", 1, 100)), "amount_paid": -1}, "greater than or equal to 0"),
        ({"items": items(("A", 1, 100)), "amount_paid": 100.01}, "cannot be greater than the invoice total"),
        ({"tax_rate": 120}, "less than or equal to 100"),
        ({"customer_email": "nope"}, "email"),
        ({"issue_date": "2026-10-10", "due_date": "2026-10-01"}, "Due date can't be before"),
        ({"customer_name": "  "}, "Customer name is required"),
    ],
)
async def test_validation(client, admin_headers, booking, overrides, message):
    resp = await create(client, admin_headers, booking, **overrides)
    assert resp.status_code == 422, resp.text
    assert message.lower() in _msgs(resp).lower()


async def test_blank_optional_text_becomes_null_and_overrides_prefill(client, admin_headers, booking):
    body = (await create(client, admin_headers, booking, customer_email="", notes="  ", terms="")).json()
    assert body["customer_email"] is None and body["notes"] is None and body["terms"] is None


# --- read / list --------------------------------------------------------------


async def test_get_and_list_and_by_booking(client, admin_headers, booking):
    created = (await create(client, admin_headers, booking)).json()
    got = await client.get(f"{URL}/{created['id']}", headers=admin_headers)
    assert got.status_code == 200 and got.json()["invoice_number"] == created["invoice_number"]
    assert got.json()["service_type"] == "Wedding" and got.json()["event_date"] == "2026-11-15"
    listing = (await client.get(URL, headers=admin_headers)).json()
    assert listing["total"] == 1 and listing["items"][0]["id"] == created["id"]
    assert "items" not in listing["items"][0]
    linked = (await client.get(f"{URL}/by-booking/{booking['id']}", headers=admin_headers)).json()
    assert linked["id"] == created["id"]
    assert (await client.get(f"{URL}/11111111-1111-1111-1111-111111111111", headers=admin_headers)).status_code == 404


async def test_by_booking_is_null_without_invoice(client, admin_headers, booking):
    resp = await client.get(f"{URL}/by-booking/{booking['id']}", headers=admin_headers)
    assert resp.status_code == 200 and resp.json() is None


async def test_list_search_and_filters(client, admin_headers, make_booking):
    a = await make_booking(customer_name="Alice Wong")
    b = await make_booking(customer_name="Bob Singh")
    inv_a = (await create(client, admin_headers, a)).json()
    inv_b = (await create(client, admin_headers, b, status="ISSUED", amount_paid=0)).json()

    async def ids(**params):
        return {i["id"] for i in (await client.get(URL, params=params, headers=admin_headers)).json()["items"]}

    assert await ids(search="alice") == {inv_a["id"]}
    assert await ids(search=inv_b["invoice_number"].lower()) == {inv_b["id"]}
    assert await ids(search=a["booking_number"]) == {inv_a["id"]}
    assert await ids(status="DRAFT") == {inv_a["id"]}
    assert await ids(status="DRAFT,ISSUED") == {inv_a["id"], inv_b["id"]}
    assert await ids(date_from=(_today() + timedelta(days=1)).isoformat()) == set()
    assert await ids(date_to=_today().isoformat()) == {inv_a["id"], inv_b["id"]}
    bad = await client.get(URL, params={"status": "NOPE"}, headers=admin_headers)
    assert bad.status_code == 422


async def test_overdue_is_derived_not_stored(client, admin_headers, booking):
    past = (_today() - timedelta(days=30)).isoformat()
    created = (
        await create(
            client, admin_headers, booking, status="ISSUED", amount_paid=0,
            issue_date=(_today() - timedelta(days=44)).isoformat(), due_date=past,
        )
    ).json()
    assert created["status"] == "ISSUED"  # stored status is untouched
    assert created["is_overdue"] is True and created["display_status"] == "OVERDUE"
    assert {i["id"] for i in (await client.get(URL, params={"overdue": True}, headers=admin_headers)).json()["items"]} == {created["id"]}
    assert (await client.get(URL, params={"status": "OVERDUE"}, headers=admin_headers)).json()["total"] == 1

    paid = (await client.patch(f"{URL}/{created['id']}/payment", json={"mark_as_paid": True}, headers=admin_headers)).json()
    assert paid["is_overdue"] is False and paid["display_status"] == "PAID"


async def test_drafts_and_future_invoices_are_not_overdue(client, admin_headers, make_booking):
    draft = (await create(client, admin_headers, await make_booking(), due_date=(_today() - timedelta(days=3)).isoformat(),
                          issue_date=(_today() - timedelta(days=10)).isoformat())).json()
    assert draft["is_overdue"] is False
    future = (await create(client, admin_headers, await make_booking(customer_name="F"), status="ISSUED")).json()
    assert future["is_overdue"] is False


# --- update -------------------------------------------------------------------


async def test_update_recalculates_and_replaces_items(client, admin_headers, booking):
    created = (await create(client, admin_headers, booking)).json()
    resp = await client.put(
        f"{URL}/{created['id']}",
        json={"items": items(("Album", 2, 300), ("Prints", 1, 75.5)), "discount_amount": 75.5, "tax_name": "GST", "tax_rate": 5},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [i["description"] for i in body["items"]] == ["Album", "Prints"]
    assert body["subtotal"] == 675.5 and body["tax_amount"] == 30 and body["total_amount"] == 630
    assert body["balance_due"] == 130  # the 500 deposit stays recorded
    assert body["invoice_number"] == created["invoice_number"]


async def test_partial_update_keeps_other_fields(client, admin_headers, booking):
    created = (await create(client, admin_headers, booking)).json()
    body = (await client.put(f"{URL}/{created['id']}", json={"notes": "Hello"}, headers=admin_headers)).json()
    assert body["notes"] == "Hello" and body["total_amount"] == 2500 and len(body["items"]) == 1


async def test_update_validates_the_merged_result(client, admin_headers, booking):
    created = (await create(client, admin_headers, booking)).json()  # total 2500, paid 500
    resp = await client.put(f"{URL}/{created['id']}", json={"discount_amount": 2500, "amount_paid": 500}, headers=admin_headers)
    assert resp.status_code == 422 and "cannot be greater than the invoice total" in _msgs(resp)
    resp = await client.put(f"{URL}/{created['id']}", json={"items": []}, headers=admin_headers)
    assert resp.status_code == 422
    resp = await client.put(f"{URL}/{created['id']}", json={"customer_name": None}, headers=admin_headers)
    assert resp.status_code == 422


async def test_editing_an_issued_invoice_resettles_its_status(client, admin_headers, booking):
    created = (await create(client, admin_headers, booking, status="ISSUED", amount_paid=500)).json()
    assert created["status"] == "PARTIALLY_PAID"
    body = (await client.put(f"{URL}/{created['id']}", json={"amount_paid": 2500}, headers=admin_headers)).json()
    assert body["status"] == "PAID" and body["balance_due"] == 0


async def test_cancelled_invoice_cannot_be_edited(client, admin_headers, booking):
    created = (await create(client, admin_headers, booking)).json()
    await client.patch(f"{URL}/{created['id']}/status", json={"status": "CANCELLED"}, headers=admin_headers)
    resp = await client.put(f"{URL}/{created['id']}", json={"notes": "x"}, headers=admin_headers)
    assert resp.status_code == 400


# --- status & payment ---------------------------------------------------------


async def test_status_transitions(client, admin_headers, booking):
    inv = (await create(client, admin_headers, booking)).json()
    path = f"{URL}/{inv['id']}/status"
    issued = await client.patch(path, json={"status": "ISSUED"}, headers=admin_headers)
    assert issued.json()["status"] == "PARTIALLY_PAID"  # deposit already received
    assert (await client.patch(path, json={"status": "ISSUED"}, headers=admin_headers)).status_code == 400
    assert (await client.patch(path, json={"status": "PAID"}, headers=admin_headers)).status_code == 422
    assert (await client.patch(path, json={"status": "OVERDUE"}, headers=admin_headers)).status_code == 422
    cancelled = await client.patch(path, json={"status": "CANCELLED"}, headers=admin_headers)
    assert cancelled.json()["status"] == "CANCELLED"
    assert (await client.patch(path, json={"status": "ISSUED"}, headers=admin_headers)).status_code == 400


async def test_paid_invoice_cannot_be_cancelled(client, admin_headers, booking):
    inv = (await create(client, admin_headers, booking, status="ISSUED")).json()
    await client.patch(f"{URL}/{inv['id']}/payment", json={"mark_as_paid": True}, headers=admin_headers)
    resp = await client.patch(f"{URL}/{inv['id']}/status", json={"status": "CANCELLED"}, headers=admin_headers)
    assert resp.status_code == 400


async def test_payment_updates_balance_and_status(client, admin_headers, booking):
    inv = (await create(client, admin_headers, booking, status="ISSUED", amount_paid=0)).json()
    path = f"{URL}/{inv['id']}/payment"
    partial = (await client.patch(path, json={"amount_paid": 1000}, headers=admin_headers)).json()
    assert partial["status"] == "PARTIALLY_PAID" and partial["balance_due"] == 1500 and partial["amount_paid"] == 1000
    full = (await client.patch(path, json={"amount_paid": 2500}, headers=admin_headers)).json()
    assert full["status"] == "PAID" and full["balance_due"] == 0
    back = (await client.patch(path, json={"amount_paid": 0}, headers=admin_headers)).json()
    assert back["status"] == "ISSUED" and back["balance_due"] == 2500


async def test_mark_as_paid(client, admin_headers, booking):
    inv = (await create(client, admin_headers, booking, status="ISSUED", tax_name="HST", tax_rate=13)).json()
    body = (await client.patch(f"{URL}/{inv['id']}/payment", json={"mark_as_paid": True}, headers=admin_headers)).json()
    assert body["status"] == "PAID" and body["amount_paid"] == body["total_amount"] == 2825 and body["balance_due"] == 0


async def test_payment_rules(client, admin_headers, booking):
    inv = (await create(client, admin_headers, booking)).json()
    path = f"{URL}/{inv['id']}/payment"
    assert (await client.patch(path, json={"amount_paid": 10}, headers=admin_headers)).status_code == 400  # draft
    await client.patch(f"{URL}/{inv['id']}/status", json={"status": "ISSUED"}, headers=admin_headers)
    assert (await client.patch(path, json={"amount_paid": 2500.01}, headers=admin_headers)).status_code == 422
    assert (await client.patch(path, json={"amount_paid": -1}, headers=admin_headers)).status_code == 422
    assert (await client.patch(path, json={}, headers=admin_headers)).status_code == 422
    await client.patch(f"{URL}/{inv['id']}/status", json={"status": "CANCELLED"}, headers=admin_headers)
    assert (await client.patch(path, json={"amount_paid": 10}, headers=admin_headers)).status_code == 400


# --- delete -------------------------------------------------------------------


async def test_delete_only_drafts_and_cancelled(client, admin_headers, booking):
    inv = (await create(client, admin_headers, booking, status="ISSUED")).json()
    assert (await client.delete(f"{URL}/{inv['id']}", headers=admin_headers)).status_code == 409
    await client.patch(f"{URL}/{inv['id']}/status", json={"status": "CANCELLED"}, headers=admin_headers)
    assert (await client.delete(f"{URL}/{inv['id']}", headers=admin_headers)).status_code == 204
    assert (await client.get(f"{URL}/{inv['id']}", headers=admin_headers)).status_code == 404


async def test_deleting_a_draft_frees_the_booking(client, admin_headers, booking):
    inv = (await create(client, admin_headers, booking)).json()
    assert (await client.delete(f"{URL}/{inv['id']}", headers=admin_headers)).status_code == 204
    assert (await create(client, admin_headers, booking)).status_code == 201


async def test_booking_with_an_invoice_cannot_be_deleted(client, admin_headers, booking):
    inv = (await create(client, admin_headers, booking)).json()
    resp = await client.delete(f"/api/v1/admin/bookings/{booking['id']}", headers=admin_headers)
    assert resp.status_code == 409 and "invoice" in resp.json()["detail"].lower()
    await client.delete(f"{URL}/{inv['id']}", headers=admin_headers)
    assert (await client.delete(f"/api/v1/admin/bookings/{booking['id']}", headers=admin_headers)).status_code == 204


# --- settings -----------------------------------------------------------------


async def test_settings_endpoint_exposes_business_defaults_only(client, admin_headers):
    body = (await client.get(f"{URL}/settings", headers=admin_headers)).json()
    assert body["currency"] == "CAD" and body["business_name"] == "Chanthans"
    assert body["invoice_due_days"] == 14 and body["tax_rate"] is None
    assert body["default_terms"] == "Payment is due within 14 days of the invoice date."
    assert not any("secret" in k or "token" in k or "key" in k for k in body)
