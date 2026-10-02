import os

os.environ["ENVIRONMENT"] = "test"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["JWT_SECRET"] = "test-secret-test-secret-test-secret-123456"
os.environ["R2_PUBLIC_URL"] = "https://cdn.test"
os.environ["MAX_UPLOAD_SIZE_MB"] = "1"

import io
from collections.abc import AsyncIterator
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_session
from app.core.security import hash_password
from app.main import app
from app.models.hero_image import HeroImage
from app.models.user import User
from app.services.storage_service import get_storage_service

ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "correct-horse-battery"


@pytest_asyncio.fixture
async def sessionmaker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def storage() -> MagicMock:
    mock = MagicMock()
    mock.upload_image.side_effect = lambda key, data, ct: f"https://cdn.test/{key}"
    return mock


@pytest_asyncio.fixture
async def client(sessionmaker, storage) -> AsyncIterator[AsyncClient]:
    async def _session() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as session:
            yield session

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_storage_service] = lambda: storage
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def admin_headers(client, sessionmaker) -> dict[str, str]:
    async with sessionmaker() as session:
        session.add(User(email=ADMIN_EMAIL, password_hash=hash_password(ADMIN_PASSWORD), role="admin"))
        await session.commit()
    resp = await client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest_asyncio.fixture
async def make_hero(sessionmaker):
    async def _make(order: int = 0, active: bool = True, key: str | None = None) -> HeroImage:
        async with sessionmaker() as session:
            hero = HeroImage(
                image_key=key or f"hero/{order}.jpg",
                image_url=f"https://cdn.test/hero/{order}.jpg",
                title=f"t{order}",
                display_order=order,
                is_active=active,
            )
            session.add(hero)
            await session.commit()
            return hero

    return _make


def image_bytes(fmt: str = "PNG") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(buf, format=fmt)
    return buf.getvalue()
