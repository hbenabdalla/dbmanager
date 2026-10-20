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


#: serialises migrations of concurrently starting replicas (and a migrate Job): the second one
#: waits, then finds the schema at head and has nothing to do
MIGRATION_LOCK_ID = 0x44424D_4D4947  # "DBM" "MIG"


async def run_migrations_online() -> None:
    from sqlalchemy import text

    from app.core.tlsclient import database_connect_args

    engine = create_async_engine(_url(), connect_args=database_connect_args("dbmanager-migrate"))
    async with engine.connect() as conn:
        await conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": MIGRATION_LOCK_ID})
        await conn.commit()
        try:
            await conn.run_sync(_do_run)
        finally:
            await conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": MIGRATION_LOCK_ID})
            await conn.commit()
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    connection = context.config.attributes.get("connection")
    if connection is not None:
        _do_run(connection)
    else:
        asyncio.run(run_migrations_online())
