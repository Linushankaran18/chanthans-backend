from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD


async def test_login_success(client, admin_headers):
    resp = await client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer" and body["access_token"]
    assert "password" not in str(body)


async def test_login_wrong_password(client, admin_headers):
    resp = await client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": "nope-nope-nope"})
    assert resp.status_code == 401
    assert resp.json() == {"detail": "Invalid credentials"}


async def test_login_unknown_email_same_response(client, admin_headers):
    resp = await client.post("/api/v1/auth/login", json={"email": "ghost@example.com", "password": "whatever-1234"})
    assert resp.status_code == 401
    assert resp.json() == {"detail": "Invalid credentials"}


async def test_protected_route_requires_token(client):
    assert (await client.get("/api/v1/admin/hero-images")).status_code == 401


async def test_protected_route_rejects_garbage_token(client):
    resp = await client.get("/api/v1/admin/hero-images", headers={"Authorization": "Bearer abc.def.ghi"})
    assert resp.status_code == 401


async def test_health(client):
    resp = await client.get("/health")
    assert resp.json() == {"status": "ok"}


async def test_me_returns_profile_without_secrets(client, admin_headers):
    resp = await client.get("/api/v1/auth/me", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"id", "email", "full_name", "role"}
    assert body["email"] == ADMIN_EMAIL and body["role"] == "admin" and body["full_name"] is None
