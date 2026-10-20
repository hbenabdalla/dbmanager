"""Replicas starting together (sprint 0.15.4): several processes run the start-up work against an
empty database at the same time. The advisory locks serialise them, so the schema is migrated
once, the bootstrap administrator exists once, and every process starts."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys

import pytest
from sqlalchemy.engine import make_url

from tests.migrations.test_migrations import BACKEND, URL, _exec, _pg_available

DB = "dbm_concurrent_start_test"
pytestmark = pytest.mark.skipif(not _pg_available(), reason="test PostgreSQL (DBM_TEST_DATABASE_URL) not available")
START = "import asyncio\nfrom app.main import startup_tasks\nasyncio.run(startup_tasks())\n"


def test_replicas_starting_together_migrate_and_bootstrap_once() -> None:
    asyncio.run(_exec(f"DROP DATABASE IF EXISTS {DB}"))
    asyncio.run(_exec(f"CREATE DATABASE {DB}"))
    url = make_url(URL).set(database=DB).render_as_string(hide_password=False)
    env = {**os.environ, "DBM_DATABASE_URL": url, "DBM_RUN_MIGRATIONS_ON_START": "true", "DBM_LOG_LEVEL": "WARNING"}
    try:
        procs = [
            subprocess.Popen(  # noqa: S603 - our interpreter, fixed script
                [sys.executable, "-c", START], cwd=BACKEND, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
            )
            for _ in range(3)
        ]
        outs = [p.communicate(timeout=240)[0].decode(errors="replace") for p in procs]
        assert [p.returncode for p in procs] == [0, 0, 0], "\n---\n".join(o[-2000:] for o in outs)

        async def check() -> tuple[str, int, int]:
            import asyncpg

            u = make_url(url)
            conn = await asyncpg.connect(
                host=u.host, port=u.port or 5432, user=u.username, password=u.password, database=DB
            )
            try:
                head = await conn.fetchval("SELECT version_num FROM alembic_version")
                admins = await conn.fetchval("SELECT count(*) FROM users WHERE username = 'admin'")
                rows = await conn.fetchval("SELECT count(*) FROM alembic_version")
                return head, admins, rows
            finally:
                await conn.close()

        head, admins, rows = asyncio.run(check())
        heads = sorted(p.stem.split("_")[0] for p in (BACKEND / "alembic" / "versions").glob("0*.py"))
        assert head == heads[-1] and rows == 1 and admins == 1
    finally:
        asyncio.run(_exec(f"DROP DATABASE IF EXISTS {DB} WITH (FORCE)"))
