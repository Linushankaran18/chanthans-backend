import os

from cryptography.fernet import Fernet

os.environ["ENVIRONMENT"] = "test"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["JWT_SECRET"] = "test-secret-test-secret-test-secret-123456"
os.environ["R2_PUBLIC_URL"] = "https://cdn.test"
os.environ["MAX_UPLOAD_SIZE_MB"] = "1"
os.environ["APP_TIMEZONE"] = "America/Toronto"
os.environ["FRONTEND_URL"] = "http://localhost:5173"
os.environ["GOOGLE_CLIENT_ID"] = "test-client-id.apps.example"
os.environ["GOOGLE_CLIENT_SECRET"] = "test-client-secret-value"
os.environ["GOOGLE_REDIRECT_URI"] = "http://localhost:8000/api/v1/integrations/google-calendar/callback"
os.environ["GOOGLE_CALENDAR_DEFAULT_ID"] = "primary"
# Throwaway key generated per test run; never a real secret.
os.environ["TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()

import io
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from uuid import UUID

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.core.crypto import TokenCipher
from app.core.database import Base, get_session
from app.core.security import hash_password
from app.dependencies.google import get_google_client
from app.main import app
from app.models.google_calendar_integration import GoogleCalendarIntegration
from app.models.hero_image import HeroImage
from app.models.user import User
from app.services.storage_service import get_storage_service
from tests.fakes import FakeGoogleClient

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
def google() -> FakeGoogleClient:
    return FakeGoogleClient(get_settings())


@pytest.fixture
def storage() -> MagicMock:
    mock = MagicMock()
    mock.upload_image.side_effect = lambda key, data, ct: f"https://cdn.test/{key}"
    return mock


@pytest_asyncio.fixture
async def client(sessionmaker, storage, google) -> AsyncIterator[AsyncClient]:
    async def _session() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as session:
            yield session

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_storage_service] = lambda: storage
    app.dependency_overrides[get_google_client] = lambda: google
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


@pytest_asyncio.fixture
async def connect_google(client, sessionmaker, admin_headers):
    """Seeds a connected Google Calendar integration (tokens encrypted like production)."""

    async def _connect(expired: bool = False) -> None:
        cipher = TokenCipher(get_settings().token_encryption_key)
        me = (await client.get("/api/v1/auth/me", headers=admin_headers)).json()
        expiry = datetime.now(timezone.utc) + timedelta(hours=-1 if expired else 1)
        async with sessionmaker() as session:
            session.add(
                GoogleCalendarIntegration(
                    user_id=UUID(me["id"]),
                    google_email="studio@example.com",
                    calendar_id="primary",
                    access_token_encrypted=cipher.encrypt("access-1"),
                    refresh_token_encrypted=cipher.encrypt("refresh-1"),
                    token_expiry=expiry,
                    scope="openid email",
                    is_connected=True,
                )
            )
            await session.commit()

    return _connect


def booking_payload(**overrides) -> dict:
    payload = {
        "customer_name": "Priya Nair",
        "customer_email": "priya@example.com",
        "customer_phone": "(416) 555-0134",
        "service_type": "Wedding",
        "event_date": "2026-11-15",
        "start_time": "09:00",
        "end_time": "17:00",
        "location": "Toronto",
        "total_amount": 2500,
        "deposit_amount": 500,
    }
    payload.update(overrides)
    return payload


@pytest_asyncio.fixture
async def make_booking(client, admin_headers):
    async def _make(**overrides) -> dict:
        resp = await client.post("/api/v1/admin/bookings", json=booking_payload(**overrides), headers=admin_headers)
        assert resp.status_code == 201, resp.text
        return resp.json()

    return _make
