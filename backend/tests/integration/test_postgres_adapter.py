"""PostgreSQL reference adapter against a real server (regression suite).

Enabled when DBM_IT_PG_HOST is set; DBM_IT_PG_USER must be a superuser (it only prepares the
fixtures — database, schemas, objects, a least-privilege monitoring role with ``pg_monitor``,
an application role and a workload). Every adapter check runs as the monitoring role, the
administration checks as the admin role::

    DBM_IT_PG_HOST=localhost DBM_IT_PG_PORT=15432 DBM_IT_PG_USER=postgres DBM_IT_PG_PASSWORD=... pytest -m postgresql

Query statistics need ``shared_preload_libraries = 'pg_stat_statements'`` on the server (the
compose test profile sets it).
"""

from __future__ import annotations

import asyncio
import os
import threading
import time

import asyncpg
import pytest

from app.adapters.base import (
    AdapterAuthenticationError,
    AdapterConnectionError,
    AdapterDatabaseNotFound,
    AdapterPermissionError,
    Capability,
    CapabilityLevel,
    ConnectionSpec,
)
from app.adapters.postgres.adapter import PostgreSQLAdapter
from tests.integration.matrix import record_version

pytestmark = [
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(
        not os.environ.get("DBM_IT_PG_HOST"),
        reason="DBM_IT_PG_HOST not set (docker compose -f docker-compose.test.yml up -d pg16, or scripts/integration-test)",
    ),
]
DB = "dbm_it_pg"
MON_PW = "It-Pg-Monitor-1!"
ADM_PW = "It-Pg-Admin-1!"


def env(key: str, default: str = "") -> str:
    return os.environ.get(f"DBM_IT_PG_{key}", default)


def spec(user: str = "dbm_it_monitor", password: str = MON_PW, **kw: object) -> ConnectionSpec:
    base: dict[str, object] = dict(
        host=env("HOST", "localhost"),
        port=int(env("PORT", "5432")),
        database=DB,
        username=user,
        password=password,
        tls_mode=env("TLS", "prefer"),
        statement_timeout=15,
    )
    base.update(kw)
    return ConnectionSpec(**base)  # type: ignore[arg-type]


async def _admin(database: str | None = None) -> asyncpg.Connection:
    return await asyncpg.connect(
        host=env("HOST", "localhost"),
        port=int(env("PORT", "5432")),
        user=env("USER", "postgres"),
        password=env("PASSWORD") or None,
        database=database or env("DB", "postgres"),
    )


@pytest.fixture(scope="module", autouse=True)
async def fixtures():  # type: ignore[no-untyped-def]
    c = await _admin()
    record_version("postgresql", await c.fetchval("SHOW server_version"))
    await c.execute(f"DROP DATABASE IF EXISTS {DB} WITH (FORCE)")
    for r in ("dbm_it_monitor", "dbm_it_app", "dbm_it_admin", "dbm_it_created"):
        await c.execute(f"DROP ROLE IF EXISTS {r}")
    await c.execute("DROP ROLE IF EXISTS dbm_it_reporting")
    await c.execute(f"CREATE DATABASE {DB}")
    await c.execute(f"CREATE ROLE dbm_it_monitor LOGIN PASSWORD '{MON_PW}'")
    await c.execute("GRANT pg_monitor TO dbm_it_monitor")
    await c.execute(f"GRANT CONNECT ON DATABASE {DB} TO dbm_it_monitor")
    await c.execute(f"CREATE ROLE dbm_it_admin LOGIN PASSWORD '{ADM_PW}' CREATEDB CREATEROLE")
    await c.execute("GRANT pg_signal_backend TO dbm_it_admin")
    await c.execute("CREATE ROLE dbm_it_reporting")
    await c.execute("CREATE ROLE dbm_it_app LOGIN PASSWORD 'It-Pg-App-1!' IN ROLE dbm_it_reporting")
    await c.close()
    d = await _admin(DB)
    for stmt in (
        "CREATE EXTENSION IF NOT EXISTS pg_stat_statements",
        "CREATE SCHEMA sales",
        "CREATE SCHEMA hr",
        "CREATE TABLE sales.customers (id serial PRIMARY KEY, email text NOT NULL, country char(2))",
        "CREATE INDEX ix_customers_country ON sales.customers (country)",
        "CREATE TABLE sales.orders (id serial PRIMARY KEY, customer_id int NOT NULL REFERENCES sales.customers, total numeric(10,2))",
        "CREATE TABLE hr.staff (id int PRIMARY KEY, name text)",
        "CREATE SEQUENCE sales.invoice_seq",
        "CREATE VIEW sales.v_totals AS SELECT customer_id, sum(total) AS total FROM sales.orders GROUP BY customer_id",
        "CREATE FUNCTION sales.order_count() RETURNS bigint LANGUAGE sql AS 'SELECT count(*) FROM sales.orders'",
        "CREATE FUNCTION sales.touch() RETURNS trigger LANGUAGE plpgsql AS 'BEGIN RETURN NEW; END'",
        "CREATE TRIGGER trg_orders BEFORE INSERT ON sales.orders FOR EACH ROW EXECUTE FUNCTION sales.touch()",
        "INSERT INTO sales.customers (email, country) SELECT 'c' || g || '@x.test', CASE WHEN g % 2 = 0 THEN 'DE' ELSE 'FR' END FROM generate_series(1, 5000) g",
        "INSERT INTO sales.orders (customer_id, total) SELECT 1 + (g % 5000), g % 97 FROM generate_series(1, 20000) g",
        "ANALYZE",
        "GRANT USAGE ON SCHEMA sales TO dbm_it_reporting",
        "GRANT SELECT ON ALL TABLES IN SCHEMA sales TO dbm_it_reporting",
        "GRANT SELECT (email) ON sales.customers TO dbm_it_app",
        "GRANT USAGE ON SCHEMA sales, hr TO dbm_it_monitor",
        "GRANT SELECT ON ALL TABLES IN SCHEMA sales TO dbm_it_monitor",  # only for EXPLAIN of application SQL
        "GRANT USAGE, CREATE ON SCHEMA sales TO dbm_it_admin",
        "ALTER TABLE sales.orders OWNER TO dbm_it_admin",
    ):
        await d.execute(stmt)
    # workload for query statistics: the same statement shape many times, plus a scan
    for i in range(50):
        await d.fetch(
            "SELECT count(*) FROM sales.orders o JOIN sales.customers c ON c.id = o.customer_id WHERE c.country = $1",
            "DE" if i % 2 else "FR",
        )
    await d.fetch("SELECT * FROM sales.orders WHERE total::text LIKE '%7%'")
    await d.close()
    yield
    c = await _admin()
    await c.execute(f"DROP DATABASE IF EXISTS {DB} WITH (FORCE)")
    for r in ("dbm_it_monitor", "dbm_it_app", "dbm_it_admin", "dbm_it_created", "dbm_it_reporting"):
        await c.execute(f"DROP ROLE IF EXISTS {r}")
    await c.close()


@pytest.mark.capability("connection")
async def test_connection_and_capabilities() -> None:
    async with PostgreSQLAdapter(spec()) as a:
        info = await a.test_connection()
        assert info.version_num and info.version_num >= 120000 and info.current_user == "dbm_it_monitor"
        caps = await a.discover_capabilities()
        assert caps[Capability.SESSIONS].level is CapabilityLevel.FULL  # pg_monitor
        assert caps[Capability.QUERY_STATISTICS].level is CapabilityLevel.FULL  # pg_stat_statements


@pytest.mark.capability("inventory")
async def test_inventory() -> None:
    async with PostgreSQLAdapter(spec()) as a:
        dbs = {d.name: d for d in await a.list_databases()}
        assert DB in dbs and dbs[DB].size_bytes
        schemas = {s.name: s for s in await a.list_schemas(DB)}
        assert {"sales", "hr"} <= set(schemas) and schemas["sales"].object_counts
        objs = {(o.type, o.name) for o in await a.list_objects(DB, "sales")}
        assert {
            ("table", "orders"),
            ("view", "v_totals"),
            ("index", "ix_customers_country"),
            ("sequence", "invoice_seq"),
        } <= objs
        assert any(t == "function" and n == "order_count" for t, n in objs)
        detail = await a.get_object_detail(DB, "sales", "orders", "table")
        assert [c.name for c in detail.columns] == ["id", "customer_id", "total"]
        assert detail.constraints and detail.triggers


@pytest.mark.capability("users", "privileges")
async def test_roles_and_privileges() -> None:
    async with PostgreSQLAdapter(spec()) as a:
        users = {u.name: u for u in await a.list_users()}
        assert "dbm_it_app" in users and "dbm_it_reporting" in users["dbm_it_app"].member_of
        su = [u for u in users.values() if u.is_superuser]
        assert su and all(u.admin_reasons for u in su)
        privs = await a.get_privileges(database=DB)
        assert any(p.grantee == "dbm_it_reporting" and p.privilege == "SELECT" for p in privs)
        assert any(p.grantee == "dbm_it_app" and "email" in p.object_name for p in privs)


@pytest.mark.capability("sessions", "locks")
async def test_sessions_blocking_and_cancel() -> None:
    holder = await asyncpg.connect(  # a non-superuser holder, so pg_signal_backend applies
        host=env("HOST", "localhost"), port=int(env("PORT", "5432")), user="dbm_it_admin", password=ADM_PW, database=DB
    )
    await holder.execute("BEGIN")
    await holder.execute("UPDATE sales.orders SET total = total + 1 WHERE id = 1")
    holder_pid = await holder.fetchval("SELECT pg_backend_pid()")

    def wait() -> None:
        async def run() -> None:
            w = await asyncpg.connect(
                host=env("HOST", "localhost"),
                port=int(env("PORT", "5432")),
                user="dbm_it_admin",
                password=ADM_PW,
                database=DB,
            )
            try:
                await w.execute("SET lock_timeout = '20s'")
                await w.execute("UPDATE sales.orders SET total = total + 1 WHERE id = 1")
            except Exception:  # noqa: BLE001, S110 - cancelled by the test
                pass
            finally:
                await w.close()

        asyncio.run(run())

    t = threading.Thread(target=wait, daemon=True)
    t.start()
    time.sleep(1.5)  # noqa: ASYNC251 - the waiter must reach its lock wait
    try:
        async with PostgreSQLAdapter(spec()) as a:
            sessions = await a.get_sessions()
            blocked = [s for s in sessions if s.blocked_by]
            assert blocked and blocked[0].blocked_by == [str(holder_pid)] and blocked[0].wait_event_type == "Lock"
            locks = await a.get_locks()
            assert any(not lk.granted and lk.blocked_by == [str(holder_pid)] for lk in locks)
            snap = await a.get_performance_metrics()
            assert snap.gauges["sessions.blocked"] >= 1
            with pytest.raises(AdapterPermissionError):  # pg_monitor cannot signal other roles' backends
                await a.cancel_session(blocked[0].session_id)
        async with PostgreSQLAdapter(spec("dbm_it_admin", ADM_PW)) as a:  # same role: allowed
            assert await a.cancel_session(blocked[0].session_id) is True
    finally:
        await holder.execute("ROLLBACK")
        await holder.close()
        t.join(timeout=25)


@pytest.mark.capability("metrics")
async def test_metrics() -> None:
    async with PostgreSQLAdapter(spec()) as a:
        snap = await a.get_performance_metrics()
        g, c = snap.gauges, snap.counters
        assert g["availability.up"] == 1.0 and g["connections.total"] >= 1 and 0 < g["connections.utilization"] <= 100
        assert {"transactions.commits", "io.blocks_hit", "io.blocks_read"} <= set(c)
        assert "cpu.percent" in snap.unavailable  # never fabricated
        assert g.get("storage.database_bytes", 0) > 0


@pytest.mark.capability("queries")
async def test_query_statistics() -> None:
    async with PostgreSQLAdapter(spec()) as a:
        stats = await a.get_query_statistics(500)
        join = [s for s in stats if "JOIN sales.customers" in s.query_text]
        assert join and join[0].calls >= 50 and "'DE'" not in join[0].query_text and "$1" in join[0].query_text


@pytest.mark.capability("explain")
async def test_explain_estimate_and_analyze() -> None:
    async with PostgreSQLAdapter(spec()) as a:
        res = await a.explain_query(
            "SELECT * FROM sales.orders o JOIN sales.customers c ON c.id = o.customer_id WHERE o.total > 50", DB
        )
        assert res.root and not res.executed
        act = await a.explain_query("SELECT count(*) FROM sales.orders WHERE total > 50", DB, analyze=True)
        assert act.executed and act.root and act.root.actual_rows is not None


@pytest.mark.capability("storage")
async def test_configuration_replication_storage() -> None:
    async with PostgreSQLAdapter(spec()) as a:
        cfg = await a.get_configuration()
        assert any(p.name == "max_connections" for p in cfg)
        rep = await a.get_replication_status()
        assert rep.role in ("standalone", "primary", "replica")
        st = await a.get_storage_metrics()
        assert st.total_bytes and st.top_tables


@pytest.mark.capability("security", "recommendations")
async def test_security_and_rules_on_real_evidence() -> None:
    from app.advisors.engine import EvidenceContext, evaluate

    async with PostgreSQLAdapter(spec(env("USER", "postgres"), env("PASSWORD"))) as a:
        sec = await a.get_security_context()
        assert "roles" in sec and "settings" in sec and sec["extensions"].get("pg_stat_statements")
        tun = await a.get_tuning_context()
        assert tun["pg_stat_statements"] is True
    ctx = EvidenceContext(
        server_id="it",
        server_name="it",
        engine="postgresql",
        environment_tier="production",
        tls_mode="disable",
        is_demo=False,
        window_hours=1,
        security=sec,
        tuning=tun,
    )
    findings = evaluate(ctx, "security") + evaluate(ctx, "performance")
    assert all(f.rule.applies_to("postgresql") for f in findings)
    assert any(f.rule.rule_id.startswith(("GEN-SEC", "PG-SEC")) for f in findings)  # tls_mode=disable at least


@pytest.mark.capability("administration")
async def test_admin_actions_with_admin_role() -> None:
    async with PostgreSQLAdapter(spec("dbm_it_admin", ADM_PW)) as a:
        stmt = a.build_admin("create_user", {"name": "dbm_it_created", "password": "Created-Pass-1!"})
        assert "Created-Pass-1!" not in " ".join(stmt.statements + stmt.display)
        await a.run_admin(stmt)
        await a.run_admin(
            a.build_admin(
                "create_index",
                {"database": DB, "schema": "sales", "table": "orders", "name": "ix_orders_total", "columns": ["total"]},
            )
        )
        assert "ix_orders_total" in {o.name for o in await a.list_objects(DB, "sales", "index")}
        await a.run_admin(a.build_admin("drop_user", {"name": "dbm_it_created"}))
    async with PostgreSQLAdapter(spec()) as a:  # the monitoring role cannot change anything
        with pytest.raises(AdapterPermissionError):
            await a.run_admin(a.build_admin("create_role", {"name": "dbm_it_nope"}))


async def test_read_only_workspace() -> None:
    async with PostgreSQLAdapter(spec("dbm_it_admin", ADM_PW)) as a:
        with pytest.raises(AdapterPermissionError):
            await a.execute_sql("DELETE FROM sales.orders", DB, read_only=True, max_rows=10, timeout_s=5)
        res = await a.execute_sql(
            "SELECT count(*) AS n FROM sales.orders", DB, read_only=True, max_rows=10, timeout_s=5
        )
        assert res.rows == [[20000]]


@pytest.mark.capability("connection")
async def test_error_classification() -> None:
    with pytest.raises(AdapterAuthenticationError):
        async with PostgreSQLAdapter(spec(password="wrong")) as a:
            await a.test_connection()
    with pytest.raises(AdapterDatabaseNotFound):
        async with PostgreSQLAdapter(spec(database="dbm_it_nope")) as a:
            await a.test_connection()
    with pytest.raises(AdapterConnectionError):
        async with PostgreSQLAdapter(spec(port=1, connect_timeout=2)) as a:
            await a.test_connection()
