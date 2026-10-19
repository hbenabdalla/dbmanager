"""Database backup providers against real engines (milestone 0.12).

Each test creates a small database (or schema), backs it up with the engine's provider, verifies
the backup with the engine's own check, restores it under a NEW name and compares row counts,
then removes everything it created. Only the disposable integration containers started by
``scripts/integration-test.(sh|ps1)`` are used — never point these variables at a real system.

Connection details come from the same DBM_IT_<ENGINE>_* variables as the adapter tests; the
privileged account (``DBM_IT_<ENGINE>_USER`` / ``_PASSWORD``) runs the backups.
"""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.adapters.base import ConnectionSpec
from app.adapters.registry import create_adapter
from app.backup import engines

pytestmark = [pytest.mark.real_database, pytest.mark.asyncio(loop_scope="session")]


def _env(prefix: str, key: str, default: str = "") -> str:
    return os.environ.get(f"DBM_IT_{prefix}_{key}", default)


def _server(engine: str, prefix: str, port: str, **kw: Any) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        name=f"it-{engine}",
        engine=engine,
        host=_env(prefix, "HOST"),
        port=int(_env(prefix, "PORT", port)),
        tls_mode=kw.pop("tls_mode", "disable"),
        tls_ca_pem=None,
        deployment="self-managed",
        is_demo=False,
        version=None,
        options=kw.pop("options", {}),
        **kw,
    )


def _ctx(
    server: SimpleNamespace,
    user: str,
    password: str,
    database: str,
    workdir: Path,
    spec_kw: dict[str, Any] | None = None,
    **options: Any,
) -> engines.Context:
    async def adapter() -> Any:
        spec = ConnectionSpec(
            host=server.host,
            port=server.port,
            username=user,
            password=password,
            tls_mode=server.tls_mode,
            statement_timeout=600,
            options=dict(server.options),
            **(spec_kw or {}),
        )
        return create_adapter(server.engine, spec)

    return engines.Context(
        server=server,
        username=user,
        password=password,
        database=database,
        workdir=workdir,
        options=options,
        adapter=adapter,
    )


async def _round_trip(
    provider: engines.DatabaseBackupProvider, ctx: engines.Context, restore_ctx: engines.Context, target: str
) -> dict[str, Any]:
    out = await provider.backup(ctx)
    record = SimpleNamespace(location=out.server_location, database_name=ctx.database, engine=provider.engine)
    detail = await provider.verify(ctx, record, out.file)
    assert detail.get("level") == "engine", detail
    restored = await provider.restore(restore_ctx, record, out.file, target)
    with pytest.raises(engines.BackupProviderError, match="already exists"):
        await provider.restore(restore_ctx, record, out.file, target)
    return {"outcome": out, "verify": detail, "restore": restored}


# ---- PostgreSQL ------------------------------------------------------------------------------
@pytest.mark.engine("postgresql")
@pytest.mark.capability("backup", "restore")
@pytest.mark.skipif(not _env("PG", "HOST"), reason="DBM_IT_PG_HOST not set")
@pytest.mark.skipif(not shutil.which("pg_dump"), reason="pg_dump not installed")
async def test_postgresql_pg_dump_round_trip(tmp_path: Path) -> None:
    import asyncpg

    user, pw = _env("PG", "USER", "postgres"), _env("PG", "PASSWORD")
    srv = _server("postgresql", "PG", "5432")
    src, dst = f"dbm_it_bk_{uuid.uuid4().hex[:6]}", f"dbm_it_bkr_{uuid.uuid4().hex[:6]}"
    conn = await asyncpg.connect(host=srv.host, port=srv.port, user=user, password=pw, database="postgres")
    try:
        await conn.execute(f'CREATE DATABASE "{src}"')
        c2 = await asyncpg.connect(host=srv.host, port=srv.port, user=user, password=pw, database=src)
        await c2.execute("CREATE TABLE t(id int primary key); INSERT INTO t SELECT generate_series(1, 1000)")
        await c2.close()
        p = engines.provider("postgresql")
        res = await _round_trip(p, _ctx(srv, user, pw, src, tmp_path), _ctx(srv, user, pw, dst, tmp_path), dst)
        assert res["outcome"].file and res["verify"]["tables"] == 1
        c3 = await asyncpg.connect(host=srv.host, port=srv.port, user=user, password=pw, database=dst)
        assert await c3.fetchval("SELECT count(*) FROM t") == 1000
        await c3.close()
    finally:
        for d in (src, dst):
            await conn.execute(f'DROP DATABASE IF EXISTS "{d}" WITH (FORCE)')
        await conn.close()


# ---- MySQL / MariaDB ---------------------------------------------------------------------------
@pytest.mark.parametrize("flavor", ["mysql", "mariadb"])
@pytest.mark.capability("backup", "restore")
async def test_mysql_family_dump_round_trip(flavor: str, tmp_path: Path) -> None:
    prefix = flavor.upper()
    if not _env(prefix, "HOST"):
        pytest.skip(f"DBM_IT_{prefix}_HOST not set")
    if not (shutil.which("mysqldump") or shutil.which("mariadb-dump")):
        pytest.skip("no mysqldump / mariadb-dump installed")
    import pymysql

    user, pw = _env(prefix, "USER", "root"), _env(prefix, "PASSWORD")
    srv = _server(flavor, prefix, "3306", tls_mode="prefer")
    src, dst = f"dbm_it_bk_{uuid.uuid4().hex[:6]}", f"dbm_it_bkr_{uuid.uuid4().hex[:6]}"
    c = pymysql.connect(host=srv.host, port=srv.port, user=user, password=pw, autocommit=True)
    try:
        with c.cursor() as cur:
            cur.execute(f"CREATE DATABASE `{src}` CHARACTER SET utf8mb4")
            cur.execute(f"CREATE TABLE `{src}`.t (id INT PRIMARY KEY, txt VARCHAR(20)) ENGINE=InnoDB")
            cur.execute(f"INSERT INTO `{src}`.t VALUES " + ",".join(f"({i}, 'é{i}')" for i in range(1, 301)))
            cur.execute(f"USE `{src}`")
            cur.execute("CREATE TRIGGER trg BEFORE INSERT ON t FOR EACH ROW SET NEW.txt = UPPER(NEW.txt)")
        p = engines.provider(flavor)
        await _round_trip(p, _ctx(srv, user, pw, src, tmp_path), _ctx(srv, user, pw, dst, tmp_path), dst)
        # a definition qualified with the source name would land in the source database: refused
        with c.cursor() as cur:
            cur.execute(f"CREATE TRIGGER `{src}`.trg2 BEFORE UPDATE ON `{src}`.t FOR EACH ROW SET NEW.txt = NEW.txt")
        out = await p.backup(_ctx(srv, user, pw, src, tmp_path))
        rec = SimpleNamespace(location=None, database_name=src, engine=flavor)
        with pytest.raises(engines.BackupProviderError, match="qualified with the source database"):
            await p.restore(_ctx(srv, user, pw, dst + "x", tmp_path), rec, out.file, dst + "x")
        with c.cursor() as cur:
            cur.execute(f"SELECT count(*), max(txt) FROM `{dst}`.t")
            n, mx = cur.fetchone()
            assert n == 300 and mx.startswith("é")
            cur.execute("SELECT count(*) FROM information_schema.triggers WHERE trigger_schema = %s", (dst,))
            assert cur.fetchone()[0] == 1  # triggers come back too
    finally:
        with c.cursor() as cur:
            for d in (src, dst):
                cur.execute(f"DROP DATABASE IF EXISTS `{d}`")
        c.close()


# ---- SQL Server -------------------------------------------------------------------------------
@pytest.mark.engine("mssql")
@pytest.mark.capability("backup", "restore")
@pytest.mark.skipif(not _env("MSSQL", "HOST"), reason="DBM_IT_MSSQL_HOST not set")
async def test_sqlserver_native_backup_verify_restore(tmp_path: Path) -> None:
    import pyodbc

    from app.adapters import drivers

    user, pw = _env("MSSQL", "USER", "sa"), _env("MSSQL", "PASSWORD")
    srv = _server("mssql", "MSSQL", "1433", tls_mode="require", options={"trust_server_certificate": True})
    src, dst = f"dbm_it_bk_{uuid.uuid4().hex[:6]}", f"dbm_it_bkr_{uuid.uuid4().hex[:6]}"
    cs = (
        f"DRIVER={{{drivers.odbc_driver_name()}}};SERVER={srv.host},{srv.port};UID={user};PWD={pw};"
        "Encrypt=yes;TrustServerCertificate=yes"
    )
    conn = pyodbc.connect(cs, autocommit=True)
    try:
        cur = conn.cursor()
        cur.execute(f"CREATE DATABASE [{src}]")
        cur.execute(f"CREATE TABLE [{src}].dbo.t (id int primary key)")
        cur.execute(
            f"INSERT INTO [{src}].dbo.t SELECT TOP 500 ROW_NUMBER() OVER (ORDER BY (SELECT 1)) FROM sys.all_objects"
        )
        p = engines.provider("mssql")
        ctx = _ctx(srv, user, pw, src, tmp_path, tag=uuid.uuid4().hex[:8])
        res = await _round_trip(p, ctx, _ctx(srv, user, pw, dst, tmp_path), dst)
        assert res["outcome"].server_location.endswith(".bak") and res["outcome"].details["checksums"] is True
        cur.execute(f"SELECT count(*) FROM [{dst}].dbo.t")
        assert cur.fetchone()[0] == 500
    finally:
        cur = conn.cursor()
        for d in (src, dst):
            cur.execute(
                f"IF DB_ID(N'{d}') IS NOT NULL BEGIN ALTER DATABASE [{d}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE; DROP DATABASE [{d}]; END"
            )
        conn.close()


# ---- Oracle ------------------------------------------------------------------------------------
@pytest.mark.engine("oracle")
@pytest.mark.capability("backup", "restore")
@pytest.mark.skipif(not _env("ORACLE", "HOST"), reason="DBM_IT_ORACLE_HOST not set")
async def test_oracle_datapump_export_verify_import(tmp_path: Path) -> None:
    import secrets

    import oracledb

    user, pw = _env("ORACLE", "USER", "system"), _env("ORACLE", "PASSWORD")
    service = _env("ORACLE", "SERVICE", "XEPDB1")
    srv = _server("oracle", "ORACLE", "1521", options={"service_name": service})
    tag = uuid.uuid4().hex[:6].upper()
    src, dst = f"DBM_IT_BK{tag}", f"DBM_IT_BKR{tag}"
    conn = oracledb.connect(user=user, password=pw, dsn=f"{srv.host}:{srv.port}/{service}")
    cur = conn.cursor()
    try:
        spw = "It" + secrets.token_hex(8)
        cur.execute(f'CREATE USER {src} IDENTIFIED BY "{spw}" QUOTA UNLIMITED ON USERS')
        cur.execute(f"CREATE TABLE {src}.T (ID NUMBER PRIMARY KEY)")
        cur.execute(f"INSERT INTO {src}.T SELECT LEVEL FROM DUAL CONNECT BY LEVEL <= 400")
        conn.commit()
        p = engines.provider("oracle")
        res = await _round_trip(
            p, _ctx(srv, user, pw, src, tmp_path, tag=tag), _ctx(srv, user, pw, dst, tmp_path, tag=tag + "R"), dst
        )
        assert res["outcome"].server_location.startswith("DATA_PUMP_DIR:")
        cur.execute(f"SELECT COUNT(*) FROM {dst}.T")
        assert cur.fetchone()[0] == 400
    finally:
        for u in (src, dst):
            try:
                cur.execute(f"DROP USER {u} CASCADE")
            except oracledb.DatabaseError:
                pass
        conn.close()


# ---- Db2 LUW ------------------------------------------------------------------------------------
@pytest.mark.engine("db2")
@pytest.mark.capability("backup")
@pytest.mark.skipif(not _env("DB2", "HOST"), reason="DBM_IT_DB2_HOST not set")
async def test_db2_online_backup_or_honest_configuration_requirement(tmp_path: Path) -> None:
    user, pw = _env("DB2", "USER", "db2inst1"), _env("DB2", "PASSWORD")
    db = _env("DB2", "DB", "TESTDB")
    srv = _server("db2", "DB2", "50000")
    p = engines.provider("db2")
    backup_dir = _env("DB2", "BACKUP_DIR", "/tmp")  # noqa: S108 - a directory inside the Db2 container
    ctx = _ctx(srv, user, pw, db, tmp_path, spec_kw={"database": db}, server_directory=backup_dir)
    try:
        out = await p.backup(ctx)
    except engines.BackupProviderError as exc:
        if "REQUIRES_CONFIGURATION" in str(exc):
            pytest.skip(f"Db2 container runs with circular logging: {exc}")
        raise
    record = SimpleNamespace(location=out.server_location, database_name=db, engine="db2")
    detail = await p.verify(ctx, record, None)
    assert detail["level"] == "engine"
    assert "RESTORE DATABASE" in (p.restore_command(record, "DBMRST") or "")
