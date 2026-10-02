from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import jwt
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.main import app
from app.models.user import User
from app.core.security import hash_password
from app.services.google_calendar_client import GoogleNetworkError
from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD

P = "/api/v1/auth/google"
FRONT = "http://localhost:5173"
LOGIN_CB = "http://localhost:8000/api/v1/auth/google/callback"


def _settings(**over) -> Settings:
    base = get_settings()
    values = dict(
        _env_file=None, database_url="sqlite+aiosqlite://", jwt_secret=base.jwt_secret,
        google_client_id=base.google_client_id, google_client_secret=base.google_client_secret,
        google_login_redirect_uri=LOGIN_CB, google_login_allowed_emails=" Owner@Example.com , other@example.com ",
        frontend_url=FRONT,
    )
    values.update(over)
    return Settings(**values)


import pytest


@pytest.fixture(autouse=True)
def login_enabled(google):
    cfg = _settings()
    google.settings = cfg
    app.dependency_overrides[get_settings] = lambda: cfg
    yield cfg


async def _state(client) -> str:
    resp = await client.get(f"{P}/login", follow_redirects=False)
    return parse_qs(urlparse(resp.headers["location"]).query)["state"][0]


async def _cb(client, **params):
    return await client.get(f"{P}/callback", params=params, follow_redirects=False)


def _err(resp) -> str:
    loc = urlparse(resp.headers["location"])
    assert resp.status_code == 302 and f"{loc.scheme}://{loc.netloc}{loc.path}" == f"{FRONT}/login"
    return parse_qs(loc.query)["google_error"][0]


async def _users(sessionmaker):
    async with sessionmaker() as s:
        return list((await s.scalars(select(User))).all())


async def test_enabled_true_and_false(client):
    assert (await client.get(f"{P}/enabled")).json() == {"enabled": True}
    for over in ({"google_login_allowed_emails": ""}, {"google_login_redirect_uri": ""}, {"google_client_secret": ""}):
        cfg = _settings(**over)
        app.dependency_overrides[get_settings] = lambda c=cfg: c
        assert (await client.get(f"{P}/enabled")).json() == {"enabled": False}


async def test_login_redirect_contents(client):
    resp = await client.get(f"{P}/login", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["cache-control"] == "no-store"
    parsed = urlparse(resp.headers["location"])
    q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    assert parsed.netloc == "accounts.google.com"
    assert q["scope"] == "openid email"
    assert q["redirect_uri"] == LOGIN_CB and q["response_type"] == "code" and q["prompt"] == "select_account"
    assert q["client_id"] == get_settings().google_client_id and "client_secret" not in q
    payload = jwt.decode(q["state"], get_settings().jwt_secret, algorithms=["HS256"])
    assert payload["purpose"] == "google_login" and payload["nonce"]
    assert timedelta(0) < datetime.fromtimestamp(payload["exp"], timezone.utc) - datetime.now(timezone.utc) <= timedelta(minutes=10)


async def test_login_disabled_redirects_to_frontend(client):
    cfg = _settings(google_login_allowed_emails="")
    app.dependency_overrides[get_settings] = lambda: cfg
    resp = await client.get(f"{P}/login", follow_redirects=False)
    assert resp.status_code == 302 and resp.headers["location"] == f"{FRONT}/login?google_error=not_configured"
    assert _err(await _cb(client, code="c", state="s")) == "not_configured"


async def test_callback_success_creates_user_and_token_in_fragment(client, sessionmaker, google):
    google.profile = {"email": "OWNER@example.com", "email_verified": True, "name": "Owner Person"}
    resp = await _cb(client, code="the-code", state=await _state(client))
    assert resp.status_code == 302
    assert resp.headers["cache-control"] == "no-store" and resp.headers["referrer-policy"] == "no-referrer"
    loc = urlparse(resp.headers["location"])
    assert f"{loc.scheme}://{loc.netloc}{loc.path}" == f"{FRONT}/auth/google/complete"
    assert loc.query == "" and loc.fragment.startswith("token=")
    token = loc.fragment[len("token="):]
    assert ("login_exchange", "the-code") in google.calls
    users = await _users(sessionmaker)
    assert len(users) == 1 and users[0].email == "owner@example.com" and users[0].role == "admin"
    assert users[0].full_name == "Owner Person"
    me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200 and me.json()["email"] == "owner@example.com"
    bookings = await client.get("/api/v1/admin/bookings", headers={"Authorization": f"Bearer {token}"})
    assert bookings.status_code == 200


async def test_callback_reuses_existing_user(client, sessionmaker, google):
    async with sessionmaker() as s:
        s.add(User(email="owner@example.com", password_hash=hash_password("pw-pw-pw-pw"), role="admin"))
        await s.commit()
    resp = await _cb(client, code="c", state=await _state(client))
    assert "#token=" in resp.headers["location"]
    users = await _users(sessionmaker)
    assert len(users) == 1 and users[0].full_name == "Owner Person"
    # Existing name is kept.
    async with sessionmaker() as s:
        u = (await s.scalars(select(User))).one()
        u.full_name = "Kept"
        await s.commit()
    await _cb(client, code="c", state=await _state(client))
    assert (await _users(sessionmaker))[0].full_name == "Kept"


async def test_not_allowed(client, sessionmaker, google):
    google.profile = {"email": "stranger@example.com", "email_verified": True}
    resp = await _cb(client, code="c", state=await _state(client))
    assert _err(resp) == "not_allowed" and "#" not in resp.headers["location"]
    assert await _users(sessionmaker) == []


async def test_not_verified(client, google):
    google.profile = {"email": "owner@example.com", "email_verified": False}
    assert _err(await _cb(client, code="c", state=await _state(client))) == "not_verified"
    google.profile = {"email": "owner@example.com"}
    assert _err(await _cb(client, code="c", state=await _state(client))) == "not_verified"


async def test_denied_and_other_errors(client, google):
    assert _err(await _cb(client, error="access_denied", state=await _state(client))) == "denied"
    assert _err(await _cb(client, state=await _state(client))) == "exchange_failed"
    google.exchange_error = GoogleNetworkError("x")
    assert _err(await _cb(client, code="c", state=await _state(client))) == "exchange_failed"


async def test_invalid_expired_and_calendar_state(client, google):
    assert _err(await _cb(client, code="c")) == "invalid_state"
    assert _err(await _cb(client, code="c", state="garbage")) == "invalid_state"
    secret = get_settings().jwt_secret
    now = datetime.now(timezone.utc)
    expired = jwt.encode({"purpose": "google_login", "nonce": "n", "exp": now - timedelta(minutes=1)}, secret, algorithm="HS256")
    assert _err(await _cb(client, code="c", state=expired)) == "invalid_state"
    calendar = jwt.encode({"sub": "x", "purpose": "google_calendar_connect", "nonce": "n", "exp": now + timedelta(minutes=5)}, secret, algorithm="HS256")
    assert _err(await _cb(client, code="c", state=calendar)) == "invalid_state"
    assert not any(c[0] == "login_exchange" for c in google.calls)


async def test_login_state_rejected_by_calendar_callback(client):
    state = await _state(client)
    resp = await client.get(
        "/api/v1/integrations/google-calendar/callback", params={"code": "c", "state": state}, follow_redirects=False
    )
    assert "reason=invalid_state" in resp.headers["location"]


async def test_password_login_still_works(client, admin_headers):
    resp = await client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200 and resp.json()["access_token"]
