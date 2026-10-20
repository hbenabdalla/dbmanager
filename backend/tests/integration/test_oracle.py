"""Oracle adapter against a real database (python-oracledb Thin mode, no Instant Client).

Target: the official Oracle Container Registry image
``container-registry.oracle.com/database/express:21.3.0-xe`` (CDB ``XE``, PDB ``XEPDB1``), or
``database/enterprise:21.3.0.0`` (``oracle-ee`` profile, after ``docker login`` and accepting
Oracle's license on the registry). DB Manager neither downloads nor redistributes Oracle software:
``scripts/integration-test oracle`` pulls the image on *your* machine, under your acceptance.

Enabled when DBM_IT_ORACLE_HOST is set. DBM_IT_ORACLE_USER (``system``) only prepares fixtures:

* in the PDB: application schema DBM_IT_SHOP (tables, index, view, sequence, procedure, function,
  package, trigger, synonym, 5 000 customers / 20 000 orders), role DBM_IT_REPORTING, user
  DBM_IT_APP, monitoring user DBM_IT_MONITOR (CREATE SESSION + SELECT_CATALOG_ROLE — the set in
  docs/database-engines/oracle.md) and admin user DBM_IT_ADMIN (CREATE/ALTER/DROP USER,
  CREATE ROLE, GRANT ANY ROLE, ALTER SYSTEM, CREATE ANY INDEX)
* in the CDB root: common monitoring user C##DBM_IT_MONITOR (CONTAINER = ALL)

Licensing: Diagnostics and Tuning Pack are never marked as licensed here, and a test proves from
V$SQL that the monitoring user never touched AWR, ASH, ADDM, SQL Monitor or SQL Tuning Advisor —
even though an Enterprise Edition container would technically allow it.
"""

from __future__ import annotations

import os
import secrets
import threading
import time
from collections.abc import Iterator
from pathlib import Path

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
from app.adapters.oracle.adapter import OracleAdapter
from tests.integration.matrix import record_version
from tests.integration.storage_series import size_series

pytestmark = [
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(
        not os.environ.get("DBM_IT_ORACLE_HOST"),
        reason="DBM_IT_ORACLE_HOST not set (scripts/integration-test oracle pulls Oracle XE 21c from container-registry.oracle.com)",
    ),
]
PDB_USERS = ("DBM_IT_MONITOR", "DBM_IT_ADMIN", "DBM_IT_APP", "DBM_IT_CREATED", "DBM_IT_SHOP", "DBM_IT_DBA")
PW = {k: "It" + secrets.token_hex(10) for k in ("monitor", "admin", "app", "shop", "created", "common", "dba")}


def env(key: str, default: str = "") -> str:
    return os.environ.get(f"DBM_IT_ORACLE_{key}", default)


PDB = env("SERVICE", "XEPDB1")
CDB = env("CDB_SERVICE", "XE")


def spec(user: str = "DBM_IT_MONITOR", password: str | None = None, service: str = PDB, **kw: object) -> ConnectionSpec:
    base: dict[str, object] = dict(
        host=env("HOST"),
        port=int(env("PORT", "1521")),
        username=user,
        password=PW["monitor"] if password is None else password,
        tls_mode="disable",
        statement_timeout=60,
        options={"service_name": service},
    )
    base.update(kw)
    return ConnectionSpec(**base)  # type: ignore[arg-type]


def _conn(user: str | None = None, password: str | None = None, service: str = PDB):  # type: ignore[no-untyped-def]
    import oracledb

    return oracledb.connect(
        user=user or env("USER", "system"),
        password=password or env("PASSWORD"),
        host=env("HOST"),
        port=int(env("PORT", "1521")),
        service_name=service,
    )


def _run(cur, stmts: tuple[str, ...] | list[str], ignore: tuple[int, ...] = ()) -> None:  # type: ignore[no-untyped-def]
    import oracledb

    for s in stmts:
        try:
            cur.execute(s)
        except oracledb.DatabaseError as exc:
            if exc.args[0].code not in ignore:
                raise


SHOP = (
    "CREATE TABLE DBM_IT_SHOP.CUSTOMERS (ID NUMBER PRIMARY KEY, EMAIL VARCHAR2(200) NOT NULL, COUNTRY CHAR(2) NOT NULL)",
    "CREATE INDEX DBM_IT_SHOP.IX_CUSTOMERS_COUNTRY ON DBM_IT_SHOP.CUSTOMERS (COUNTRY)",
    "CREATE TABLE DBM_IT_SHOP.ORDERS (ID NUMBER PRIMARY KEY, CUSTOMER_ID NUMBER NOT NULL REFERENCES DBM_IT_SHOP.CUSTOMERS, "
    "TOTAL NUMBER(10,2) NOT NULL)",
    "CREATE SEQUENCE DBM_IT_SHOP.INVOICE_SEQ",
    "CREATE VIEW DBM_IT_SHOP.V_TOTALS AS SELECT CUSTOMER_ID, SUM(TOTAL) TOTAL FROM DBM_IT_SHOP.ORDERS GROUP BY CUSTOMER_ID",
    "CREATE FUNCTION DBM_IT_SHOP.ORDER_COUNT RETURN NUMBER AS N NUMBER; BEGIN SELECT COUNT(*) INTO N FROM DBM_IT_SHOP.ORDERS; RETURN N; END;",
    "CREATE PROCEDURE DBM_IT_SHOP.TOUCH AS BEGIN NULL; END;",
    "CREATE PACKAGE DBM_IT_SHOP.SHOP_API AS FUNCTION TOTAL_FOR(P NUMBER) RETURN NUMBER; END;",
    "CREATE PACKAGE BODY DBM_IT_SHOP.SHOP_API AS FUNCTION TOTAL_FOR(P NUMBER) RETURN NUMBER AS T NUMBER; "
    "BEGIN SELECT SUM(TOTAL) INTO T FROM DBM_IT_SHOP.ORDERS WHERE CUSTOMER_ID = P; RETURN T; END; END;",
    "CREATE TRIGGER DBM_IT_SHOP.TRG_ORDERS BEFORE INSERT ON DBM_IT_SHOP.ORDERS FOR EACH ROW BEGIN NULL; END;",
    "CREATE SYNONYM DBM_IT_SHOP.ORD FOR DBM_IT_SHOP.ORDERS",
    "INSERT INTO DBM_IT_SHOP.CUSTOMERS SELECT LEVEL, 'c' || LEVEL || '@x.test', CASE MOD(LEVEL, 2) WHEN 0 THEN 'DE' ELSE 'FR' END "
    "FROM DUAL CONNECT BY LEVEL <= 5000",
    "INSERT INTO DBM_IT_SHOP.ORDERS SELECT LEVEL, 1 + MOD(LEVEL, 5000), MOD(LEVEL, 97) FROM DUAL CONNECT BY LEVEL <= 20000",
)


@pytest.fixture(scope="module", autouse=True)
def fixtures() -> Iterator[None]:
    conn = _conn()
    cur = conn.cursor()
    record_version(
        "oracle", cur.execute("SELECT VERSION_FULL FROM PRODUCT_COMPONENT_VERSION WHERE ROWNUM = 1").fetchone()[0]
    )
    _run(cur, [f"DROP USER {u} CASCADE" for u in PDB_USERS] + ["DROP ROLE DBM_IT_REPORTING"], ignore=(1918, 1919))
    _run(
        cur,
        (
            f'CREATE USER DBM_IT_SHOP IDENTIFIED BY "{PW["shop"]}" QUOTA UNLIMITED ON USERS',
            "GRANT CREATE SESSION TO DBM_IT_SHOP",
            *SHOP,
            "BEGIN DBMS_STATS.GATHER_SCHEMA_STATS('DBM_IT_SHOP'); END;",
            # least-privilege monitoring user (docs/database-engines/oracle.md)
            f'CREATE USER DBM_IT_MONITOR IDENTIFIED BY "{PW["monitor"]}"',
            "GRANT CREATE SESSION, SELECT_CATALOG_ROLE TO DBM_IT_MONITOR",
            "GRANT READ ON DBM_IT_SHOP.ORDERS TO DBM_IT_MONITOR",  # only for EXPLAIN of application SQL
            "GRANT READ ON DBM_IT_SHOP.CUSTOMERS TO DBM_IT_MONITOR",
            f'CREATE USER DBM_IT_ADMIN IDENTIFIED BY "{PW["admin"]}"',
            "GRANT CREATE USER, ALTER USER, DROP USER, CREATE ROLE, GRANT ANY ROLE, ALTER SYSTEM, "
            "CREATE ANY INDEX TO DBM_IT_ADMIN",
            # Create user grants CREATE SESSION to the new user: that needs ADMIN OPTION on it
            "GRANT CREATE SESSION TO DBM_IT_ADMIN WITH ADMIN OPTION",
            # an extra DBA holder so the security evidence has a non-built-in DBA grantee to report
            f'CREATE USER DBM_IT_DBA IDENTIFIED BY "{PW["dba"]}"',
            "GRANT DBA TO DBM_IT_DBA",
            "GRANT SELECT_CATALOG_ROLE TO DBM_IT_ADMIN",
            "GRANT SELECT, UPDATE ON DBM_IT_SHOP.ORDERS TO DBM_IT_ADMIN",
            "CREATE ROLE DBM_IT_REPORTING",
            "GRANT SELECT ON DBM_IT_SHOP.ORDERS TO DBM_IT_REPORTING",
            f'CREATE USER DBM_IT_APP IDENTIFIED BY "{PW["app"]}"',
            "GRANT CREATE SESSION, DBM_IT_REPORTING TO DBM_IT_APP",
            "GRANT UPDATE (EMAIL) ON DBM_IT_SHOP.CUSTOMERS TO DBM_IT_APP",
        ),
    )
    conn.commit()
    conn.close()
    # workload as the application: one statement shape, many executions with binds
    app = _conn("DBM_IT_SHOP", PW["shop"])
    ac = app.cursor()
    for i in range(50):
        ac.execute(
            "SELECT COUNT(*) FROM DBM_IT_SHOP.ORDERS O JOIN DBM_IT_SHOP.CUSTOMERS C ON C.ID = O.CUSTOMER_ID WHERE C.COUNTRY = :c",
            c="DE" if i % 2 else "FR",
        ).fetchall()
    app.close()
    # common monitoring user in the CDB root
    root = _conn(service=CDB)
    rc = root.cursor()
    _run(rc, ["DROP USER C##DBM_IT_MONITOR CASCADE"], ignore=(1918,))
    _run(
        rc,
        (
            f'CREATE USER C##DBM_IT_MONITOR IDENTIFIED BY "{PW["common"]}" CONTAINER = ALL',
            "GRANT CREATE SESSION, SELECT_CATALOG_ROLE TO C##DBM_IT_MONITOR CONTAINER = ALL",
            # without CONTAINER_DATA a common user in the root sees only CDB$ROOT rows in V$/CDB_ views
            "ALTER USER C##DBM_IT_MONITOR SET CONTAINER_DATA = ALL CONTAINER = CURRENT",
        ),
    )
    root.close()
    yield
    conn = _conn()
    _run(
        conn.cursor(),
        [f"DROP USER {u} CASCADE" for u in PDB_USERS] + ["DROP ROLE DBM_IT_REPORTING"],
        ignore=(1918, 1919, 1940),
    )
    conn.close()
    root = _conn(service=CDB)
    _run(root.cursor(), ["DROP USER C##DBM_IT_MONITOR CASCADE"], ignore=(1918, 1940))
    root.close()


@pytest.mark.capability("connection")
async def test_connection_pdb_service() -> None:
    async with OracleAdapter(spec()) as a:
        info = await a.test_connection()
        assert info.version_num and info.version_num >= 190000
        assert info.current_user == "DBM_IT_MONITOR"
        assert info.extra["cdb"] is True and info.extra["in_root"] is False
        assert info.extra["container"] == PDB.split(".")[0].upper()
        assert info.extra["network_protocol"] in ("tcp", "TCP")
        caps = await a.discover_capabilities()
        assert caps[Capability.SESSIONS].level is CapabilityLevel.FULL
        assert caps[Capability.LOCKS].level is CapabilityLevel.FULL


@pytest.mark.capability("connection")
async def test_connection_cdb_root_common_user() -> None:
    async with OracleAdapter(spec("C##DBM_IT_MONITOR", PW["common"], service=CDB)) as a:
        info = await a.test_connection()
        assert info.extra["cdb"] is True and info.extra["in_root"] is True
        assert info.extra["container"] == "CDB$ROOT"
        dbs = {d.name for d in await a.list_databases()}  # containers seen from the root
        assert PDB.split(".")[0].upper() in dbs


@pytest.mark.capability("connection")
async def test_connection_tcps() -> None:
    port = env("TCPS_PORT")
    if not port:
        pytest.skip(
            "TCPS listener not configured: the Oracle XE image listens on TCP only; set DBM_IT_ORACLE_TCPS_PORT "
            "(and DBM_IT_ORACLE_TCPS_CA) after adding a TCPS endpoint with a wallet"
        )
    ca = Path(env("TCPS_CA")).read_text() if env("TCPS_CA") else None  # noqa: ASYNC240
    async with OracleAdapter(
        spec(port=int(port), tls_mode="verify-ca", tls_ca_pem=ca, options={"service_name": PDB, "protocol": "tcps"})
    ) as a:
        await a.test_connection()
        assert (await a.get_security_context())["network_encryption"] == "TCPS"


@pytest.mark.capability("inventory")
async def test_inventory_schema_objects() -> None:
    async with OracleAdapter(spec()) as a:
        info = await a.get_server_info()
        container = info.extra["container"]
        assert "DBM_IT_SHOP" in {s.name for s in await a.list_schemas(container)}
        objs = {(o.type, o.name) for o in await a.list_objects(container, "DBM_IT_SHOP")}
        assert {
            ("table", "ORDERS"),
            ("view", "V_TOTALS"),
            ("index", "IX_CUSTOMERS_COUNTRY"),
            ("sequence", "INVOICE_SEQ"),
        } <= objs
        assert {
            ("function", "ORDER_COUNT"),
            ("procedure", "TOUCH"),
            ("package", "SHOP_API"),
            ("trigger", "TRG_ORDERS"),
            ("synonym", "ORD"),
        } <= objs
        detail = await a.get_object_detail(container, "DBM_IT_SHOP", "ORDERS", "table")
        assert [c.name for c in detail.columns] == ["ID", "CUSTOMER_ID", "TOTAL"]
        assert detail.definition and "CREATE TABLE" in detail.definition  # DBMS_METADATA


@pytest.mark.capability("users", "privileges")
async def test_users_roles_privileges() -> None:
    async with OracleAdapter(spec()) as a:
        users = {u.name: u for u in await a.list_users()}
        assert {"DBM_IT_MONITOR", "DBM_IT_ADMIN", "DBM_IT_APP"} <= set(users)
        assert "DBM_IT_REPORTING" in users["DBM_IT_APP"].member_of
        assert users["SYSTEM"].admin_reasons  # DBA role
        assert any("ALTER SYSTEM" in r for r in users["DBM_IT_ADMIN"].admin_reasons)
        privs = await a.get_privileges()
        assert any(
            p.grantee == "DBM_IT_REPORTING" and p.privilege == "SELECT" and "ORDERS" in (p.object_name or "")
            for p in privs
        )
        assert any(
            p.grantee == "DBM_IT_APP" and p.privilege == "UPDATE" and "EMAIL" in (p.object_name or "") for p in privs
        )


def _blocker() -> tuple[object, str, threading.Thread]:
    holder = _conn("DBM_IT_ADMIN", PW["admin"])
    hc = holder.cursor()
    hc.execute("UPDATE DBM_IT_SHOP.ORDERS SET TOTAL = TOTAL + 1 WHERE ID = 1")
    sid = hc.execute("SELECT SYS_CONTEXT('USERENV', 'SID') FROM DUAL").fetchone()[0]

    def wait() -> None:
        try:
            w = _conn("DBM_IT_ADMIN", PW["admin"])
            w.call_timeout = 30_000
            w.cursor().execute("UPDATE DBM_IT_SHOP.ORDERS SET TOTAL = TOTAL + 1 WHERE ID = 1")
            w.rollback()
            w.close()
        except Exception:  # noqa: BLE001, S110 - cancelled by the test
            pass

    t = threading.Thread(target=wait, daemon=True)
    t.start()
    time.sleep(2)
    return holder, str(sid), t


@pytest.mark.capability("sessions", "locks")
async def test_sessions_blocking_cancel() -> None:
    holder, holder_sid, t = _blocker()
    try:
        async with OracleAdapter(spec()) as a:
            sessions = await a.get_sessions()
            blocked = [s for s in sessions if s.blocked_by]
            assert blocked and any(b.startswith(holder_sid + ",") or b == holder_sid for b in blocked[0].blocked_by)
            assert "," in blocked[0].session_id  # 'sid,serial#'
            locks = await a.get_locks()
            assert any(not lk.granted for lk in locks)
            snap = await a.get_performance_metrics()
            assert snap.gauges["sessions.blocked"] >= 1
            with pytest.raises(AdapterPermissionError):  # ALTER SYSTEM is not granted to monitoring
                await a.cancel_session(blocked[0].session_id)
        async with OracleAdapter(spec("DBM_IT_ADMIN", PW["admin"])) as a:
            assert await a.cancel_session(blocked[0].session_id) is True  # ALTER SYSTEM CANCEL SQL (18c+)
    finally:
        holder.rollback()  # type: ignore[attr-defined]
        holder.close()  # type: ignore[attr-defined]
        t.join(timeout=35)


@pytest.mark.capability("metrics")
async def test_metrics() -> None:
    async with OracleAdapter(spec()) as a:
        snap = await a.get_performance_metrics(slow=True)
        g, c = snap.gauges, snap.counters
        assert g["availability.up"] == 1.0 and g["connections.total"] >= 1
        assert {"transactions.commits", "cpu.busy_time_cs", "io.blocks_read"} <= set(c)
        assert any(k.startswith("oracle.") for k in g)
        for reason in snap.unavailable.values():
            assert reason


@pytest.mark.capability("queries")
async def test_query_statistics_from_v_sqlstats() -> None:
    # run the workload right here (the fixture's cursors can age out of a small XE shared pool) and
    # make each execution heavy enough to rank among the top statements by elapsed time
    app = _conn("DBM_IT_SHOP", PW["shop"])
    cur = app.cursor()
    sql = (
        "SELECT COUNT(*) AS DBM_IT_WORKLOAD FROM DBM_IT_SHOP.ORDERS O JOIN DBM_IT_SHOP.CUSTOMERS C "
        "ON C.ID = O.CUSTOMER_ID, (SELECT LEVEL L FROM DUAL CONNECT BY LEVEL <= 40) X WHERE C.COUNTRY = :c"
    )
    for i in range(50):
        cur.execute(sql, c="DE" if i % 2 else "FR").fetchall()
    app.close()
    async with OracleAdapter(spec()) as a:
        stats = await a.get_query_statistics(5000)
        join = [s for s in stats if "DBM_IT_WORKLOAD" in s.query_text.upper()]
        shop = [s.query_text[:80] for s in stats if "DBM_IT_SHOP" in s.query_text.upper()][:5]
        assert join, f"workload not in V$SQLSTATS top {len(stats)}; DBM_IT_SHOP statements seen: {shop}"
        assert join[0].calls >= 50 and ":c" in join[0].query_text.lower()
        assert join[0].query_id  # SQL_ID
        # plan identity for regression / plan-change detection (0.13): distinct cached plans and the
        # plan_hash_value that accounts for most of the elapsed time
        assert join[0].plans is not None and join[0].plans >= 1
        assert join[0].plan_id is not None and join[0].plan_id > 0


@pytest.mark.capability("explain")
async def test_explain_estimate_and_actual() -> None:
    async with OracleAdapter(spec()) as a:
        est = await a.explain_query(
            "SELECT * FROM DBM_IT_SHOP.ORDERS O JOIN DBM_IT_SHOP.CUSTOMERS C ON C.ID = O.CUSTOMER_ID WHERE O.TOTAL > 50"
        )
        assert est.root is not None and not est.executed and est.raw
        act = await a.explain_query("SELECT COUNT(*) FROM DBM_IT_SHOP.ORDERS WHERE TOTAL > 50", analyze=True)
        assert act.executed and act.root is not None and act.root.actual_rows is not None


@pytest.mark.capability("storage")
async def test_configuration_replication_storage() -> None:
    async with OracleAdapter(spec()) as a:
        assert any(p.name == "processes" for p in await a.get_configuration())
        rep = await a.get_replication_status()
        assert rep.role in ("standalone", "primary")
        st = await a.get_storage_metrics()
        assert st.total_bytes and st.tablespaces
        assert all({"schema", "name", "table_bytes", "index_bytes", "total_bytes"} <= set(t) for t in st.top_tables)
        series = size_series("oracle", await a.list_databases(), st.tablespaces)
        assert "SYSTEM" in series["db_tablespace_used_bytes"] and series["db_tablespace_max_bytes"]


@pytest.mark.capability("security", "recommendations")
async def test_security_evidence_rules_and_licensing() -> None:
    from app.advisors.engine import EvidenceContext, evaluate

    async with OracleAdapter(spec()) as a:
        sec = await a.get_security_context()
        tun = await a.get_tuning_context()
        caps = await a.discover_capabilities()
    assert sec["network_encryption"] == "NOT_REQUIRED"  # plain TCP worked
    assert "DBM_IT_DBA" in (sec["dba_role_grantees"] or [])  # Oracle-maintained users are not reported
    assert tun["diagnostics_pack_licensed"] is False and tun["tuning_pack_licensed"] is False
    assert "Diagnostics Pack" in (caps[Capability.QUERY_HISTORY].note or "")
    ctx = EvidenceContext(
        server_id="it",
        server_name="it",
        engine="oracle",
        environment_tier="production",
        tls_mode="disable",
        is_demo=False,
        window_hours=1,
        security=sec,
        tuning=tun,
    )
    findings = evaluate(ctx, "security") + evaluate(ctx, "performance")
    assert all(f.rule.applies_to("oracle") for f in findings)
    assert any(f.rule.rule_id.startswith(("ORA-SEC", "GEN-SEC")) for f in findings)
    # proof from the database itself: nothing licensed was queried by the monitoring user
    conn = _conn()
    n_total, n_licensed = (
        conn.cursor()
        .execute(
            """SELECT COUNT(*),
                  COUNT(CASE WHEN UPPER(SQL_TEXT) LIKE '%DBA_HIST%' OR UPPER(SQL_TEXT) LIKE '%ACTIVE_SESSION_HISTORY%'
                               OR UPPER(SQL_TEXT) LIKE '%DBA_ADDM%' OR UPPER(SQL_TEXT) LIKE '%DBMS_SQLTUNE%'
                               OR UPPER(SQL_TEXT) LIKE '%SQL_MONITOR%' OR UPPER(SQL_TEXT) LIKE '%DBMS_WORKLOAD_REPOSITORY%'
                             THEN 1 END)
             FROM V$SQL WHERE PARSING_SCHEMA_NAME = 'DBM_IT_MONITOR'"""
        )
        .fetchone()
    )
    conn.close()
    assert n_total > 0 and n_licensed == 0


@pytest.mark.capability("administration")
async def test_administration_guarded_actions() -> None:
    async with OracleAdapter(spec("DBM_IT_ADMIN", PW["admin"])) as a:
        stmt = a.build_admin("create_user", {"name": "DBM_IT_CREATED", "password": PW["created"]})
        assert PW["created"] not in " ".join(stmt.statements + stmt.display)  # bind variable
        await a.run_admin(stmt)
        assert "DBM_IT_CREATED" in {u.name for u in await a.list_users()}
        await a.run_admin(
            a.build_admin("grant", {"object_type": "role", "role": "DBM_IT_REPORTING", "grantee": "DBM_IT_CREATED"})
        )
        await a.run_admin(
            a.build_admin(
                "create_index",
                {"schema": "DBM_IT_SHOP", "table": "ORDERS", "name": "IX_ORDERS_TOTAL", "columns": ["TOTAL"]},
            )
        )
        await a.run_admin(a.build_admin("drop_user", {"name": "DBM_IT_CREATED"}))
    conn = _conn()
    n = conn.cursor().execute("SELECT COUNT(*) FROM V$SQL WHERE INSTR(SQL_TEXT, :p) > 0", p=PW["created"]).fetchone()[0]
    conn.close()
    assert n == 0  # the password never reached the shared pool as SQL text
    async with OracleAdapter(spec()) as a:
        with pytest.raises(AdapterPermissionError):
            await a.run_admin(a.build_admin("create_role", {"name": "DBM_IT_NOPE"}))


async def test_read_only_workspace() -> None:
    async with OracleAdapter(spec("DBM_IT_ADMIN", PW["admin"])) as a:
        with pytest.raises(AdapterPermissionError):  # SET TRANSACTION READ ONLY → ORA-01456
            await a.execute_sql(
                "UPDATE DBM_IT_SHOP.ORDERS SET TOTAL = 0", None, read_only=True, max_rows=10, timeout_s=10
            )
        res = await a.execute_sql(
            "SELECT COUNT(*) N FROM DBM_IT_SHOP.ORDERS", None, read_only=True, max_rows=10, timeout_s=10
        )
        assert res.rows == [[20000]]


@pytest.mark.capability("connection")
async def test_error_classification() -> None:
    with pytest.raises(AdapterAuthenticationError):
        async with OracleAdapter(spec(password="wrong")) as a:
            await a.test_connection()
    with pytest.raises(AdapterDatabaseNotFound):  # ORA-12514: unknown service
        async with OracleAdapter(spec(service="DBM_IT_NO_SUCH_SERVICE")) as a:
            await a.test_connection()
    with pytest.raises(AdapterConnectionError):
        async with OracleAdapter(spec(port=1, connect_timeout=3)) as a:
            await a.test_connection()
