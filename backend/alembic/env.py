"""Alembic environment (async). The URL comes from DBM_DATABASE_URL."""

from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import create_async_engine

import app.models  # noqa: F401  - register models
from alembic import context
from app.core.config import get_settings
from app.db.base import Base

target_metadata = Base.metadata


def _url() -> str:
    url = get_settings().database_url.get_secret_value()
    if not url:
        raise RuntimeError("DBM_DATABASE_URL is not set")
    return url


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def _do_run(connection) -> None:  # type: ignore[no-untyped-def]
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(_url())
    async with engine.connect() as conn:
        await conn.run_sync(_do_run)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    connection = context.config.attributes.get("connection")
    if connection is not None:
        _do_run(connection)
    else:
        asyncio.run(run_migrations_online())
