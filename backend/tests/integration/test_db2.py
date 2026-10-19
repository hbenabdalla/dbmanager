"""Db2 LUW adapter against a real database (icr.io/db2_community/db2, Community Edition).

``scripts/integration-test db2`` starts the container (DB2INSTANCE=db2inst1, DBNAME=TESTDB), waits
until the instance owner can really connect to TESTDB, runs
``tests/integration/db2/custom/dbm-it-users.sh`` (OS users DBMMON / DBMADM / DBMRO with the grants
from docs/database-engines/db2-luw.md, explain tables for DBMMON) and runs this suite.

Enabled when DBM_IT_DB2_HOST is set. The instance owner (DBM_IT_DB2_USER) only prepares fixtures:
schema SHOP with tables, index, view, sequence, procedure, function and trigger, 5 000 customers
and 20 000 orders, a role, a bound workload and a real lock wait. Checks run as:

* DBMMON — monitoring (CONNECT, SQLADM, EXPLAIN, EXECUTE on ENV_GET_SYSTEM_RESOURCES etc.)
* DBMADM — administration (ACCESSCTRL, WLMADM, CREATEIN/ALTERIN on SHOP)
* DBMRO  — read-only (SELECTIN on SHOP)

DB Manager never creates Db2 users (authentication is external) and never runs REORG/RUNSTATS:
both are asserted here.
"""

from __future__ import annotations

import os
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
from app.adapters.db2.adapter import Db2Adapter
from tests.integration.matrix import record_version
from tests.integration.storage_series import size_series

pytestmark = [
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(
        not os.environ.get("DBM_IT_DB2_HOST"),
        reason="DBM_IT_DB2_HOST not set (scripts/integration-test db2 starts Db2 Community Edition from icr.io)",
    ),
]


def env(key: str, default: str = "") -> str:
    return os.environ.get(f"DBM_IT_DB2_{key}", default)


DB = env("DB", "TESTDB")
USERS = {"mon": "DBMMON", "adm": "DBMADM", "ro": "DBMRO"}


def _pw(kind: str) -> str:
    pw = env(f"{kind.upper()}_PASSWORD")
    if not pw:
        pytest.skip(
            f"DBM_IT_DB2_{kind.upper()}_PASSWORD not set: the least-privilege identities come from dbm-it-users.sh"
        )
    return pw


def spec(kind: str = "mon", **kw: object) -> ConnectionSpec:
    base: dict[str, object] = dict(
        host=env("HOST"),
        port=int(env("PORT", "50000")),
        database=DB,
        username=USERS[kind].lower() if kind in USERS else env("USER", "db2inst1"),
        password=_pw(kind) if kind in USERS else env("PASSWORD"),
        tls_mode="disable",
        statement_timeout=60,
    )
    base.update(kw)
    return ConnectionSpec(**base)  # type: ignore[arg-type]


def _conn(user: str | None = None, password: str | None = None):  # type: ignore[no-untyped-def]
    import ibm_db_dbi

    return ibm_db_dbi.connect(
        f"DATABASE={DB};HOSTNAME={env('HOST')};PORT={env('PORT', '50000')};PROTOCOL=TCPIP;"
        f"UID={user or env('USER', 'db2inst1')};PWD={password or env('PASSWORD')};",
        "",
        "",
    )


def _run(cur, stmts, ignore: tuple[str, ...] = ()) -> None:  # type: ignore[no-untyped-def]
    for s in stmts:
        try:
            cur.execute(s)
        except Exception as exc:
            if not any(code in str(exc) for code in ignore):
                raise


DROP = [
    "DROP TRIGGER SHOP.TRG_ORDERS",
    "DROP VIEW SHOP.V_TOTALS",
    "DROP PROCEDURE SHOP.TOUCH",
    "DROP FUNCTION SHOP.ORDER_COUNT",
    "DROP TABLE SHOP.ORDERS",
    "DROP TABLE SHOP.CUSTOMERS",
    "DROP SEQUENCE SHOP.INVOICE_SEQ",
    "DROP ROLE REPORTING",
]
SHOP = (
    "CREATE TABLE SHOP.CUSTOMERS (ID INTEGER NOT NULL PRIMARY KEY, EMAIL VARCHAR(200) NOT NULL, COUNTRY CHAR(2) NOT NULL)",
    "CREATE INDEX SHOP.IX_CUSTOMERS_COUNTRY ON SHOP.CUSTOMERS (COUNTRY)",
    "CREATE TABLE SHOP.ORDERS (ID INTEGER NOT NULL PRIMARY KEY, CUSTOMER_ID INTEGER NOT NULL REFERENCES SHOP.CUSTOMERS, "
    "TOTAL DECIMAL(10,2) NOT NULL)",
    "CREATE SEQUENCE SHOP.INVOICE_SEQ",
    "CREATE VIEW SHOP.V_TOTALS AS SELECT CUSTOMER_ID, SUM(TOTAL) AS TOTAL FROM SHOP.ORDERS GROUP BY CUSTOMER_ID",
    "CREATE FUNCTION SHOP.ORDER_COUNT() RETURNS INTEGER LANGUAGE SQL READS SQL DATA RETURN (SELECT COUNT(*) FROM SHOP.ORDERS)",
    "CREATE PROCEDURE SHOP.TOUCH() LANGUAGE SQL BEGIN END",
    "CREATE TRIGGER SHOP.TRG_ORDERS NO CASCADE BEFORE INSERT ON SHOP.ORDERS REFERENCING NEW AS N FOR EACH ROW SET N.TOTAL = N.TOTAL",
    "INSERT INTO SHOP.CUSTOMERS WITH G(N) AS (VALUES 1 UNION ALL SELECT N + 1 FROM G WHERE N < 5000) "
    "SELECT N, 'c' || VARCHAR(N) || '@x.test', CASE MOD(N, 2) WHEN 0 THEN 'DE' ELSE 'FR' END FROM G",
    "INSERT INTO SHOP.ORDERS WITH G(N) AS (VALUES 1 UNION ALL SELECT N + 1 FROM G WHERE N < 20000) "
    "SELECT N, 1 + MOD(N, 5000), MOD(N, 97) FROM G",
    "CREATE ROLE REPORTING",
    "GRANT SELECT ON SHOP.ORDERS TO ROLE REPORTING",
    "GRANT ROLE REPORTING TO USER DBMRO",
    "GRANT SELECT, UPDATE, INDEX ON SHOP.ORDERS TO USER DBMADM",
    "GRANT SELECT ON SHOP.ORDERS TO USER DBMMON",  # only for EXPLAIN of application SQL
    "GRANT SELECT ON SHOP.CUSTOMERS TO USER DBMMON",
)


@pytest.fixture(scope="module", autouse=True)
def fixtures() -> Iterator[None]:
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT SERVICE_LEVEL FROM SYSIBMADM.ENV_INST_INFO")
    record_version("db2", cur.fetchone()[0])
    _run(cur, DROP, ignore=("SQL0204N",))
    _run(cur, SHOP)
    conn.commit()
    # workload: one statement shape with a parameter marker, many executions
    for i in range(50):
        cur.execute(
            "SELECT COUNT(*) FROM SHOP.ORDERS O JOIN SHOP.CUSTOMERS C ON C.ID = O.CUSTOMER_ID WHERE C.COUNTRY = ?",
            ("DE" if i % 2 else "FR",),
        )
        cur.fetchall()
    conn.commit()
    conn.close()
    yield
    conn = _conn()
    _run(conn.cursor(), DROP + ["DROP INDEX SHOP.IX_ORDERS_TOTAL"], ignore=("SQL0204N", "SQL0478N"))
    conn.commit()
    conn.close()


@pytest.mark.capability("connection")
async def test_connection_and_capabilities() -> None:
    async with Db2Adapter(spec()) as a:
        info = await a.test_connection()
        assert info.version_num and info.version_num >= 11_050_000
        assert (info.current_user or "").upper() == "DBMMON"
        assert (info.extra["database"] or "").upper() == DB.upper() and (
            info.extra["instance"] or ""
        ).lower() == "db2inst1"
        assert "SQLADM" in info.extra["authorities"] and info.extra["monitor_routines_executable"]
        caps = await a.discover_capabilities()
        for c in (Capability.SESSIONS, Capability.LOCKS, Capability.QUERY_STATISTICS, Capability.EXPLAIN):
            assert caps[c].level is CapabilityLevel.FULL, (c, caps[c])


@pytest.mark.capability("connection")
async def test_read_only_identity_cannot_monitor() -> None:
    async with Db2Adapter(spec("ro")) as a:
        caps = await a.discover_capabilities()
        assert caps[Capability.SESSIONS].level is CapabilityLevel.REQUIRES_PRIVILEGE
        assert "MON_GET" in (caps[Capability.SESSIONS].note or "")


@pytest.mark.capability("inventory")
async def test_inventory() -> None:
    async with Db2Adapter(spec()) as a:
        assert "SHOP" in {s.name for s in await a.list_schemas(DB)}
        objs = {(o.type, o.name) for o in await a.list_objects(DB, "SHOP")}
        assert {
            ("table", "ORDERS"),
            ("view", "V_TOTALS"),
            ("index", "IX_CUSTOMERS_COUNTRY"),
            ("sequence", "INVOICE_SEQ"),
        } <= objs
        assert {("function", "ORDER_COUNT"), ("procedure", "TOUCH"), ("trigger", "TRG_ORDERS")} <= objs
        detail = await a.get_object_detail(DB, "SHOP", "ORDERS", "table")
        assert [c.name for c in detail.columns] == ["ID", "CUSTOMER_ID", "TOTAL"]
        assert detail.constraints


@pytest.mark.capability("users", "privileges")
async def test_authorization_ids_roles_privileges() -> None:
    async with Db2Adapter(spec()) as a:
        users = {u.name: u for u in await a.list_users()}
        assert {"DBMMON", "DBMADM", "DBMRO"} <= set(users)
        assert "REPORTING" in users["DBMRO"].member_of
        assert any("ACCESSCTRL" in r for r in users["DBMADM"].admin_reasons)
        privs = await a.get_privileges()
        assert any(
            p.grantee == "REPORTING" and p.privilege == "SELECT" and "ORDERS" in (p.object_name or "") for p in privs
        )
        assert any(p.grantee == "DBMRO" and p.privilege == "SELECTIN" for p in privs)


def _blocker() -> tuple[object, threading.Thread]:
    holder = _conn("dbmadm", _pw("adm"))
    hc = holder.cursor()
    hc.execute("UPDATE SHOP.ORDERS SET TOTAL = TOTAL + 1 WHERE ID = 1")  # autocommit off: lock held

    def wait() -> None:
        try:
            w = _conn("dbmadm", _pw("adm"))
            wc = w.cursor()
            wc.execute("SET CURRENT LOCK TIMEOUT 30")
            wc.execute("UPDATE SHOP.ORDERS SET TOTAL = TOTAL + 1 WHERE ID = 1")
            w.rollback()
            w.close()
        except Exception:  # noqa: BLE001, S110 - cancelled by the test or timed out
            pass

    t = threading.Thread(target=wait, daemon=True)
    t.start()
    time.sleep(3)
    return holder, t


@pytest.mark.capability("sessions", "locks")
async def test_sessions_lock_wait_and_cancel() -> None:
    holder, t = _blocker()
    try:
        async with Db2Adapter(spec()) as a:
            sessions = await a.get_sessions()
            blocked = [s for s in sessions if s.blocked_by]
            assert blocked and blocked[0].session_id.isdigit()  # application handle
            locks = await a.get_locks()
            assert any(not lk.granted and lk.blocked_by for lk in locks)
            snap = await a.get_performance_metrics()
            assert snap.gauges["sessions.blocked"] >= 1
        # SQLADM (granted to the monitoring user) also permits WLM_CANCEL_ACTIVITY, so the negative
        # check uses the read-only identity, which holds neither SQLADM nor WLMADM
        async with Db2Adapter(spec("ro")) as a:
            with pytest.raises((AdapterPermissionError, AdapterNotSupported)):
                await a.cancel_session(blocked[0].session_id)
        async with Db2Adapter(spec("adm")) as a:  # WLMADM: WLM_CANCEL_ACTIVITY
            assert await a.cancel_session(blocked[0].session_id) is True
            with pytest.raises(AdapterPermissionError):  # FORCE APPLICATION needs SYSMAINT: not granted
                await a.cancel_session(blocked[0].session_id, terminate=True)
    finally:
        holder.rollback()  # type: ignore[attr-defined]
        holder.close()  # type: ignore[attr-defined]
        t.join(timeout=35)


@pytest.mark.capability("metrics")
async def test_metrics_from_mon_get_functions() -> None:
    async with Db2Adapter(spec()) as a:
        snap = await a.get_performance_metrics(slow=True)
        g, c = snap.gauges, snap.counters
        assert g["availability.up"] == 1.0 and g["connections.total"] >= 1
        assert {"transactions.commits", "io.blocks_read", "io.blocks_hit"} <= set(c)  # MON_GET_DATABASE / BUFFERPOOL
        assert "db2.log_used_percent" in g  # MON_GET_TRANSACTION_LOG
        for reason in snap.unavailable.values():
            assert reason


@pytest.mark.capability("queries")
async def test_query_statistics_from_package_cache() -> None:
    conn = _conn()
    cur = conn.cursor()
    sql = (
        "SELECT COUNT(*) AS DBM_IT_WORKLOAD FROM SHOP.ORDERS O JOIN SHOP.CUSTOMERS C ON C.ID = O.CUSTOMER_ID, "
        "(SELECT 1 FROM SYSIBM.SYSDUMMY1 UNION ALL SELECT 2 FROM SYSIBM.SYSDUMMY1) X WHERE C.COUNTRY = ?"
    )
    for i in range(50):
        cur.execute(sql, ("DE" if i % 2 else "FR",))
        cur.fetchall()
    conn.commit()
    conn.close()
    async with Db2Adapter(spec()) as a:
        stats = await a.get_query_statistics(1000)
        join = [s for s in stats if "DBM_IT_WORKLOAD" in s.query_text.upper()]
        shop = [s.query_text[:80] for s in stats if "SHOP." in s.query_text.upper()][:5]
        assert join, f"workload not in MON_GET_PKG_CACHE_STMT top {len(stats)}; SHOP statements seen: {shop}"
        assert join[0].calls >= 50 and "?" in join[0].query_text  # MON_GET_PKG_CACHE_STMT
        assert join[0].query_id  # EXECUTABLE_ID


@pytest.mark.capability("explain")
async def test_explain_into_monitoring_users_tables() -> None:
    async with Db2Adapter(spec()) as a:
        res = await a.explain_query(
            "SELECT * FROM SHOP.ORDERS O JOIN SHOP.CUSTOMERS C ON C.ID = O.CUSTOMER_ID WHERE O.TOTAL > 50"
        )
        assert res.root is not None and not res.executed and res.raw
        caps = await a.discover_capabilities()
        assert caps[Capability.EXPLAIN_ANALYZE].level is not CapabilityLevel.FULL  # estimates only


@pytest.mark.capability("storage")
async def test_configuration_replication_storage() -> None:
    async with Db2Adapter(spec()) as a:
        assert any(p.name.lower() == "locklist" for p in await a.get_configuration())  # SYSIBMADM.DBCFG
        rep = await a.get_replication_status()
        assert rep.role in ("standalone", "primary")  # HADR off in the test container
        st = await a.get_storage_metrics()
        assert st.total_bytes and st.tablespaces  # MON_GET_TABLESPACE
        series = size_series("db2", await a.list_databases(), st.tablespaces)
        assert "SYSCATSPACE" in series["db_tablespace_used_bytes"]


@pytest.mark.capability("security", "recommendations")
async def test_security_tuning_evidence_and_rules() -> None:
    from app.advisors.engine import EvidenceContext, evaluate

    async with Db2Adapter(spec()) as a:
        sec = await a.get_security_context()
        tun = await a.get_tuning_context()
    assert "DB2INST1" in [h.upper() for h in sec["dbadm_holders"] or []]
    assert "DBMADM" in [h.upper() for h in sec["accessctrl_holders"] or []]
    assert sec["dbm_cfg_readable"] is True and sec["authentication"]
    # freshly loaded tables without statistics: a RUNSTATS *recommendation*, never an execution
    assert isinstance(tun["tables_needing_runstats"], list)
    ctx = EvidenceContext(
        server_id="it",
        server_name="it",
        engine="db2",
        environment_tier="production",
        tls_mode="disable",
        is_demo=False,
        window_hours=1,
        security=sec,
        tuning=tun,
    )
    findings = evaluate(ctx, "security") + evaluate(ctx, "performance")
    assert all(f.rule.applies_to("db2") for f in findings)
    assert any(f.rule.rule_id.startswith(("DB2-SEC", "GEN-SEC")) for f in findings)  # tls disabled at least
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM SYSCAT.TABLES WHERE TABSCHEMA = 'SHOP' AND STATS_TIME IS NOT NULL AND TABNAME = 'ORDERS'"
    )
    stats_collected = cur.fetchone()[0]
    conn.close()
    if not tun.get("auto_runstats", "").upper().startswith("ON"):
        assert stats_collected == 0  # DB Manager did not run RUNSTATS


@pytest.mark.capability("administration")
async def test_administration_guarded_actions() -> None:
    async with Db2Adapter(spec("adm")) as a:
        with pytest.raises(AdapterNotSupported):  # authentication is external: no CREATE USER
            a.build_admin("create_user", {"name": "X", "password": "unused-Pass-1"})
        # ACCESSCTRL grants and revokes object privileges and database authorities …
        grant = {
            "object_type": "table",
            "schema": "SHOP",
            "object_name": "CUSTOMERS",
            "privileges": ["SELECT"],
            "grantee": "DBMRO",
            "grantee_type": "user",
        }
        await a.run_admin(a.build_admin("grant", grant))
        assert any(p.grantee == "DBMRO" and "CUSTOMERS" in (p.object_name or "") for p in await a.get_privileges())
        await a.run_admin(a.build_admin("revoke", grant))
        # … but creating roles needs SECADM, which a DB Manager admin account should not hold
        with pytest.raises(AdapterPermissionError):
            await a.run_admin(a.build_admin("create_role", {"name": "DBM_IT_ROLE"}))
        await a.run_admin(
            a.build_admin(
                "create_index", {"schema": "SHOP", "table": "ORDERS", "name": "IX_ORDERS_TOTAL", "columns": ["TOTAL"]}
            )
        )
        assert "IX_ORDERS_TOTAL" in {o.name for o in await a.list_objects(DB, "SHOP", "index")}
    async with Db2Adapter(spec()) as a:  # Monitoring Only identity cannot change anything
        with pytest.raises(AdapterPermissionError):
            await a.run_admin(
                a.build_admin(
                    "grant",
                    {
                        "object_type": "table",
                        "schema": "SHOP",
                        "object_name": "ORDERS",
                        "privileges": ["SELECT"],
                        "grantee": "DBMRO",
                        "grantee_type": "user",
                    },
                )
            )


async def test_read_only_identity_and_workspace() -> None:
    async with Db2Adapter(spec("ro")) as a:
        with pytest.raises(AdapterPermissionError):  # SELECTIN only
            await a.execute_sql(
                "DELETE FROM SHOP.ORDERS WHERE ID = 1", None, read_only=False, max_rows=10, timeout_s=10
            )
        res = await a.execute_sql(
            "SELECT COUNT(*) AS N FROM SHOP.ORDERS", None, read_only=True, max_rows=10, timeout_s=10
        )
        assert res.rows == [[20000]]


@pytest.mark.capability("connection")
async def test_error_classification() -> None:
    with pytest.raises(AdapterAuthenticationError):  # SQL30082N
        async with Db2Adapter(spec(password="wrong")) as a:
            await a.test_connection()
    with pytest.raises(AdapterDatabaseNotFound):  # SQL1013N / SQL30061N
        async with Db2Adapter(spec(database="NOSUCHDB")) as a:
            await a.test_connection()
    with pytest.raises(AdapterConnectionError):  # SQL30081N
        async with Db2Adapter(spec(port=1, connect_timeout=3)) as a:
            await a.test_connection()
