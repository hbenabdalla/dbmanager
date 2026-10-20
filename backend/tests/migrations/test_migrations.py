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


# ---- existing installation -> latest ----------------------------------------------------------
#: tables seeded at an older revision (in FK order); a table missing at that revision is skipped
#: — users and RBAC, servers and credential references, alerting, findings, backup configuration
#: and settings must all survive an upgrade
SEEDED = [
    "organizations",
    "environments",
    "users",
    "roles",
    "role_assignments",
    "servers",
    "credentials",
    "alert_rules",
    "notification_channels",
    "recommendations",
    "security_findings",
    "backup_targets",
    "backup_policies",
    "app_settings",
]


async def _seed_generic(tables: list[str]) -> dict[str, str]:
    """Insert one row per table using only the columns that exist at the current revision:
    required columns get a type-appropriate value, foreign keys point at the seeded parent row."""
    import uuid as _uuid

    import asyncpg

    u = make_url(_scratch_url())
    conn = await asyncpg.connect(
        host=u.host, port=u.port or 5432, user=u.username, password=u.password, database=SCRATCH
    )
    ids: dict[str, str] = {}
    try:
        for table in tables:
            cols = await conn.fetch(
                "SELECT column_name, data_type, is_nullable, column_default, character_maximum_length AS maxlen "
                "FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=$1 ORDER BY ordinal_position",
                table,
            )
            if not cols:
                continue
            fks = {
                r["col"]: r["ref"]
                for r in await conn.fetch(
                    "SELECT kcu.column_name AS col, ccu.table_name AS ref FROM information_schema.table_constraints tc "
                    "JOIN information_schema.key_column_usage kcu ON kcu.constraint_name = tc.constraint_name "
                    "JOIN information_schema.constraint_column_usage ccu ON ccu.constraint_name = tc.constraint_name "
                    "WHERE tc.table_name=$1 AND tc.constraint_type='FOREIGN KEY'",
                    table,
                )
            }
            names, values = [], []
            for c in cols:
                name, typ = c["column_name"], c["data_type"]
                if name in fks:
                    if fks[name] in ids:
                        names.append(name)
                        values.append(ids[fks[name]])
                    continue
                if c["is_nullable"] == "YES" or c["column_default"] is not None and name != "id":
                    continue
                names.append(name)
                if name == "id" and typ == "uuid":
                    ids[table] = str(_uuid.uuid4())
                    values.append(ids[table])
                elif typ == "uuid":
                    values.append(str(_uuid.uuid4()))
                elif typ in ("character varying", "text"):
                    v = {"engine": "postgresql", "kind": "metric", "severity": "high", "tier": "test"}.get(
                        name, f"seed-{table}-{name}"
                    )
                    values.append(v[: c["maxlen"]] if c["maxlen"] else v)
                elif typ in ("integer", "bigint", "smallint"):
                    values.append(5432 if name == "port" else 1)
                elif typ == "double precision":
                    values.append(1.0)
                elif typ == "boolean":
                    values.append(False)
                elif typ.startswith("timestamp"):
                    from datetime import UTC, datetime

                    values.append(datetime.now(UTC))
                elif typ == "jsonb":
                    values.append("[]" if name.endswith("_ids") or name.endswith("states") else "{}")
                else:
                    raise AssertionError(f"no seed value for {table}.{name} ({typ})")
            ph = ", ".join(f"${i + 1}" + ("::jsonb" if v in ("[]", "{}") else "") for i, v in enumerate(values))
            await conn.execute(f'INSERT INTO "{table}" ({", ".join(names)}) VALUES ({ph})', *values)  # noqa: S608
            if table not in ids:
                ids[table] = "row"
        return ids
    finally:
        await conn.close()


async def _row_ids(tables: list[str]) -> dict[str, list[str]]:
    import asyncpg

    u = make_url(_scratch_url())
    conn = await asyncpg.connect(
        host=u.host, port=u.port or 5432, user=u.username, password=u.password, database=SCRATCH
    )
    try:
        # first column = primary key (``id``, or ``key`` for app_settings)
        return {t: sorted(str(r[0]) for r in await conn.fetch(f'SELECT * FROM "{t}"')) for t in tables}  # noqa: S608
    finally:
        await conn.close()


#: schema head of every published release (tags v0.12.0, v0.13.0): upgrades are tested from each
#: of them, not from "the revision before head" (a release may add several migrations)
RELEASES = {"0.12.0": "0010", "0.13.0": "0011"}
LATEST_RELEASE = "0.13.0"


@pytest.mark.parametrize("start", ["first", *RELEASES])
def test_existing_installation_upgrades_to_head_with_its_data(start: str) -> None:
    """An installation at an older revision, with data in it, upgrades to the latest schema and
    keeps every row (``first`` = the initial schema; otherwise the schema of that release)."""
    revs = revisions()
    rev = revs[0] if start == "first" else RELEASES[start]
    assert rev in revs
    alembic("downgrade", "base")
    alembic("upgrade", rev)
    seeded = asyncio.run(_seed_generic(SEEDED))
    present = [t for t in SEEDED if t in seeded]
    assert "servers" in present and "users" in present
    if start == LATEST_RELEASE:  # the last release has every one of these tables
        assert present == SEEDED
    before = asyncio.run(_row_ids(present))
    alembic("upgrade", "head")
    assert revs[-1] in alembic("current")
    assert asyncio.run(_row_ids(present)) == before
    # 0013: servers registered without a management mode keep their previous behaviour
    modes = asyncio.run(_management_modes())
    assert modes and all(m == "administration" for m in modes), modes


async def _management_modes() -> list[str | None]:
    import asyncpg

    u = make_url(_scratch_url())
    conn = await asyncpg.connect(
        host=u.host, port=u.port or 5432, user=u.username, password=u.password, database=SCRATCH
    )
    try:
        return [r[0] for r in await conn.fetch("SELECT options->>'management_mode' FROM servers")]
    finally:
        await conn.close()
