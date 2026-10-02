import uuid
from unittest.mock import MagicMock

from sqlalchemy import select

from app.models.hero_image import HeroImage
from app.services.storage_service import StorageError
from tests.conftest import image_bytes

ADMIN = "/api/v1/admin/hero-images"


async def test_public_only_active_sorted(client, make_hero):
    await make_hero(order=3)
    await make_hero(order=1)
    await make_hero(order=2, active=False)
    resp = await client.get("/api/v1/hero-images")
    assert resp.status_code == 200
    data = resp.json()
    assert [h["display_order"] for h in data] == [1, 3]
    assert set(data[0]) == {"id", "image_url", "title", "subtitle", "alt_text", "display_order"}


async def test_admin_list_includes_inactive(client, admin_headers, make_hero):
    await make_hero(order=1)
    await make_hero(order=2, active=False)
    resp = await client.get(ADMIN, headers=admin_headers)
    assert len(resp.json()) == 2
    assert "is_active" in resp.json()[0] and "image_key" not in resp.json()[0]


async def test_create_rejects_bad_content_type(client, admin_headers):
    files = {"image": ("a.gif", image_bytes("GIF"), "image/gif")}
    resp = await client.post(ADMIN, headers=admin_headers, files=files)
    assert resp.status_code == 400


async def test_create_rejects_non_image(client, admin_headers, storage):
    files = {"image": ("a.png", b"definitely not an image", "image/png")}
    resp = await client.post(ADMIN, headers=admin_headers, files=files)
    assert resp.status_code == 400
    storage.upload_image.assert_not_called()


async def test_create_rejects_disallowed_real_format(client, admin_headers):
    files = {"image": ("a.png", image_bytes("GIF"), "image/png")}
    assert (await client.post(ADMIN, headers=admin_headers, files=files)).status_code == 400


async def test_create_rejects_too_large(client, admin_headers, storage):
    files = {"image": ("a.png", b"\x89PNG" + b"0" * (2 * 1024 * 1024), "image/png")}
    resp = await client.post(ADMIN, headers=admin_headers, files=files)
    assert resp.status_code == 413
    storage.upload_image.assert_not_called()


async def test_create_requires_auth(client):
    files = {"image": ("a.png", image_bytes(), "image/png")}
    assert (await client.post(ADMIN, files=files)).status_code == 401


async def test_create_success(client, admin_headers, storage, sessionmaker):
    files = {"image": ("../evil name.jpg", image_bytes("PNG"), "image/png")}
    data = {"title": "Hi", "alt_text": "alt", "display_order": "4", "is_active": "false"}
    resp = await client.post(ADMIN, headers=admin_headers, files=files, data=data)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["title"] == "Hi" and body["display_order"] == 4 and body["is_active"] is False
    key = storage.upload_image.call_args.args[0]
    assert key.startswith("hero/") and key.endswith(".png") and "evil" not in key
    assert body["image_url"] == f"https://cdn.test/{key}"
    async with sessionmaker() as s:
        assert (await s.execute(select(HeroImage))).scalar_one().image_key == key


async def test_create_cleans_up_r2_when_db_fails(client, admin_headers, storage, monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError

    from app.repositories.hero_repository import HeroRepository

    async def boom(self, image):
        raise SQLAlchemyError("db down")

    monkeypatch.setattr(HeroRepository, "add", boom)
    files = {"image": ("a.png", image_bytes(), "image/png")}
    resp = await client.post(ADMIN, headers=admin_headers, files=files)
    assert resp.status_code == 500
    assert resp.json() == {"detail": "Database error"}
    storage.delete_image.assert_called_once_with(storage.upload_image.call_args.args[0])


async def test_update(client, admin_headers, make_hero):
    hero = await make_hero(order=1)
    resp = await client.put(f"{ADMIN}/{hero.id}", headers=admin_headers, json={"title": "New", "display_order": 9})
    assert resp.status_code == 200
    assert resp.json()["title"] == "New" and resp.json()["display_order"] == 9


async def test_update_not_found(client, admin_headers):
    resp = await client.put(f"{ADMIN}/{uuid.uuid4()}", headers=admin_headers, json={"title": "x"})
    assert resp.status_code == 404


async def test_status_update(client, admin_headers, make_hero):
    hero = await make_hero(order=1)
    resp = await client.patch(f"{ADMIN}/{hero.id}/status", headers=admin_headers, json={"is_active": False})
    assert resp.json()["is_active"] is False
    assert (await client.get("/api/v1/hero-images")).json() == []


async def test_reorder(client, admin_headers, make_hero):
    a, b = await make_hero(order=1), await make_hero(order=2)
    payload = [{"id": str(a.id), "display_order": 5}, {"id": str(b.id), "display_order": 0}]
    resp = await client.patch(f"{ADMIN}/reorder", headers=admin_headers, json=payload)
    assert resp.status_code == 200
    assert [h["id"] for h in resp.json()] == [str(b.id), str(a.id)]


async def test_reorder_all_or_nothing(client, admin_headers, make_hero):
    a = await make_hero(order=1)
    payload = [{"id": str(a.id), "display_order": 7}, {"id": str(uuid.uuid4()), "display_order": 1}]
    resp = await client.patch(f"{ADMIN}/reorder", headers=admin_headers, json=payload)
    assert resp.status_code == 404
    listing = (await client.get(ADMIN, headers=admin_headers)).json()
    assert listing[0]["display_order"] == 1


async def test_delete(client, admin_headers, make_hero, storage):
    hero = await make_hero(order=1, key="hero/x.jpg")
    resp = await client.delete(f"{ADMIN}/{hero.id}", headers=admin_headers)
    assert resp.status_code == 204
    storage.delete_image.assert_called_once_with("hero/x.jpg")
    assert (await client.get(ADMIN, headers=admin_headers)).json() == []


async def test_delete_not_found(client, admin_headers):
    assert (await client.delete(f"{ADMIN}/{uuid.uuid4()}", headers=admin_headers)).status_code == 404


async def test_delete_keeps_row_when_r2_fails(client, admin_headers, make_hero, storage: MagicMock):
    hero = await make_hero(order=1)
    storage.delete_image.side_effect = StorageError("Failed to delete image from storage")
    resp = await client.delete(f"{ADMIN}/{hero.id}", headers=admin_headers)
    assert resp.status_code == 502
    assert len((await client.get(ADMIN, headers=admin_headers)).json()) == 1


def test_local_storage_roundtrip_and_traversal(tmp_path):
    import pytest

    from app.core.config import Settings
    from app.core.exceptions import StorageError
    from app.services.storage_service import LocalStorageService

    settings = Settings(
        database_url="sqlite+aiosqlite://",
        storage_backend="local",
        local_media_dir=str(tmp_path),
        local_media_url="http://x/media/",
    )
    storage = LocalStorageService(settings)
    url = storage.upload_image("hero/a.png", b"data", "image/png")
    assert url == "http://x/media/hero/a.png"
    assert (tmp_path / "hero" / "a.png").read_bytes() == b"data"
    storage.delete_image("hero/a.png")
    assert not (tmp_path / "hero" / "a.png").exists()
    with pytest.raises(StorageError):
        storage.upload_image("../evil.png", b"x", "image/png")
