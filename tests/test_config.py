import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_database_url_normalized():
    s = Settings(database_url="postgresql://u:p@h:6543/db")
    assert s.database_url.startswith("postgresql+asyncpg://")


def test_origins_parsed():
    s = Settings(database_url="sqlite+aiosqlite://", allowed_origins="http://a.com, http://b.com")
    assert s.cors_origins == ["http://a.com", "http://b.com"]


def test_production_rejects_weak_secret():
    with pytest.raises(ValidationError):
        Settings(database_url="sqlite+aiosqlite://", environment="production", jwt_secret="dev-insecure-secret-change-me")


def test_production_rejects_wildcard():
    with pytest.raises(ValidationError):
        Settings(database_url="sqlite+aiosqlite://", environment="production", jwt_secret="x" * 40, allowed_origins="*")


def test_business_address_accepts_literal_backslash_n_for_line_breaks(monkeypatch):
    from app.core.config import Settings

    settings = Settings(business_address="9699 Jane St\\nVaughan, ON L6A 3R4\\nCanada")  # type: ignore[call-arg]
    assert settings.business_address.splitlines() == ["9699 Jane St", "Vaughan, ON L6A 3R4", "Canada"]
