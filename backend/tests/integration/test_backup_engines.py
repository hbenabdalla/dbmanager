"""Database backup providers against real engines (milestone 0.12).

Each test creates a small database (or schema), backs it up with the engine's provider, verifies
the backup with the engine's own check, restores it under a NEW name and compares row counts,
then removes everything it created. Only the disposable integration containers started by
``scripts/integration-test.(sh|ps1)`` are used — never point these variables at a real system.

Pattern per engine: create test data → backup → verify (engine check) → change and delete the
source data → restore under a new name → the restored data is the state at backup time, the
source keeps its changes → a second restore to the same name is refused.

Backups run as a **least-privilege backup account** created by the test with exactly the grants
documented in docs/backup-restore/engines/ (Db2 is the exception: BACKUP needs SYSADM, SYSCTRL or
SYSMAINT, an instance-level authority, so the instance owner runs it). The privileged account
(``DBM_IT_<ENGINE>_USER``) only prepares fixtures.
"""

from __future__ import annotations

import os
import secrets
import shutil
import uuid
from collections.abc import Awaitable, Callable
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
    provider: engines.DatabaseBackupProvider,
    ctx: engines.Context,
    restore_ctx: engines.Context,
    target: str,
    change_source: Callable[[], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    out = await provider.backup(ctx)
    record = SimpleNamespace(location=out.server_location, database_name=ctx.database, engine=provider.engine)
    detail = await provider.verify(ctx, record, out.file)
    assert detail.get("level") == "engine", detail
    if change_source is not None:
        await change_source()  # the restore must bring back the state at backup time
    restored = await provider.restore(restore_ctx, record, out.file, target)
    with pytest.raises(engines.BackupProviderError, match="already exists"):
        await provider.restore(restore_ctx, record, out.file, target)
    return {"outcome": out, "verify": detail, "restore": restored, "record": record}


def _pw() -> str:
    return "It" + secrets.token_hex(10) + "aA1!"


# ---- PostgreSQL ------------------------------------------------------------------------------
@pytest.mark.engine("postgresql")
@pytest.mark.capability("backup", "restore")
@pytest.mark.skipif(not _env("PG", "HOST"), reason="DBM_IT_PG_HOST not set")
@pytest.mark.skipif(not shutil.which("pg_dump"), reason="pg_dump not installed")
async def test_postgresql_pg_dump_round_trip(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import asyncpg

    user, pw = _env("PG", "USER", "postgres"), _env("PG", "PASSWORD")
    srv = _server("postgresql", "PG", "5432")
    tag = uuid.uuid4().hex[:6]
    src, dst, dst2, bk = f"dbm_it_bk_{tag}", f"dbm_it_bkr_{tag}", f"dbm_it_bks_{tag}", f"dbm_it_bkuser_{tag}"
    bk_pw = _pw()
    conn = await asyncpg.connect(host=srv.host, port=srv.port, user=user, password=pw, database="postgres")

    async def q(db: str, sql: str, fetch: bool = False) -> Any:
        c = await asyncpg.connect(host=srv.host, port=srv.port, user=user, password=pw, database=db)
        try:
            return await (c.fetchval(sql) if fetch else c.execute(sql))
        finally:
            await c.close()

    try:
        # least privilege: read every table (pg_read_all_data, PostgreSQL 14+) and create databases
        await conn.execute(f"CREATE ROLE {bk} LOGIN PASSWORD '{bk_pw}' CREATEDB")
        await conn.execute(f"GRANT pg_read_all_data TO {bk}")
        await conn.execute(f'CREATE DATABASE "{src}"')
        await q(
            src,
            "CREATE TABLE t(id int primary key, note text); INSERT INTO t SELECT g, 'n' || g FROM generate_series(1, 1000) g; "
            "CREATE INDEX t_note ON t(note); CREATE VIEW v AS SELECT id FROM t WHERE id % 2 = 0; "
            "CREATE FUNCTION f() RETURNS int LANGUAGE sql AS 'SELECT count(*)::int FROM t'",
        )

        async def change() -> None:
            await q(src, "DELETE FROM t WHERE id > 500; UPDATE t SET note = 'changed'")

        p = engines.provider("postgresql")
        res = await _round_trip(
            p, _ctx(srv, bk, bk_pw, src, tmp_path), _ctx(srv, bk, bk_pw, dst, tmp_path), dst, change
        )
        assert res["outcome"].file and res["verify"]["tables"] == 1
        assert await q(dst, "SELECT count(*) FROM t", True) == 1000
        assert await q(dst, "SELECT count(*) FROM t WHERE note = 'changed'", True) == 0
        assert await q(dst, "SELECT f()", True) == 1000 and await q(dst, "SELECT count(*) FROM v", True) == 500
        assert await q(src, "SELECT count(*) FROM t", True) == 500  # the source keeps its changes

        # restore path for a server older than the client tools (pg_restore 17 → PostgreSQL ≤ 16):
        # script rendered by pg_restore, unknown settings dropped, run with psql (ON_ERROR_STOP)
        real_major = engines._major
        monkeypatch.setattr(engines, "_major", lambda v: (real_major(v) or 0) + 1)
        out = await p.restore(_ctx(srv, bk, bk_pw, dst2, tmp_path), res["record"], res["outcome"].file, dst2)
        assert "psql" in out["tool"]
        assert await q(dst2, "SELECT count(*) FROM t", True) == 1000
    finally:
        for d in (src, dst, dst2):
            await conn.execute(f'DROP DATABASE IF EXISTS "{d}" WITH (FORCE)')
        await conn.execute(f"DROP ROLE IF EXISTS {bk}")
        await conn.close()


# ---- MySQL / MariaDB ---------------------------------------------------------------------------
#: documented least privilege for dumps (no PROCESS thanks to --no-tablespaces, no RELOAD/LOCK
#: thanks to --single-transaction) and for restoring into a new database
MYSQL_BACKUP_GRANTS = "SELECT, SHOW VIEW, TRIGGER, EVENT, LOCK TABLES"
MYSQL_RESTORE_GRANTS = (
    "CREATE, DROP, ALTER, INDEX, INSERT, UPDATE, DELETE, CREATE VIEW, CREATE ROUTINE, ALTER ROUTINE, REFERENCES"
)


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
    tag = uuid.uuid4().hex[:6]
    src, dst, dst3, bk = f"dbm_it_bk_{tag}", f"dbm_it_bkr_{tag}", f"dbm_it_bkq_{tag}", f"dbm_it_bkuser_{tag}"
    dst0 = f"dbm_it_bkz_{tag}"
    bk_pw = _pw()
    guarded = False
    c = pymysql.connect(host=srv.host, port=srv.port, user=user, password=pw, autocommit=True)

    def run(sql: str, *args: Any) -> Any:
        with c.cursor() as cur:
            cur.execute(sql, args or None)
            return cur.fetchall()

    try:
        run(f"CREATE USER '{bk}'@'%' IDENTIFIED BY '{bk_pw}'")
        run(f"GRANT {MYSQL_BACKUP_GRANTS} ON *.* TO '{bk}'@'%'")
        run(f"GRANT {MYSQL_RESTORE_GRANTS} ON `dbm\\_it\\_bk%`.* TO '{bk}'@'%'")
        run(f"CREATE DATABASE `{src}` CHARACTER SET utf8mb4")
        run(f"CREATE TABLE `{src}`.t (id INT PRIMARY KEY, txt VARCHAR(20)) ENGINE=InnoDB")
        run(f"INSERT INTO `{src}`.t VALUES " + ",".join(f"({i}, 'é{i}')" for i in range(1, 301)))
        run(f"USE `{src}`")
        # objects created by another account (definer root): restored by the backup account,
        # which needs no SUPER / SET_USER_ID because DB Manager drops the DEFINER clauses
        run("CREATE TRIGGER trg BEFORE INSERT ON t FOR EACH ROW SET NEW.txt = UPPER(NEW.txt)")
        run("CREATE VIEW v AS SELECT id FROM t WHERE id <= 10")
        run("CREATE PROCEDURE p_count() SELECT COUNT(*) FROM t")

        async def change() -> None:
            run(f"DELETE FROM `{src}`.t WHERE id > 100")

        p = engines.provider(flavor)
        log_bin, trusted = run("SELECT @@global.log_bin, @@global.log_bin_trust_function_creators")[0]
        guarded = bool(int(log_bin)) and not bool(int(trusted))
        if guarded:
            # Binary logging on (the MySQL 8 default) and routine creators not trusted: the
            # least-privilege account cannot create the trigger and procedure (error 1419). The
            # restore refuses before creating anything and names the fix ...
            first = await p.backup(_ctx(srv, bk, bk_pw, src, tmp_path))
            rec0 = SimpleNamespace(location=None, database_name=src, engine=flavor)
            with pytest.raises(engines.BackupProviderError, match="log_bin_trust_function_creators"):
                await p.restore(_ctx(srv, bk, bk_pw, dst0, tmp_path), rec0, first.file, dst0)
            assert run("SELECT count(*) FROM information_schema.schemata WHERE schema_name = %s", dst0)[0][0] == 0
            # ... which is the documented DBA prerequisite (docs/database-permissions/mysql.md)
            run("SET GLOBAL log_bin_trust_function_creators = ON")
        await _round_trip(p, _ctx(srv, bk, bk_pw, src, tmp_path), _ctx(srv, bk, bk_pw, dst, tmp_path), dst, change)
        n, mx = run(f"SELECT count(*), max(txt) FROM `{dst}`.t")[0]
        assert n == 300 and mx.startswith("é")  # state at backup time, utf8mb4 intact
        assert run(f"SELECT count(*) FROM `{src}`.t")[0][0] == 100
        assert run("SELECT count(*) FROM information_schema.triggers WHERE trigger_schema = %s", dst)[0][0] == 1
        assert run("SELECT count(*) FROM information_schema.views WHERE table_schema = %s", dst)[0][0] == 1
        assert run("SELECT count(*) FROM information_schema.routines WHERE routine_schema = %s", dst)[0][0] == 1

        # A trigger defined with the source schema name: MySQL stores it unqualified (the dump is
        # safe to restore under another name); MariaDB keeps the original text, so DB Manager
        # refuses the cross-name restore instead of changing the source database.
        run(f"CREATE TRIGGER `{src}`.trg2 BEFORE UPDATE ON `{src}`.t FOR EACH ROW SET NEW.txt = NEW.txt")
        out = await p.backup(_ctx(srv, bk, bk_pw, src, tmp_path))
        rec = SimpleNamespace(location=None, database_name=src, engine=flavor)
        if engines._mentions_schema(out.file, src):
            with pytest.raises(engines.BackupProviderError, match="qualified with the source database"):
                await p.restore(_ctx(srv, bk, bk_pw, dst3, tmp_path), rec, out.file, dst3)
        else:
            await p.restore(_ctx(srv, bk, bk_pw, dst3, tmp_path), rec, out.file, dst3)
            assert run("SELECT count(*) FROM information_schema.triggers WHERE trigger_schema = %s", dst3)[0][0] == 2
        assert run("SELECT count(*) FROM information_schema.triggers WHERE trigger_schema = %s", src)[0][0] == 2
    finally:
        if guarded:
            run("SET GLOBAL log_bin_trust_function_creators = OFF")
        for d in (src, dst, dst3, dst0):
            run(f"DROP DATABASE IF EXISTS `{d}`")
        run(f"DROP USER IF EXISTS '{bk}'@'%'")
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
    tag = uuid.uuid4().hex[:6]
    src, dst, bk = f"dbm_it_bk_{tag}", f"dbm_it_bkr_{tag}", f"dbm_it_bkuser_{tag}"
    bk_pw = _pw()
    cs = (
        f"DRIVER={{{drivers.odbc_driver_name()}}};SERVER={srv.host},{srv.port};UID={user};PWD={pw};"
        "Encrypt=yes;TrustServerCertificate=yes"
    )
    conn = pyodbc.connect(cs, autocommit=True)
    cur = conn.cursor()
    try:
        cur.execute(f"CREATE DATABASE [{src}]")
        cur.execute(f"CREATE TABLE [{src}].dbo.t (id int primary key)")
        cur.execute(
            f"INSERT INTO [{src}].dbo.t SELECT TOP 500 ROW_NUMBER() OVER (ORDER BY (SELECT 1)) FROM sys.all_objects"
        )
        # least privilege: db_backupoperator in the source database, dbcreator to restore as new
        cur.execute(f"CREATE LOGIN [{bk}] WITH PASSWORD = N'{bk_pw}', CHECK_POLICY = OFF")
        cur.execute(f"ALTER SERVER ROLE dbcreator ADD MEMBER [{bk}]")
        cur.execute(f"USE [{src}]; CREATE USER [{bk}] FOR LOGIN [{bk}]; ALTER ROLE db_backupoperator ADD MEMBER [{bk}]")
        cur.execute("USE master")

        async def change() -> None:
            cur.execute(f"DELETE FROM [{src}].dbo.t WHERE id > 100")

        p = engines.provider("mssql")
        ctx = _ctx(srv, bk, bk_pw, src, tmp_path, tag=uuid.uuid4().hex[:8])
        res = await _round_trip(p, ctx, _ctx(srv, bk, bk_pw, dst, tmp_path), dst, change)
        assert res["outcome"].server_location.endswith(".bak") and res["outcome"].details["checksums"] is True
        cur.execute(f"SELECT count(*) FROM [{dst}].dbo.t")
        assert cur.fetchone()[0] == 500
        cur.execute(f"SELECT count(*) FROM [{src}].dbo.t")
        assert cur.fetchone()[0] == 100
    finally:
        cur.execute("USE master")
        for d in (src, dst):
            cur.execute(
                f"IF DB_ID(N'{d}') IS NOT NULL BEGIN ALTER DATABASE [{d}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE; DROP DATABASE [{d}]; END"
            )
        cur.execute(f"IF SUSER_ID(N'{bk}') IS NOT NULL DROP LOGIN [{bk}]")
        conn.close()


# ---- Oracle ------------------------------------------------------------------------------------
@pytest.mark.engine("oracle")
@pytest.mark.capability("backup", "restore")
@pytest.mark.skipif(not _env("ORACLE", "HOST"), reason="DBM_IT_ORACLE_HOST not set")
async def test_oracle_datapump_export_verify_import(tmp_path: Path) -> None:
    import oracledb

    user, pw = _env("ORACLE", "USER", "system"), _env("ORACLE", "PASSWORD")
    service = _env("ORACLE", "SERVICE", "XEPDB1")
    srv = _server("oracle", "ORACLE", "1521", options={"service_name": service})
    tag = uuid.uuid4().hex[:6].upper()
    src, dst, bk = f"DBM_IT_BK{tag}", f"DBM_IT_BKR{tag}", f"DBM_IT_BKU{tag}"
    bk_pw = "It" + secrets.token_hex(8)
    conn = oracledb.connect(user=user, password=pw, dsn=f"{srv.host}:{srv.port}/{service}")
    cur = conn.cursor()
    try:
        spw = "It" + secrets.token_hex(8)
        cur.execute(f'CREATE USER {src} IDENTIFIED BY "{spw}" QUOTA UNLIMITED ON USERS')
        cur.execute(f"CREATE TABLE {src}.T (ID NUMBER PRIMARY KEY)")
        cur.execute(f"INSERT INTO {src}.T SELECT LEVEL FROM DUAL CONNECT BY LEVEL <= 400")
        conn.commit()
        # least privilege: the Data Pump roles, the directory, and its own schema for the job's
        # master table (no DBA role)
        cur.execute(f'CREATE USER {bk} IDENTIFIED BY "{bk_pw}" QUOTA UNLIMITED ON USERS')
        cur.execute(f"GRANT CREATE SESSION, CREATE TABLE TO {bk}")
        cur.execute(f"GRANT DATAPUMP_EXP_FULL_DATABASE, DATAPUMP_IMP_FULL_DATABASE TO {bk}")
        cur.execute(f"GRANT READ, WRITE ON DIRECTORY DATA_PUMP_DIR TO {bk}")

        async def change() -> None:
            cur.execute(f"DELETE FROM {src}.T WHERE ID > 100")
            conn.commit()

        p = engines.provider("oracle")
        res = await _round_trip(
            p,
            _ctx(srv, bk, bk_pw, src, tmp_path, tag=tag),
            _ctx(srv, bk, bk_pw, dst, tmp_path, tag=tag + "R"),
            dst,
            change,
        )
        assert res["outcome"].server_location.startswith("DATA_PUMP_DIR:")
        cur.execute(f"SELECT COUNT(*) FROM {dst}.T")
        assert cur.fetchone()[0] == 400
        cur.execute(f"SELECT COUNT(*) FROM {src}.T")
        assert cur.fetchone()[0] == 100
    finally:
        for u in (src, dst, bk):
            try:
                cur.execute(f"DROP USER {u} CASCADE")
            except oracledb.DatabaseError:
                pass
        conn.close()


# ---- Db2 LUW ------------------------------------------------------------------------------------
@pytest.mark.engine("db2")
@pytest.mark.capability("backup")
@pytest.mark.skipif(not _env("DB2", "HOST"), reason="DBM_IT_DB2_HOST not set")
async def test_db2_online_backup_and_recovery_history(tmp_path: Path) -> None:
    """The integration container enables archive logging (dbm-it-users.sh), as a production
    database must for online backups. BACKUP needs SYSADM / SYSCTRL / SYSMAINT (instance-level
    authorities): the instance owner runs it. RESTORE is an external step on the Db2 host; the
    generated commands are checked for the exact image."""
    user, pw = _env("DB2", "USER", "db2inst1"), _env("DB2", "PASSWORD")
    db = _env("DB2", "DB", "TESTDB")
    srv = _server("db2", "DB2", "50000")
    p = engines.provider("db2")
    backup_dir = _env("DB2", "BACKUP_DIR", "/database/dbm-it-backups")
    ctx = _ctx(srv, user, pw, db, tmp_path, spec_kw={"database": db}, server_directory=backup_dir)
    out = await p.backup(ctx)
    directory, _, ts = out.server_location.partition("#")
    assert directory == backup_dir and len(ts) == 14 and ts.isdigit()
    record = SimpleNamespace(location=out.server_location, database_name=db, engine="db2")
    detail = await p.verify(ctx, record, None)
    assert detail["level"] == "engine" and detail["tool"] == "SYSIBMADM.DB_HISTORY"
    cmd = p.restore_command(record, "DBMRST") or ""
    assert f"RESTORE DATABASE {db} FROM {backup_dir} TAKEN AT {ts} INTO DBMRST" in cmd
    assert "ROLLFORWARD DATABASE DBMRST TO END OF BACKUP AND COMPLETE" in cmd
    # a timestamp that is not in the history is reported, not crashed on
    with pytest.raises(engines.BackupProviderError, match="no backup taken"):
        await p.verify(ctx, SimpleNamespace(location=f"{backup_dir}#19990101000000", database_name=db), None)
