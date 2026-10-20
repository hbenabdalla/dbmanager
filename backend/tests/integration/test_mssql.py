"""SQL Server adapter against a real instance (mcr.microsoft.com/mssql/server:2022-latest).

Enabled when DBM_IT_MSSQL_HOST is set; ``scripts/integration-test mssql`` starts the container and
runs this suite inside the backend image (which has Microsoft ODBC Driver 18). DBM_IT_MSSQL_USER
(``sa``) only prepares the fixtures:

* databases ``dbm_it_shop`` (Query Store OFF) and ``dbm_it_qs`` (Query Store ON, READ_WRITE)
* schemas ``sales`` / ``hr`` with tables, index, view, procedure, function, trigger, sequence
* logins with generated passwords (never stored):
  ``dbm_it_monitor`` — VIEW SERVER STATE, VIEW ANY DEFINITION, a database user and SHOWPLAN
  (the least-privilege set documented in docs/database-engines/sql-server.md);
  ``dbm_it_admin`` — ALTER ANY LOGIN, ALTER ANY CONNECTION, db_owner of dbm_it_shop;
  ``dbm_it_app`` — column-level SELECT
* a parameterized workload and a real blocking chain.

Every adapter check runs as the monitoring login unless it tests administration.
"""

from __future__ import annotations

import os
import secrets
import threading
import time
from collections.abc import Iterator

import pytest

from app.adapters.base import (
    AdapterAuthenticationError,
    AdapterConnectionError,
    AdapterDatabaseNotFound,
    AdapterNotSupported,
    AdapterPermissionError,
    Capability,
    CapabilityLevel,
    ConnectionSpec,
)
from app.adapters.mssql.adapter import SQLServerAdapter
from tests.integration.matrix import record_version
from tests.integration.storage_series import size_series

pytestmark = [
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(
        not os.environ.get("DBM_IT_MSSQL_HOST"),
        reason="DBM_IT_MSSQL_HOST not set (scripts/integration-test mssql starts SQL Server 2022 from mcr.microsoft.com)",
    ),
]
DB, QS_DB = "dbm_it_shop", "dbm_it_qs"


def _pw() -> str:  # satisfies the SQL Server password policy
    return f"It-{secrets.token_urlsafe(12)}-aA1!"


PW = {"monitor": _pw(), "admin": _pw(), "app": _pw(), "created": _pw()}


def env(key: str, default: str = "") -> str:
    return os.environ.get(f"DBM_IT_MSSQL_{key}", default)


def spec(user: str = "dbm_it_monitor", password: str | None = None, **kw: object) -> ConnectionSpec:
    base: dict[str, object] = dict(
        host=env("HOST"),
        port=int(env("PORT", "1433")),
        username=user,
        password=PW["monitor"] if password is None else password,
        tls_mode="require",
        options={"trust_server_certificate": True},  # the container uses a self-signed certificate
        statement_timeout=30,
        database="master",
    )
    base.update(kw)
    return ConnectionSpec(**base)  # type: ignore[arg-type]


def _conn(user: str | None = None, password: str | None = None, database: str = "master"):  # type: ignore[no-untyped-def]
    import pyodbc

    from app.adapters import drivers

    return pyodbc.connect(
        f"DRIVER={{{drivers.odbc_driver_name()}}};SERVER={env('HOST')},{env('PORT', '1433')};DATABASE={database};"
        f"UID={user or env('USER', 'sa')};PWD={{{password or env('PASSWORD')}}};Encrypt=yes;TrustServerCertificate=yes",
        autocommit=True,
        timeout=30,
    )


def _exec(cur, *stmts: str) -> None:  # type: ignore[no-untyped-def]
    for s in stmts:
        cur.execute(s)
        while cur.nextset():
            pass


SCHEMA_DDL = (
    "CREATE SCHEMA sales",
    "CREATE SCHEMA hr",
    "CREATE TABLE sales.customers (id int IDENTITY PRIMARY KEY, email nvarchar(200) NOT NULL, country char(2) NOT NULL)",
    "CREATE INDEX ix_customers_country ON sales.customers (country)",
    "CREATE TABLE sales.orders (id int IDENTITY PRIMARY KEY, customer_id int NOT NULL REFERENCES sales.customers(id), "
    "total decimal(10,2) NOT NULL)",
    "CREATE TABLE hr.staff (id int PRIMARY KEY, name nvarchar(100))",
    "CREATE SEQUENCE sales.invoice_seq START WITH 1",
    "CREATE VIEW sales.v_totals AS SELECT customer_id, SUM(total) AS total FROM sales.orders GROUP BY customer_id",
    "CREATE FUNCTION sales.order_count() RETURNS int AS BEGIN RETURN (SELECT COUNT(*) FROM sales.orders) END",
    "CREATE PROCEDURE sales.top_customers @n int AS SELECT TOP (@n) customer_id FROM sales.v_totals ORDER BY total DESC",
    "CREATE TRIGGER sales.trg_orders ON sales.orders AFTER INSERT AS SET NOCOUNT ON",
    "INSERT sales.customers (email, country) SELECT TOP (5000) CONCAT('c', ROW_NUMBER() OVER (ORDER BY (SELECT 1)), '@x.test'), "
    "CASE WHEN ABS(CHECKSUM(NEWID())) % 2 = 0 THEN 'DE' ELSE 'FR' END FROM sys.all_objects a CROSS JOIN sys.all_objects b",
    "INSERT sales.orders (customer_id, total) SELECT TOP (20000) 1 + ABS(CHECKSUM(NEWID())) % 5000, ABS(CHECKSUM(NEWID())) % 97 "
    "FROM sys.all_objects a CROSS JOIN sys.all_objects b",
    "UPDATE STATISTICS sales.orders",
)


@pytest.fixture(scope="module", autouse=True)
def fixtures() -> Iterator[None]:
    conn = _conn()
    cur = conn.cursor()
    record_version("mssql", cur.execute("SELECT CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(64))").fetchval())
    for db in (DB, QS_DB):
        _exec(
            cur,
            f"IF DB_ID('{db}') IS NOT NULL BEGIN ALTER DATABASE [{db}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE; DROP DATABASE [{db}] END",
        )
    for login in ("dbm_it_monitor", "dbm_it_admin", "dbm_it_app", "dbm_it_created"):
        _exec(cur, f"IF SUSER_ID('{login}') IS NOT NULL DROP LOGIN [{login}]")
    _exec(
        cur,
        f"CREATE DATABASE [{DB}]",
        f"CREATE DATABASE [{QS_DB}]",
        f"ALTER DATABASE [{DB}] SET QUERY_STORE = OFF",
        f"ALTER DATABASE [{QS_DB}] SET QUERY_STORE = ON (OPERATION_MODE = READ_WRITE)",
        f"CREATE LOGIN [dbm_it_monitor] WITH PASSWORD = N'{PW['monitor']}', CHECK_POLICY = OFF",
        f"CREATE LOGIN [dbm_it_admin] WITH PASSWORD = N'{PW['admin']}', CHECK_POLICY = OFF",
        f"CREATE LOGIN [dbm_it_app] WITH PASSWORD = N'{PW['app']}', CHECK_POLICY = OFF",
        # least privilege for monitoring (docs/database-engines/sql-server.md)
        "GRANT VIEW SERVER STATE TO [dbm_it_monitor]",
        "GRANT VIEW ANY DEFINITION TO [dbm_it_monitor]",
        "GRANT ALTER ANY LOGIN, ALTER ANY CONNECTION TO [dbm_it_admin]",
    )
    for db in (DB, QS_DB):
        d = _conn(database=db)
        dc = d.cursor()
        _exec(dc, *SCHEMA_DDL)
        _exec(
            dc,
            "CREATE USER [dbm_it_monitor] FOR LOGIN [dbm_it_monitor]",
            "GRANT SHOWPLAN TO [dbm_it_monitor]",
            "GRANT SELECT ON SCHEMA::sales TO [dbm_it_monitor]",  # only to EXPLAIN application SQL
            "CREATE USER [dbm_it_admin] FOR LOGIN [dbm_it_admin]",
            "CREATE USER [dbm_it_app] FOR LOGIN [dbm_it_app]",
            "CREATE ROLE reporting",
            "GRANT SELECT ON SCHEMA::sales TO reporting",
            "ALTER ROLE reporting ADD MEMBER [dbm_it_app]",
            "GRANT SELECT (email) ON sales.customers TO [dbm_it_app]",
        )
        if db == DB:
            _exec(dc, "ALTER ROLE db_owner ADD MEMBER [dbm_it_admin]")
        # workload: one parameterized statement shape, many executions
        for i in range(50):
            dc.execute(
                "SELECT COUNT(*) FROM sales.orders o JOIN sales.customers c ON c.id = o.customer_id WHERE c.country = ?",
                "DE" if i % 2 else "FR",
            ).fetchall()
        dc.execute("SELECT * FROM sales.orders WHERE CAST(total AS varchar(20)) LIKE '%7%'").fetchall()
        d.close()
    conn.close()
    yield
    conn = _conn()
    cur = conn.cursor()
    for db in (DB, QS_DB):
        _exec(
            cur,
            f"IF DB_ID('{db}') IS NOT NULL BEGIN ALTER DATABASE [{db}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE; DROP DATABASE [{db}] END",
        )
    for login in ("dbm_it_monitor", "dbm_it_admin", "dbm_it_app", "dbm_it_created"):
        _exec(cur, f"IF SUSER_ID('{login}') IS NOT NULL DROP LOGIN [{login}]")
    conn.close()


@pytest.mark.capability("connection")
async def test_connection_and_capabilities() -> None:
    async with SQLServerAdapter(spec()) as a:
        info = await a.test_connection()
        assert info.version_num and info.version_num >= 16_000_000  # 2022
        assert info.current_user == "dbm_it_monitor"
        assert info.extra["view_server_state"] is True and info.extra["is_sysadmin"] is False
        caps = await a.discover_capabilities()
        assert caps[Capability.SESSIONS].level is CapabilityLevel.FULL
        assert caps[Capability.LOCKS].level is CapabilityLevel.FULL
        assert caps[Capability.QUERY_STATISTICS].level is CapabilityLevel.FULL


@pytest.mark.capability("connection")
async def test_monitoring_without_view_server_state_degrades() -> None:
    """The application login has no server permissions: DMV-backed features say why, not fail."""
    async with SQLServerAdapter(spec("dbm_it_app", PW["app"], database=DB)) as a:
        caps = await a.discover_capabilities()
        assert caps[Capability.LOCKS].level is CapabilityLevel.REQUIRES_PRIVILEGE
        assert "VIEW SERVER STATE" in (caps[Capability.LOCKS].note or "")


@pytest.mark.capability("inventory")
async def test_inventory_and_query_store_flags() -> None:
    async with SQLServerAdapter(spec()) as a:
        dbs = {d.name: d for d in await a.list_databases()}
        assert dbs[DB].extra["query_store"] is False and dbs[QS_DB].extra["query_store"] is True
        assert dbs[DB].size_bytes
        schemas = {s.name for s in await a.list_schemas(DB)}
        assert {"sales", "hr"} <= schemas
        objs = {(o.type, o.name) for o in await a.list_objects(DB, "sales")}
        assert {
            ("table", "orders"),
            ("view", "v_totals"),
            ("index", "ix_customers_country"),
            ("sequence", "invoice_seq"),
        } <= objs
        assert ("procedure", "top_customers") in objs and ("function", "order_count") in objs
        detail = await a.get_object_detail(DB, "sales", "orders", "table")
        assert [c.name for c in detail.columns] == ["id", "customer_id", "total"]
        assert detail.constraints and detail.triggers
        proc = await a.get_object_detail(DB, "sales", "top_customers", "procedure")
        assert proc.definition and "TOP (@n)" in proc.definition  # VIEW ANY DEFINITION


@pytest.mark.capability("users", "privileges")
async def test_logins_users_roles_privileges() -> None:
    async with SQLServerAdapter(spec()) as a:
        users = {u.name: u for u in await a.list_users()}
        assert {"dbm_it_monitor", "dbm_it_admin", "dbm_it_app"} <= set(users)
        assert "sa" in users and users["sa"].admin_reasons  # sysadmin
        roles = {r.name for r in await a.list_roles()}
        assert "sysadmin" in roles
        privs = await a.get_privileges(database=DB)
        assert any(p.grantee == "reporting" and p.privilege == "SELECT" for p in privs)
        assert any(p.grantee == "dbm_it_app" and "email" in (p.object_name or "") for p in privs)
        server = await a.get_privileges()
        assert any(p.grantee == "dbm_it_monitor" and p.privilege == "VIEW SERVER STATE" for p in server)


def _blocker() -> tuple[object, int, threading.Thread]:
    holder = _conn("dbm_it_admin", PW["admin"], DB)
    holder.autocommit = False
    hc = holder.cursor()
    hc.execute("UPDATE sales.orders SET total = total + 1 WHERE id = 1")
    spid = hc.execute("SELECT @@SPID").fetchval()

    def wait() -> None:
        try:
            w = _conn("dbm_it_admin", PW["admin"], DB)
            w.cursor().execute("SET LOCK_TIMEOUT 25000; UPDATE sales.orders SET total = total + 1 WHERE id = 1")
            w.close()
        except Exception:  # noqa: BLE001, S110 - killed by the test or timed out
            pass

    t = threading.Thread(target=wait, daemon=True)
    t.start()
    time.sleep(2)
    return holder, int(spid), t


@pytest.mark.capability("sessions", "locks")
async def test_sessions_blocking_kill_permissions() -> None:
    holder, spid, t = _blocker()
    try:
        async with SQLServerAdapter(spec()) as a:
            sessions = await a.get_sessions()
            blocked = [s for s in sessions if s.blocked_by]
            assert blocked and blocked[0].blocked_by == [str(spid)]
            locks = await a.get_locks()
            assert any(not lk.granted and lk.blocked_by == [str(spid)] for lk in locks)
            snap = await a.get_performance_metrics()
            assert snap.gauges["sessions.blocked"] >= 1
            with pytest.raises(AdapterNotSupported):  # T-SQL cannot cancel another session's batch
                await a.cancel_session(blocked[0].session_id)
            with pytest.raises(AdapterPermissionError):  # KILL needs ALTER ANY CONNECTION
                await a.cancel_session(blocked[0].session_id, terminate=True)
        async with SQLServerAdapter(spec("dbm_it_admin", PW["admin"])) as a:
            assert await a.cancel_session(blocked[0].session_id, terminate=True) is True
    finally:
        holder.rollback()  # type: ignore[attr-defined]
        holder.close()  # type: ignore[attr-defined]
        t.join(timeout=30)


@pytest.mark.capability("metrics")
async def test_metrics() -> None:
    async with SQLServerAdapter(spec()) as a:
        snap = await a.get_performance_metrics(slow=True)
        g, c = snap.gauges, snap.counters
        assert g["availability.up"] == 1.0 and g["connections.total"] >= 1
        assert {"transactions.commits", "io.blocks_read"} & set(c)
        assert all(isinstance(v, (int, float)) for v in g.values())
        for key in snap.unavailable:  # anything missing carries a reason
            assert snap.unavailable[key]


@pytest.mark.capability("queries")
async def test_query_statistics_with_and_without_query_store() -> None:
    async with SQLServerAdapter(spec()) as a:
        stats = await a.get_query_statistics(500)
        join = [s for s in stats if "JOIN sales.customers" in s.query_text]
        assert join and sum(s.calls for s in join) >= 50
        assert all("'DE'" not in s.query_text for s in join)  # parameterized text, no literals
        assert {s.database for s in join} >= {DB, QS_DB}  # works whether Query Store is on or off
        assert all(s.extra["source"].startswith("sys.dm_exec_query_stats") for s in join)
        assert all(s.plans is not None and s.plans >= 1 for s in join)  # COUNT(DISTINCT query_plan_hash), 0.13


@pytest.mark.capability("explain")
async def test_explain_estimate_and_actual() -> None:
    async with SQLServerAdapter(spec(database=DB)) as a:
        est = await a.explain_query(
            "SELECT * FROM sales.orders o JOIN sales.customers c ON c.id = o.customer_id WHERE o.total > 50", DB
        )
        assert est.root is not None and not est.executed and est.raw  # showplan XML kept
        act = await a.explain_query("SELECT COUNT(*) FROM sales.orders WHERE total > 50", DB, analyze=True)
        assert act.executed and act.root is not None

        def nodes(n):  # type: ignore[no-untyped-def]
            yield n
            for c in n.children:
                yield from nodes(c)

        # operators such as Compute Scalar carry no runtime counters; the scan below them does
        assert any(n.actual_rows is not None for n in nodes(act.root))


@pytest.mark.capability("storage")
async def test_configuration_replication_storage() -> None:
    async with SQLServerAdapter(spec(database=DB)) as a:  # largest tables come from the connected database
        cfg = {p.name for p in await a.get_configuration()}
        assert "max server memory (MB)" in cfg
        rep = await a.get_replication_status()
        assert rep.role == "standalone"
        st = await a.get_storage_metrics()
        assert st.total_bytes and st.top_tables
        assert all({"schema", "name", "table_bytes", "index_bytes", "total_bytes"} <= set(t) for t in st.top_tables)
        assert DB in size_series("mssql", await a.list_databases(), st.tablespaces)["db_database_size_bytes"]


@pytest.mark.capability("security", "recommendations")
async def test_security_evidence_and_rules() -> None:
    from app.advisors.engine import EvidenceContext, evaluate

    async with SQLServerAdapter(spec()) as a:
        sec = await a.get_security_context()
        tun = await a.get_tuning_context()
    assert sec["sa_enabled"] is True and sec["mixed_mode_auth"] is True
    assert "dbm_it_admin" not in (sec["sysadmin_members"] or [])
    assert tun["query_store_enabled"] is True  # tuning evidence: at least one user database has it on
    assert tun["cpu_count"] and tun["max_server_memory_mb"]
    ctx = EvidenceContext(
        server_id="it",
        server_name="it",
        engine="mssql",
        environment_tier="production",
        tls_mode="require",
        is_demo=False,
        window_hours=1,
        security=sec,
        tuning=tun,
    )
    findings = evaluate(ctx, "security") + evaluate(ctx, "performance")
    assert all(f.rule.applies_to("mssql") for f in findings)
    assert any(f.rule.rule_id.startswith("MSSQL-SEC") for f in findings)  # sa enabled at minimum


@pytest.mark.capability("administration")
async def test_administration_guarded_actions() -> None:
    async with SQLServerAdapter(spec("dbm_it_admin", PW["admin"])) as a:
        stmt = a.build_admin("create_user", {"name": "dbm_it_created", "password": PW["created"]})
        assert PW["created"] not in " ".join(stmt.display)
        await a.run_admin(stmt)
        assert "dbm_it_created" in {u.name for u in await a.list_users()}
        await a.run_admin(
            a.build_admin(
                "create_index",
                {"database": DB, "schema": "sales", "table": "orders", "name": "ix_orders_total", "columns": ["total"]},
            )
        )
        assert "ix_orders_total" in {o.name for o in await a.list_objects(DB, "sales", "index")}
        await a.run_admin(a.build_admin("drop_user", {"name": "dbm_it_created"}))
    async with SQLServerAdapter(spec()) as a:  # Monitoring Only identity cannot change anything
        with pytest.raises(AdapterPermissionError):
            await a.run_admin(a.build_admin("create_user", {"name": "dbm_it_nope", "password": PW["created"]}))


async def test_read_only_workspace_rolls_back() -> None:
    """SQL Server has no read-only transaction: the adapter always rolls a read-only run back
    (the workspace's statement classifier rejects writes before they reach the adapter)."""
    async with SQLServerAdapter(spec("dbm_it_admin", PW["admin"], database=DB)) as a:
        res = await a.execute_sql(
            "DELETE FROM sales.orders WHERE id <= 10", DB, read_only=True, max_rows=10, timeout_s=5
        )
        assert res.committed is False
        res = await a.execute_sql(
            "SELECT COUNT(*) AS n FROM sales.orders", DB, read_only=True, max_rows=10, timeout_s=5
        )
        assert res.rows == [[20000]]


@pytest.mark.capability("connection")
async def test_error_classification() -> None:
    with pytest.raises(AdapterAuthenticationError):
        async with SQLServerAdapter(spec(password="wrong-password")) as a:
            await a.test_connection()
    with pytest.raises(AdapterDatabaseNotFound):
        async with SQLServerAdapter(spec(database="dbm_it_nope")) as a:
            await a.test_connection()
    with pytest.raises(AdapterConnectionError):
        async with SQLServerAdapter(spec(port=1, connect_timeout=3)) as a:
            await a.test_connection()
