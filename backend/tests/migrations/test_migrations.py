"""Schema migrations: every revision upgrades and downgrades cleanly, step by step, and the head
revision matches the SQLAlchemy models exactly (so an upgrade of an existing installation yields
the same schema as a fresh one).

Uses a scratch database next to DBM_TEST_DATABASE_URL (created and dropped by the test).
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url

BACKEND = Path(__file__).resolve().parents[2]
URL = os.environ.get("DBM_TEST_DATABASE_URL", "")
SCRATCH = os.environ.get("DBM_MIGRATION_TEST_DB", "dbm_migration_test")


def _admin_url() -> str:
    return make_url(URL).set(database="postgres").render_as_string(hide_password=False)


def _scratch_url() -> str:
    return make_url(URL).set(database=SCRATCH).render_as_string(hide_password=False)


async def _exec(sql: str) -> None:
    import asyncpg

    u = make_url(_admin_url())
    conn = await asyncpg.connect(
        host=u.host, port=u.port or 5432, user=u.username, password=u.password, database="postgres"
    )
    try:
        await conn.execute(sql)
    finally:
        await conn.close()


def _pg_available() -> bool:
    if not URL:
        return False
    try:
        asyncio.run(_exec("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _pg_available(), reason="test PostgreSQL (DBM_TEST_DATABASE_URL) not available")


def alembic(*args: str) -> str:
    env = {**os.environ, "DBM_DATABASE_URL": _scratch_url()}
    res = subprocess.run(  # noqa: S603 - fixed argv, test only
        [sys.executable, "-m", "alembic", *args], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=180
    )
    assert res.returncode == 0, f"alembic {' '.join(args)} failed:\n{res.stdout}\n{res.stderr}"
    return res.stdout + res.stderr


def revisions() -> list[str]:
    out = alembic("history")
    # lines look like "0002 -> 0003 (head), message" / "<base> -> 0001, message"
    revs = [line.split(" -> ")[1].split(",")[0].split(" ")[0] for line in out.splitlines() if " -> " in line]
    return list(reversed(revs))


@pytest.fixture(scope="module", autouse=True)
def scratch_db():  # type: ignore[no-untyped-def]
    asyncio.run(_exec(f"DROP DATABASE IF EXISTS {SCRATCH} WITH (FORCE)"))
    asyncio.run(_exec(f"CREATE DATABASE {SCRATCH}"))
    yield
    asyncio.run(_exec(f"DROP DATABASE IF EXISTS {SCRATCH} WITH (FORCE)"))


def test_step_by_step_upgrade_and_full_downgrade() -> None:
    revs = revisions()
    assert revs, "no migrations found"
    for rev in revs:  # an installation upgrading one release at a time
        alembic("upgrade", rev)
    assert revs[-1] in alembic("current")
    alembic("downgrade", "base")
    alembic("upgrade", "head")
    assert revs[-1] in alembic("current")


def test_head_schema_matches_models() -> None:
    alembic("upgrade", "head")

    async def diff() -> list:  # type: ignore[type-arg]
        from alembic.autogenerate import compare_metadata
        from alembic.migration import MigrationContext
        from sqlalchemy.ext.asyncio import create_async_engine

        import app.models  # noqa: F401 - register models
        from app.db.base import Base

        engine = create_async_engine(_scratch_url())
        try:
            async with engine.connect() as conn:
                return await conn.run_sync(
                    lambda c: compare_metadata(
                        MigrationContext.configure(c, opts={"compare_type": True}), Base.metadata
                    )
                )
        finally:
            await engine.dispose()

    changes = asyncio.run(diff())
    assert changes == [], f"models and migrations disagree (write a migration): {changes}"
