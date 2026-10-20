"""Async SQLAlchemy engine/session management for the application database."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.tlsclient import database_connect_args

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None
_limiter_engine: AsyncEngine | None = None


def get_engine() -> AsyncEngine:
    global _engine, _sessionmaker
    if _engine is None:
        s = get_settings()
        url = s.database_url.get_secret_value()
        if not url:
            raise RuntimeError("DBM_DATABASE_URL is not configured")
        _engine = create_async_engine(
            url,
            pool_size=s.database_pool_size,
            max_overflow=s.database_max_overflow,
            pool_pre_ping=True,
            pool_recycle=1800,
            connect_args=database_connect_args("dbmanager", s),
        )
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False, autoflush=False)
    return _engine


def get_limiter_engine() -> AsyncEngine:
    """Small separate pool for rate-limit counters (short autocommit statements)."""
    global _limiter_engine
    if _limiter_engine is None:
        s = get_settings()
        _limiter_engine = create_async_engine(
            s.database_url.get_secret_value(),
            pool_size=s.rate_limit_pool_size,
            max_overflow=s.rate_limit_pool_size,
            pool_timeout=5,
            pool_pre_ping=True,
            pool_recycle=1800,
            connect_args=database_connect_args("dbmanager-ratelimit", s),
        )
    return _limiter_engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    get_engine()
    assert _sessionmaker is not None
    return _sessionmaker


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Unit of work for background code: commit on success, rollback on error."""
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    global _engine, _sessionmaker, _limiter_engine
    if _engine is not None:
        await _engine.dispose()
    if _limiter_engine is not None:
        await _limiter_engine.dispose()
    _engine = None
    _sessionmaker = None
    _limiter_engine = None
