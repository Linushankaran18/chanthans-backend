"""Async SQLAlchemy engine/session setup."""
from collections.abc import AsyncIterator
from functools import lru_cache
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

_NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=_NAMING)


def prepare_engine_args(url: str) -> tuple[str, dict]:
    """Return (url, engine kwargs), adapting asyncpg for Supabase's pgbouncer pooler.

    - ``sslmode`` is not understood by asyncpg, so it is translated to ``ssl``.
    - The asyncpg statement cache is disabled (pgbouncer transaction mode cannot
      handle prepared statements) and prepared statement names are made unique.
    """
    kwargs: dict = {"pool_pre_ping": True}
    if not url.startswith("postgresql+asyncpg://"):
        return url, kwargs

    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    sslmode = query.pop("sslmode", None)
    connect_args: dict = {
        "statement_cache_size": 0,
        "prepared_statement_cache_size": 0,
        "prepared_statement_name_func": lambda: f"__asyncpg_{uuid4()}__",
    }
    if sslmode and sslmode != "disable":
        connect_args["ssl"] = sslmode if sslmode in {"require", "prefer", "allow"} else "verify-full"
    kwargs["connect_args"] = connect_args
    clean = urlunsplit(parts._replace(query=urlencode(query)))
    return clean, kwargs


def build_engine(url: str) -> AsyncEngine:
    clean_url, kwargs = prepare_engine_args(url)
    return create_async_engine(clean_url, **kwargs)


@lru_cache
def get_engine() -> AsyncEngine:
    return build_engine(get_settings().database_url)


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a session; rolls back on error."""
    async with get_sessionmaker()() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
