"""MySQL and MariaDB adapters against real servers.

Each flavor runs when its variables are set (``docker compose -f docker-compose.test.yml
--profile phase3 up -d`` provides both)::

    DBM_IT_MYSQL_HOST=127.0.0.1 DBM_IT_MYSQL_PORT=13306 DBM_IT_MYSQL_USER=root DBM_IT_MYSQL_PASSWORD=it-mysql
    DBM_IT_MARIADB_HOST=127.0.0.1 DBM_IT_MARIADB_PORT=13307 DBM_IT_MARIADB_USER=root DBM_IT_MARIADB_PASSWORD=it-mariadb

The administrative account only prepares fixtures (schema, workload, a least-privilege
monitoring account as documented in docs/database-engines/mysql.md); every adapter check
below runs as that monitoring account.
"""

from __future__ import annotations

import os
import uuid

import pymysql
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
from app.adapters.mysql.adapter import MariaDBAdapter, MySQLAdapter
from tests.integration.matrix import record_version

pytestmark = pytest.mark.asyncio(loop_scope="session")

FLAVORS = {"mysql": MySQLAdapter, "mariadb": MariaDBAdapter}
MON_PW = "It-Monitor-Pass-1!"
DB = "dbm_it_shop"


def env(flavor: str, key: str, default: str | None = None) -> str | None:
    return os.environ.get(f"DBM_IT_{flavor.upper()}_{key}", default)


def configured(flavor: str) -> bool:
    return bool(env(flavor, "HOST"))


def admin_conn(flavor: str) -> pymysql.Connection:
    return pymysql.connect(
        host=env(flavor, "HOST"),
        port=int(env(flavor, "PORT", "3306") or 3306),
        user=env(flavor, "USER", "root"),
        password=env(flavor, "PASSWORD", ""),
        autocommit=True,
    )


def spec(flavor: str, **kw: object) -> ConnectionSpec:
    base: dict[str, object] = dict(
        host=env(flavor, "HOST"),
        port=int(env(flavor, "PORT", "3306") or 3306),
        username="dbm_it_monitor",
        password=MON_PW,
        tls_mode="prefer",
        statement_timeout=15,
    )
    base.update(kw)
    return ConnectionSpec(**base)  # type: ignore[arg-type]


@pytest.fixture(scope="module", params=list(FLAVORS))
def flavor(request: pytest.FixtureRequest) -> str:
    f = request.param
    if not configured(f):
        pytest.skip(f"DBM_IT_{f.upper()}_HOST not set")
    with admin_conn(f) as c, c.cursor() as cur:
        version = cur.execute("SELECT VERSION()") and cur.fetchone()[0]
        mariadb = "mariadb" in version.lower()
        record_version(f, version)
        stmts = [
            f"DROP DATABASE IF EXISTS {DB}",
            f"CREATE DATABASE {DB}",
            f"CREATE TABLE {DB}.customers (id INT PRIMARY KEY AUTO_INCREMENT, email VARCHAR(200) NOT NULL, country CHAR(2), KEY ix_country (country))",
            f"CREATE TABLE {DB}.orders (id INT PRIMARY KEY AUTO_INCREMENT, customer_id INT NOT NULL, total DECIMAL(10,2), KEY ix_customer (customer_id))",
            f"CREATE TABLE {DB}.no_pk (event VARCHAR(100)) ENGINE=InnoDB",
            f"CREATE VIEW {DB}.v_totals AS SELECT customer_id, SUM(total) AS total FROM {DB}.orders GROUP BY customer_id",
            f"CREATE PROCEDURE {DB}.p_count() SELECT COUNT(*) FROM {DB}.orders",
            f"CREATE TRIGGER {DB}.trg_ai AFTER INSERT ON {DB}.orders FOR EACH ROW INSERT INTO {DB}.no_pk VALUES ('order')",
            f"INSERT INTO {DB}.customers (email, country) VALUES "
            + ",".join(f"('c{i}@x.test', 'DE')" for i in range(200)),
            f"INSERT INTO {DB}.orders (customer_id, total) SELECT id, id * 2 FROM {DB}.customers",
            f"SELECT COUNT(*) FROM {DB}.orders o JOIN {DB}.customers c ON c.id = o.customer_id WHERE c.country = 'DE'",
            "DROP USER IF EXISTS 'dbm_it_monitor'@'%'",
            "DROP USER IF EXISTS 'dbm_it_app'@'%'",
            "DROP ROLE IF EXISTS dbm_it_reporting" if not mariadb else "DROP ROLE IF EXISTS dbm_it_reporting",
            f"CREATE USER 'dbm_it_monitor'@'%' IDENTIFIED BY '{MON_PW}'",
            "GRANT PROCESS, SHOW DATABASES, SHOW VIEW ON *.* TO 'dbm_it_monitor'@'%'",
            "GRANT SELECT ON performance_schema.* TO 'dbm_it_monitor'@'%'",
            "GRANT SELECT ON mysql.* TO 'dbm_it_monitor'@'%'",
            f"GRANT SELECT, TRIGGER ON {DB}.* TO 'dbm_it_monitor'@'%'",
            "CREATE ROLE dbm_it_reporting",
            f"GRANT SELECT ON {DB}.* TO dbm_it_reporting",
            "CREATE USER 'dbm_it_app'@'%' IDENTIFIED BY 'App-It-Pass-1!'",
            f"GRANT SELECT (email) ON {DB}.customers TO 'dbm_it_app'@'%'",
            "GRANT dbm_it_reporting TO 'dbm_it_app'@'%'",
        ]
        stmts.append(
            "GRANT REPLICA MONITOR ON *.* TO 'dbm_it_monitor'@'%'"
            if mariadb
            else "GRANT REPLICATION CLIENT ON *.* TO 'dbm_it_monitor'@'%'"
        )
        if not mariadb:
            stmts.append("GRANT SHOW_ROUTINE ON *.* TO 'dbm_it_monitor'@'%'")
        for s in stmts:
            cur.execute(s)
    yield f
    with admin_conn(f) as c, c.cursor() as cur:
        for s in (
            f"DROP DATABASE IF EXISTS {DB}",
            "DROP USER IF EXISTS 'dbm_it_monitor'@'%'",
            "DROP USER IF EXISTS 'dbm_it_app'@'%'",
            "DROP ROLE IF EXISTS dbm_it_reporting",
            "DROP USER IF EXISTS 'dbm_it_created'@'%'",
        ):
            cur.execute(s)


@pytest.mark.capability("connection")
async def test_connection_discovery_and_capabilities(flavor: str) -> None:
    cls = FLAVORS[flavor]
    async with cls(spec(flavor)) as a:
        info = await a.test_connection()
        assert info.extra["flavor_detected"] == flavor, "registered flavor must match the server"
        assert info.version_num and info.uptime_seconds and info.current_user
        caps = await a.discover_capabilities()
        assert caps[Capability.CONNECTION].level is CapabilityLevel.FULL
        assert caps[Capability.SESSIONS].level is CapabilityLevel.FULL  # PROCESS granted
        assert caps[Capability.USERS].level is CapabilityLevel.FULL  # SELECT on mysql granted
        if info.extra["performance_schema"]:
            assert caps[Capability.QUERY_STATISTICS].level is CapabilityLevel.FULL
        else:
            assert caps[Capability.QUERY_STATISTICS].level is CapabilityLevel.REQUIRES_CONFIGURATION


@pytest.mark.capability("inventory")
async def test_inventory(flavor: str) -> None:
    async with FLAVORS[flavor](spec(flavor)) as a:
        dbs = {d.name: d for d in await a.list_databases()}
        assert DB in dbs and dbs[DB].size_bytes and not dbs[DB].is_system and dbs["mysql"].is_system
        [schema] = await a.list_schemas(DB)
        assert schema.object_counts["tables"] == 3 and schema.object_counts["views"] == 1
        objs = {(o.type, o.name) for o in await a.list_objects(DB, DB)}
        assert {("table", "orders"), ("view", "v_totals"), ("index", "ix_customer"), ("trigger", "trg_ai")} <= objs
        detail = await a.get_object_detail(DB, DB, "orders", "table")
        assert [c.name for c in detail.columns] == ["id", "customer_id", "total"]
        assert detail.definition and detail.definition.startswith("CREATE TABLE")
        assert {i["name"] for i in detail.indexes} == {"PRIMARY", "ix_customer"}
        assert any(t["name"] == "trg_ai" for t in detail.triggers)


@pytest.mark.capability("users", "privileges")
async def test_accounts_roles_privileges(flavor: str) -> None:
    async with FLAVORS[flavor](spec(flavor)) as a:
        users = {u.name: u for u in await a.list_users()}
        assert "dbm_it_app@%" in users and "dbm_it_reporting" in str(users["dbm_it_app@%"].member_of)
        root = next(u for n, u in users.items() if n.startswith("root@"))
        assert root.is_superuser and "SUPER" in root.admin_reasons
        roles = [r.name for r in await a.list_roles()]
        assert any("dbm_it_reporting" in r for r in roles)
        privs = await a.get_privileges(database=DB)
        assert any(p.object_type == "column" and p.object_name.endswith("customers.email") for p in privs)
        assert any(p.object_type == "role" and p.grantee == "dbm_it_app@%" for p in privs)


@pytest.mark.capability("sessions", "locks")
async def test_sessions_locks_and_blocking(flavor: str) -> None:
    # session 1 holds a row lock, session 2 waits on it
    holder = admin_conn(flavor)
    waiter = admin_conn(flavor)
    try:
        with holder.cursor() as c:
            c.execute("SET autocommit = 0")
            c.execute(f"UPDATE {DB}.orders SET total = total + 1 WHERE id = 1")
        import threading

        def wait() -> None:
            try:
                with waiter.cursor() as c:
                    c.execute("SET SESSION innodb_lock_wait_timeout = 20")
                    c.execute(f"UPDATE {DB}.orders SET total = total + 1 WHERE id = 1")
            except Exception:  # noqa: BLE001, S110
                pass

        t = threading.Thread(target=wait, daemon=True)
        t.start()
        import time

        time.sleep(1.5)  # noqa: ASYNC251 - the blocked session must reach its lock wait
        async with FLAVORS[flavor](spec(flavor)) as a:
            sessions = {s.session_id: s for s in await a.get_sessions()}
            blocked = [s for s in sessions.values() if s.blocked_by]
            assert blocked, "the waiting session must show its blocker"
            assert blocked[0].blocked_by == [str(holder.thread_id())]
            locks = await a.get_locks()
            assert any(not lk.granted and lk.blocked_by == [str(holder.thread_id())] for lk in locks)
            snap = await a.get_performance_metrics()
            assert snap.gauges["sessions.blocked"] >= 1
            with pytest.raises(AdapterPermissionError):  # monitoring account: no CONNECTION_ADMIN
                await a.cancel_session(str(waiter.thread_id()))
        root_spec = spec(flavor, username=env(flavor, "USER", "root"), password=env(flavor, "PASSWORD", ""))
        async with FLAVORS[flavor](root_spec) as a:
            assert await a.cancel_session(str(waiter.thread_id()))  # KILL QUERY on the waiter
        t.join(timeout=10)
    finally:
        holder.rollback()
        holder.close()
        waiter.close()


@pytest.mark.capability("metrics")
async def test_metrics_and_rates(flavor: str) -> None:
    async with FLAVORS[flavor](spec(flavor)) as a:
        snap = await a.get_performance_metrics()
        g, c = snap.gauges, snap.counters
        assert g["availability.up"] == 1 and g["connections.total"] >= 1 and g["connections.max"] > 0
        assert 0 < g["connections.utilization"] <= 100
        assert {"io.blocks_hit", "io.blocks_read", "rows.returned", "query.calls"} <= set(c)
        assert g["storage.database_bytes"] > 0
        assert "cpu.percent" in snap.unavailable  # never invented
        fast = await a.get_performance_metrics(slow=False)
        assert "storage.database_bytes" not in fast.gauges  # SLOW class skipped


@pytest.mark.capability("queries")
async def test_query_statistics_use_digests(flavor: str) -> None:
    async with FLAVORS[flavor](spec(flavor)) as a:
        info = await a.get_server_info()
        if not info.extra["performance_schema"]:
            pytest.skip("performance_schema is OFF on this server")
        stats = await a.get_query_statistics(1000)
        assert stats and all(s.query_id and s.calls > 0 for s in stats)
        join = [s for s in stats if "JOIN" in s.query_text and "customers" in s.query_text]
        assert join and "'DE'" not in join[0].query_text  # digests are literal-free
        assert join[0].logical_reads is not None and join[0].extra["reads_unit"] == "rows examined"


@pytest.mark.capability("explain")
async def test_explain_estimate_and_analyze(flavor: str) -> None:
    async with FLAVORS[flavor](spec(flavor)) as a:
        sql = f"SELECT * FROM {DB}.orders o JOIN {DB}.customers c ON c.id = o.customer_id WHERE o.total > 10 ORDER BY o.total"
        est = await a.explain_query(sql)
        assert est.root and not est.executed and est.mode == "estimate"
        ops = []

        def walk(n):  # type: ignore[no-untyped-def]
            ops.append(n.operator.lower())
            for ch in n.children:
                walk(ch)

        walk(est.root)
        assert any("scan" in o or "lookup" in o for o in ops), ops
        act = await a.explain_query(f"SELECT COUNT(*) FROM {DB}.orders WHERE total > 10", analyze=True)
        assert act.executed and "rolled back" in act.notes[0]


@pytest.mark.capability("storage", "security")
async def test_configuration_replication_storage_security_tuning(flavor: str) -> None:
    async with FLAVORS[flavor](spec(flavor)) as a:
        cfg = {c.name: c for c in await a.get_configuration()}
        assert "max_connections" in cfg and cfg["max_connections"].value
        rep = await a.get_replication_status()
        assert rep.role in ("standalone", "primary", "replica")
        st = await a.get_storage_metrics()
        assert st.top_tables and any(t["name"] == "orders" for t in st.top_tables)
        sec = await a.get_security_context()
        assert sec["require_secure_transport"] in ("ON", "OFF")
        assert any(r["name"] == "dbm_it_app@%" for r in sec["roles"])
        tune = await a.get_tuning_context()
        assert f"{DB}.no_pk" in tune["tables_without_pk"]
        assert tune["innodb_buffer_pool_hit_ratio"] is None or 0 <= tune["innodb_buffer_pool_hit_ratio"] <= 100


async def test_workspace_read_only_is_enforced(flavor: str) -> None:
    async with FLAVORS[flavor](spec(flavor)) as a:
        res = await a.execute_sql(
            f"SELECT COUNT(*) AS n FROM {DB}.orders", None, read_only=True, max_rows=5, timeout_s=10
        )
        assert res.rows == [[200]] and not res.committed
    root_spec = spec(flavor, username=env(flavor, "USER", "root"), password=env(flavor, "PASSWORD", ""))
    async with FLAVORS[flavor](root_spec) as a:
        with pytest.raises(Exception, match="(?i)read.only"):
            await a.execute_sql(f"DELETE FROM {DB}.no_pk", None, read_only=True, max_rows=5, timeout_s=10)


@pytest.mark.capability("administration")
async def test_admin_actions_execute_with_admin_account(flavor: str) -> None:
    root_spec = spec(flavor, username=env(flavor, "USER", "root"), password=env(flavor, "PASSWORD", ""))
    async with FLAVORS[flavor](root_spec) as a:
        stmt = a.build_admin("create_user", {"name": "dbm_it_created@%", "password": "Created-Pass-1!"})
        assert "Created-Pass-1!" not in " ".join(stmt.display)
        await a.run_admin(stmt)
        await a.run_admin(
            a.build_admin(
                "grant",
                {"privileges": ["SELECT"], "object_type": "database", "database": DB, "grantee": "dbm_it_created@%"},
            )
        )
        await a.run_admin(
            a.build_admin("create_index", {"database": DB, "table": "orders", "name": "ix_total", "columns": ["total"]})
        )
        idx = {o.name for o in await a.list_objects(DB, DB, "index")}
        assert "ix_total" in idx
        privs = await a.get_privileges(database=DB, grantee="dbm_it_created")
        assert any(p.privilege == "SELECT" for p in privs)
        await a.run_admin(a.build_admin("drop_user", {"name": "dbm_it_created@%"}))


@pytest.mark.capability("connection")
async def test_error_classification(flavor: str) -> None:
    with pytest.raises(AdapterAuthenticationError):
        async with FLAVORS[flavor](spec(flavor, password="wrong-password")) as a:
            await a.test_connection()
    with pytest.raises(AdapterDatabaseNotFound):
        async with FLAVORS[flavor](spec(flavor, database=f"nope_{uuid.uuid4().hex[:6]}")) as a:
            await a.test_connection()
    with pytest.raises(AdapterConnectionError):
        async with FLAVORS[flavor](spec(flavor, port=1, connect_timeout=2)) as a:
            await a.test_connection()


@pytest.mark.capability("recommendations", "security")
async def test_rules_fire_on_real_evidence(flavor: str) -> None:
    from app.advisors.engine import EvidenceContext, evaluate

    root_spec = spec(flavor, username=env(flavor, "USER", "root"), password=env(flavor, "PASSWORD", ""))
    async with FLAVORS[flavor](root_spec) as a:
        ctx = EvidenceContext(
            server_id="it",
            server_name="it",
            engine=flavor,
            environment_tier="production",
            tls_mode="verify-full",
            is_demo=False,
            window_hours=6,
        )
        ctx.security = await a.get_security_context()
        ctx.tuning = await a.get_tuning_context()
    perf = {f.rule.rule_id: f for f in evaluate(ctx, "performance")}
    sec = {f.rule.rule_id for f in evaluate(ctx, "security")}
    assert "MYSQL-PERF-004" in perf and any(
        DB in (f.result.affected_object or "")
        for f in evaluate(ctx, "performance")
        if f.rule.rule_id == "MYSQL-PERF-004"
    )
    assert all(r.startswith(("MYSQL-", "GEN-")) for r in sec | set(perf)), "no other engine's rules may apply"
    if ctx.security["require_secure_transport"] == "OFF":
        assert "MYSQL-SEC-003" in sec
