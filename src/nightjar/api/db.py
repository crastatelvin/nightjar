from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from nightjar.api.settings import get_settings


class Base(DeclarativeBase):
    pass


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None
_database_url: str | None = None
_lock_engine: AsyncEngine | None = None


def configure(database_url: str, *, echo: bool = False) -> None:
    """Reconfigure the process-wide engine (tests and multi-app embedding)."""
    global _engine, _session_factory, _database_url, _lock_engine
    _database_url = database_url
    _engine = create_async_engine(database_url, echo=echo, pool_pre_ping=True)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    _lock_engine = None


async def dispose() -> None:
    """Close the process-wide pools from the event loop that created them."""
    global _engine, _session_factory, _lock_engine
    for engine in (_lock_engine, _engine):
        if engine is not None:
            await engine.dispose()
    _engine = None
    _session_factory = None
    _lock_engine = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            echo=settings.db_echo,
            pool_pre_ping=True,
        )
    return _engine


def get_lock_engine() -> AsyncEngine:
    """A connection-per-lock engine, separate from the request pool.

    A session-level advisory lock lives on its connection for the whole critical
    section, which can include model calls and provider round trips. Using the
    request pool would starve ordinary queries behind held locks, so locks get
    their own ``NullPool`` engine.
    """
    global _lock_engine
    if _lock_engine is None:
        url = _database_url or get_settings().database_url
        _lock_engine = create_async_engine(url, poolclass=NullPool, pool_pre_ping=True)
    return _lock_engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(), expire_on_commit=False, class_=AsyncSession
        )
    return _session_factory


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_session_factory()() as session:
        yield session
