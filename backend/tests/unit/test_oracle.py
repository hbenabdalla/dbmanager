"""Oracle adapter units with scripted connections: SQL syntax, connection parameters, error
mapping, plan trees, explain flows, CDB/PDB scoping, licensing boundary and administration.
No Oracle instance is available in CI; real-environment validation is pending (see
docs/database-engines/index.md and tests/integration/test_oracle.py)."""

from __future__ import annotations

import asyncio
import re
import ssl
from types import SimpleNamespace

import pytest
import sqlglot

from app.adapters import admin as A
from app.adapters.base import (
    AdapterAuthenticationError,
    AdapterConnectionError,
    AdapterDatabaseNotFound,
    AdapterError,
    AdapterNotSupported,
    AdapterPermissionError,
    AdapterTimeout,
    AdapterTLSError,
    Capability,
    CapabilityLevel,
    ConnectionSpec,
)
from app.adapters.dbapi import AdapterObjectMissing
from app.adapters.oracle import sql as Q
from app.adapters.oracle.adapter import OracleAdapter
from app.adapters.oracle.monitoring import interval_seconds
from app.adapters.oracle.plans import build_tree
from app.domain.plan_analysis import analyze_plan
from tests.unit.fakedb import FakeConnection, attach

SPEC = ConnectionSpec(host="ora.test", port=1521, username="DBM_MONITOR", password="Ora-Secret-1", database="ORCLPDB1")

PLAN = [
    {
        "id": 0,
        "parent_id": None,
        "operation": "SELECT STATEMENT",
        "options": None,
        "object_owner": None,
        "object_name": None,
        "object_type": None,
        "cost": 420,
        "cardinality": 120,
    },
    {
        "id": 1,
        "parent_id": 0,
        "operation": "HASH JOIN",
        "options": None,
        "object_owner": None,
        "object_name": None,
        "object_type": None,
        "cost": 420,
        "cardinality": 120,
        "access_predicates": '"O"."CUSTOMER_ID"="C"."ID"',
    },
    {
        "id": 2,
        "parent_id": 1,
        "operation": "TABLE ACCESS",
        "options": "FULL",
        "object_owner": "SHOP",
        "object_name": "ORDERS",
        "object_type": "TABLE",
        "cost": 400,
        "cardinality": 250000,
        "filter_predicates": '"O"."TOTAL">10',
    },
    {
        "id": 3,
        "parent_id": 1,
        "operation": "INDEX",
        "options": "RANGE SCAN",
        "object_owner": "SHOP",
        "object_name": "CUSTOMERS_PK",
        "object_type": "INDEX (UNIQUE)",
        "cost": 2,
        "cardinality": 1,
    },
]


def ora_error(full_code: str, message: str) -> Exception:
    code = int(full_code.split("-")[1]) if full_code.startswith("ORA-") else None
    return Exception(SimpleNamespace(full_code=full_code, code=code, message=f"{full_code}: {message}"))


def server_row(**over: object) -> dict[str, object]:
    row: dict[str, object] = {
        "instance_name": "ORCLCDB",
        "host_name": "ora01",
        "version": "19.0.0.0.0",
        "startup_time": None,
        "instance_status": "OPEN",
        "database_status": "ACTIVE",
        "instance_role": "PRIMARY_INSTANCE",
        "logins": "ALLOWED",
        "parallel": "NO",
        "db_name": "ORCLCDB",
        "db_unique_name": "ORCLCDB",
        "dbid": 1,
        "log_mode": "ARCHIVELOG",
        "open_mode": "READ WRITE",
        "database_role": "PRIMARY",
        "cdb": "YES",
        "platform_name": "Linux x86 64-bit",
        "force_logging": "NO",
        "flashback_on": "NO",
        "protection_mode": "MAXIMUM PERFORMANCE",
        "created": None,
        "con_name": "ORCLPDB1",
        "con_id": 3,
        "session_user": "DBM_MONITOR",
        "network_protocol": "tcp",
        "service_name": "ORCLPDB1",
        "sid": 77,
    }
    row.update(over)
    return row


def base_routes(**over: object) -> list:
    return [
        ("FROM v$instance i CROSS JOIN v$database d", [server_row(**over)]),
        ("SELECT version_full FROM v$instance", [{"version_full": "19.21.0.0.0"}]),
        ("FROM v$version", [{"banner": "Oracle Database 19c Enterprise Edition Release 19.0.0.0.0 - Production"}]),
        ("FROM session_privs", [{"privilege": "CREATE SESSION"}, {"privilege": "SELECT ANY DICTIONARY"}]),
        ("FROM session_roles", [{"role": "SELECT_CATALOG_ROLE"}]),
        (
            "FROM v$osstat",
            [
                {"stat_name": "NUM_CPUS", "value": 8},
                {"stat_name": "BUSY_TIME", "value": 1000},
                {"stat_name": "IDLE_TIME", "value": 9000},
                {"stat_name": "PHYSICAL_MEMORY_BYTES", "value": 2**34},
            ],
        ),
    ]


def adapter(routes: list, **spec: object) -> tuple[OracleAdapter, FakeConnection]:
    a = OracleAdapter(SPEC.model_copy(update=spec) if spec else SPEC)
    return a, attach(a, FakeConnection(routes))


# ---------------------------------------------------------------------------------------------
def test_every_query_parses_as_oracle_sql() -> None:
    local = {
        "v": "dba",
        **{
            k: ""
            for k in (
                "con_u",
                "con_o",
                "con_s",
                "con_s_o",
                "con_tr_o",
                "con_t_o",
                "con_c_i",
                "con_i",
                "con_c",
                "con_cc_c",
            )
        },
    }
    extra = {"types": "'TABLE'", "table_filter": "", "names": "'a'"}
    for name, sql in vars(Q).items():
        if name.isupper() and isinstance(sql, str):
            sqlglot.parse(
                sql.format(**local, **extra) if "{" in sql else sql, read="oracle", error_level=sqlglot.ErrorLevel.RAISE
            )


def test_no_management_pack_views_are_queried() -> None:
    """Licensing boundary: AWR, ASH, ADDM, SQL Monitor, metric views and tuning APIs are absent."""
    forbidden = re.compile(
        r"v\$active_session_history|dba_hist_|dba_addm|dbms_workload_repository|dbms_sqltune|dbms_advisor|"
        r"v\$sql_monitor|display_awr|v\$sysmetric|v\$metric|gv\$active_session",
        re.I,
    )
    import inspect

    from app.adapters.oracle import adapter as m_adapter
    from app.adapters.oracle import evidence, inventory, monitoring

    sources = [inspect.getsource(m) for m in (m_adapter, evidence, inventory, monitoring)]
    queries = [v for k, v in vars(Q).items() if k.isupper() and isinstance(v, str)]
    sql_lines = [ln for src in sources for ln in src.splitlines() if re.search(r"\b(SELECT|FROM|EXEC|BEGIN)\b", ln)]
    hits = [s for s in queries + sql_lines if forbidden.search(s)]
    assert hits == []


def test_connect_params_tls_and_service() -> None:
    p = OracleAdapter(SPEC.model_copy(update={"tls_mode": "verify-full"})).connect_params()
    assert p["protocol"] == "tcps" and p["service_name"] == "ORCLPDB1" and p["ssl_server_dn_match"] is True
    assert isinstance(p["ssl_context"], ssl.SSLContext) and p["ssl_context"].verify_mode == ssl.CERT_REQUIRED
    assert "password" not in p and "user" not in p
    plain = OracleAdapter(
        SPEC.model_copy(update={"tls_mode": "prefer", "options": {"sid": "ORCL"}, "database": None})
    ).connect_params()
    assert plain["protocol"] == "tcp" and plain["sid"] == "ORCL" and "service_name" not in plain
    forced = OracleAdapter(SPEC.model_copy(update={"tls_mode": "require"})).connect_params()
    assert forced["protocol"] == "tcps" and forced["ssl_context"].verify_mode == ssl.CERT_NONE


@pytest.mark.parametrize(
    ("full", "msg", "expected"),
    [
        ("ORA-01017", "invalid username/password; logon denied", AdapterAuthenticationError),
        ("ORA-28000", "the account is locked", AdapterAuthenticationError),
        ("ORA-12514", "listener does not currently know of service requested", AdapterDatabaseNotFound),
        ("ORA-12541", "no listener", AdapterConnectionError),
        ("DPY-6005", "cannot connect to database (CONNECTION_ID=x)", AdapterConnectionError),
        (
            "DPY-3001",
            "Native Network Encryption and Data Integrity is only supported in thick mode",
            AdapterConnectionError,
        ),
        ("DPY-6005", "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed", AdapterTLSError),
        ("ORA-01031", "insufficient privileges", AdapterPermissionError),
        ("ORA-00942", "table or view does not exist", AdapterPermissionError),
        ("ORA-00904", '"VERSION_FULL": invalid identifier', AdapterObjectMissing),
        ("DPY-4024", "call timeout of 30000 ms exceeded", AdapterTimeout),
        ("ORA-01013", "user requested cancel of current operation", AdapterTimeout),
    ],
)
def test_error_classification(full: str, msg: str, expected: type) -> None:
    err = OracleAdapter(SPEC)._classify(ora_error(full, msg))
    assert isinstance(err, expected), (full, type(err))


def test_dpy_3001_explains_tcps() -> None:
    err = OracleAdapter(SPEC)._classify(ora_error("DPY-3001", "NNE only in thick mode"))
    assert "TCPS" in str(err)


def test_plan_tree_and_full_scan_detection() -> None:
    root = build_tree(PLAN)
    assert root is not None and root.operator == "SELECT STATEMENT" and root.total_cost == 420
    join = root.children[0]
    assert join.operator == "HASH JOIN" and len(join.children) == 2
    scan, idx = join.children
    assert scan.operator == "TABLE ACCESS FULL" and scan.relation == "SHOP.ORDERS" and "TOTAL" in (scan.filter or "")
    assert idx.index == "CUSTOMERS_PK" and idx.relation is None
    codes = {i.code for i in analyze_plan(root, analyzed=False)}
    assert "FULL_SCAN" in codes


def test_actual_plan_rows_and_temp_spill() -> None:
    rows = [
        {**PLAN[0], "last_starts": 1, "last_output_rows": 3, "last_elapsed_time": 9000},
        {
            "id": 1,
            "parent_id": 0,
            "operation": "SORT",
            "options": "ORDER BY",
            "cost": 10,
            "cardinality": 3,
            "last_starts": 1,
            "last_output_rows": 3,
            "last_elapsed_time": 8000,
            "last_tempseg_size": 1048576,
        },
    ]
    root = build_tree(rows, actual=True)
    assert root is not None and root.actual_rows == 3 and root.actual_total_ms == 9.0
    sort = root.children[0]
    assert sort.temp_written == 1048576 and sort.raw["engine_warnings"][0]["code"] == "TEMP_SPILL"


def test_explain_estimate_uses_plan_table_and_rolls_back() -> None:
    a, conn = adapter(
        [
            *base_routes(),
            (re.compile(r"^EXPLAIN PLAN SET STATEMENT_ID = 'DBM[0-9A-F]+' FOR SELECT"), []),
            ("FROM plan_table", PLAN),
        ]
    )
    res = asyncio.run(a.explain_query("SELECT * FROM shop.orders o JOIN shop.customers c ON c.id = o.customer_id;"))
    assert res.executed is False and res.root is not None and conn.rollbacks >= 1
    assert not any(s.startswith("SELECT * FROM shop.orders") for s, _ in conn.log)  # never executed
    with pytest.raises(AdapterError):
        asyncio.run(a.explain_query("DROP TABLE shop.orders"))


def test_explain_analyze_restores_statistics_level() -> None:
    a, conn = adapter(
        [
            *base_routes(),
            ("name = 'statistics_level'", [{"value": "TYPICAL"}]),
            ("ALTER SESSION SET STATISTICS_LEVEL", []),
            ("SET TRANSACTION READ ONLY", []),
            ("SELECT COUNT(*) FROM shop.orders", [{"n": 5}]),
            ("prev_sql_id", [{"prev_sql_id": "abcd1234xyz90", "prev_child_number": 0}]),
            (
                "FROM v$sql_plan_statistics_all",
                [{**PLAN[0], "last_starts": 1, "last_output_rows": 1, "last_elapsed_time": 500}],
            ),
        ]
    )
    res = asyncio.run(a.explain_query("SELECT COUNT(*) FROM shop.orders", analyze=True))
    assert res.executed and res.raw["sql_id"] == "abcd1234xyz90"
    sets = [s for s, _ in conn.log if s.startswith("ALTER SESSION SET STATISTICS_LEVEL")]
    assert sets == ["ALTER SESSION SET STATISTICS_LEVEL = ALL", "ALTER SESSION SET STATISTICS_LEVEL = TYPICAL"]
    assert conn.executed("SET TRANSACTION READ ONLY") and conn.rollbacks >= 1


def test_server_info_and_licensing_flags() -> None:
    a, _ = adapter(base_routes())
    info = asyncio.run(a.get_server_info())
    assert info.version == "19.21.0.0.0" and info.version_num == 192100
    assert info.edition == "Oracle Database 19c Enterprise Edition"
    assert info.extra["cdb"] and info.extra["container"] == "ORCLPDB1" and not info.extra["in_root"]
    assert info.extra["diagnostics_pack_licensed"] is False and info.extra["tuning_pack_licensed"] is False
    licensed, _ = adapter(base_routes(), licensed_features=["diagnostics_pack"])
    assert asyncio.run(licensed.get_server_info()).extra["diagnostics_pack_licensed"] is True


def test_discovery_without_dictionary_access() -> None:
    a, _ = adapter(
        [
            ("FROM v$instance i CROSS JOIN v$database d", ora_error("ORA-00942", "table or view does not exist")),
            (
                "FROM product_component_version",
                [
                    {
                        "version": "19.0.0.0.0",
                        "db_name": "ORCLCDB",
                        "con_name": "ORCLPDB1",
                        "con_id": 3,
                        "session_user": "APP",
                        "network_protocol": "tcp",
                        "service_name": "ORCLPDB1",
                        "host_name": "ora01",
                        "sid": 9,
                    }
                ],
            ),
            ("FROM session_privs", [{"privilege": "CREATE SESSION"}]),
            ("FROM session_roles", []),
            ("FROM dba_users WHERE ROWNUM = 1", ora_error("ORA-00942", "table or view does not exist")),
        ]
    )
    caps = asyncio.run(a.discover_capabilities())
    assert caps[Capability.SESSIONS].level == CapabilityLevel.REQUIRES_PRIVILEGE
    assert caps[Capability.USERS].level == CapabilityLevel.REQUIRES_PRIVILEGE
    assert caps[Capability.SESSION_TERMINATE].level == CapabilityLevel.REQUIRES_PRIVILEGE
    assert "Diagnostics Pack" in (caps[Capability.QUERY_HISTORY].note or "")
    assert caps[Capability.CONNECTION].level == CapabilityLevel.FULL


def test_cdb_root_reads_a_pdb_through_cdb_views() -> None:
    a, conn = adapter(
        [
            *base_routes(con_name="CDB$ROOT", con_id=1),
            (
                "FROM v$pdbs",
                [
                    {
                        "con_id": 3,
                        "name": "ORCLPDB1",
                        "open_mode": "READ WRITE",
                        "restricted": "NO",
                        "open_time": None,
                        "total_size": 10**9,
                    }
                ],
            ),
            ("FROM cdb_objects o", lambda params: [] if params.get("con_id") == 3 else AssertionError("con_id")),
            ("FROM cdb_indexes i", []),
            ("FROM cdb_constraints c", []),
        ]
    )
    asyncio.run(a.list_objects("ORCLPDB1", "SHOP"))
    cons_sql = next(s for s, _ in conn.log if "FROM cdb_constraints c" in s)
    assert "c.con_id = :con_id" in cons_sql and "cc.con_id = c.con_id" in cons_sql
    sql, params = next((s, p) for s, p in conn.log if "FROM cdb_objects o" in s)
    assert params == {"con_id": 3, "owner": "SHOP"} and "o.con_id = :con_id" in sql
    with pytest.raises(AdapterNotSupported):
        asyncio.run(a.list_objects("NOPE", "SHOP"))


def test_pdb_connection_refuses_other_containers() -> None:
    a, _ = adapter(base_routes())
    with pytest.raises(AdapterNotSupported):
        asyncio.run(a.list_schemas("OTHERPDB"))


def test_every_read_path_survives_an_empty_instance() -> None:
    a, conn = adapter([*base_routes(), ("FROM v$pdbs", []), (re.compile(r"."), [])])

    async def run() -> None:
        await a.discover_capabilities()
        await a.list_databases()
        await a.list_schemas("ORCLPDB1")
        await a.list_objects("ORCLPDB1", "SHOP")
        await a.list_users()
        await a.list_roles()
        await a.get_privileges()
        await a.get_sessions()
        await a.get_locks()
        snap = await a.get_performance_metrics(slow=True)
        assert snap.counters["cpu.busy_time_cs"] == 1000
        await a.get_query_statistics(10)
        await a.get_replication_status()
        await a.get_configuration()
        await a.get_storage_metrics()
        sec = await a.get_security_context()
        assert sec["network_encryption"] == "NOT_REQUIRED"
        tun = await a.get_tuning_context()
        assert tun["diagnostics_pack_licensed"] is False

    asyncio.run(run())
    assert conn.log


def test_session_cancel_and_kill_statements() -> None:
    a, conn = adapter([*base_routes(), ("ALTER SYSTEM", [])])
    asyncio.run(a.cancel_session("123,4567"))
    asyncio.run(a.cancel_session("123,4567", terminate=True))
    assert conn.executed("ALTER SYSTEM CANCEL SQL '123,4567'")
    assert conn.executed("ALTER SYSTEM KILL SESSION '123,4567' IMMEDIATE")
    with pytest.raises(AdapterNotSupported):
        asyncio.run(a.cancel_session("1; DROP USER x"))


def test_metrics_cpu_derivation() -> None:
    from app.services.metric_catalog import derive

    _, state = derive({"cpu.busy_time_cs": 1000.0, "cpu.idle_time_cs": 9000.0}, None, 0.0)
    out, _ = derive({"cpu.busy_time_cs": 1300.0, "cpu.idle_time_cs": 9700.0}, state, 60.0)
    assert out["cpu.percent"] == 30.0 and "cpu.busy_time_cs.per_sec" not in out


def test_dataguard_interval_parsing() -> None:
    assert interval_seconds("+00 00:00:05") == 5.0
    assert interval_seconds("+01 02:03:04") == 93784.0
    assert interval_seconds(None) is None


def test_admin_password_is_bound_never_in_sql() -> None:
    a = OracleAdapter(SPEC)
    stmt = a.build_admin("create_user", {"name": "app_ro", "password": "Very-Secret-1", "default_tablespace": "users"})
    assert all("Very-Secret-1" not in s for s in stmt.statements + stmt.display)
    assert stmt.params[0] == ("APP_RO", "Very-Secret-1")
    assert stmt.statements[1] == 'GRANT CREATE SESSION TO "APP_RO"'
    assert 'DEFAULT TABLESPACE "USERS"' in stmt.display[0]
    with pytest.raises(A.AdminValidationError):
        a.build_admin("create_user", {"name": "x", "password": 'quote"inside'})
    with pytest.raises(A.AdminValidationError):
        a.build_admin("grant", {"object_type": "role", "role": "DBA", "grantee": "app"})
    with pytest.raises(AdapterNotSupported):
        a.build_admin("create_database", {"name": "PDB2"})
    g = a.build_admin(
        "grant",
        {
            "object_type": "table",
            "schema": "shop",
            "object_name": "orders",
            "grantee": "app_ro",
            "privileges": ["select"],
        },
    )
    assert g.statements == ['GRANT SELECT ON "SHOP"."ORDERS" TO "APP_RO"']
    with pytest.raises(A.AdminValidationError):
        a.build_admin("grant", {"object_type": "system", "grantee": "u", "privileges": ["ALTER SYSTEM"]})


@pytest.mark.parametrize(
    ("sql", "pack"),
    [
        ("select * from dba_hist_snapshot", "diagnostics_pack"),
        ("SELECT sample_time FROM V$ACTIVE_SESSION_HISTORY", "diagnostics_pack"),
        ("select * from table(dbms_xplan.display_awr('abc'))", "diagnostics_pack"),
        ("select * from awr_pdb_snapshot", "diagnostics_pack"),
        ("select * from gv$sql_monitor", "tuning_pack"),
        ("begin dbms_sqltune.execute_tuning_task('t'); end;", "tuning_pack"),
        ("select * from v$session", None),
        ("select * from v$sqlstats", None),
        ("select awr_note, hist_dba from my_table", None),
    ],
)
def test_workspace_pack_guard_detects_licensed_objects(sql: str, pack: str | None) -> None:
    from app.adapters.oracle.adapter import licensed_pack_needed

    assert licensed_pack_needed(sql) == pack


def test_workspace_refuses_pack_objects_unless_licensed() -> None:
    import asyncio

    from app.adapters.base import AdapterError, ConnectionSpec
    from app.adapters.oracle.adapter import OracleAdapter

    a = OracleAdapter(
        ConnectionSpec(host="ora.example.com", port=1521, database="ORCLPDB1", username="u", password="p")
    )
    with pytest.raises(AdapterError, match="Diagnostics Pack"):
        asyncio.run(a.execute_sql("select * from dba_hist_snapshot", None, read_only=True, max_rows=1, timeout_s=5))
    with pytest.raises(AdapterError, match="Tuning Pack"):
        asyncio.run(a.execute_sql("select * from v$sql_monitor", None, read_only=True, max_rows=1, timeout_s=5))


def test_constraints_are_listed_for_a_schema() -> None:
    a, conn = adapter(
        [
            *base_routes(),
            (
                "FROM dba_constraints c",
                [
                    {
                        "constraint_name": "ORDERS_PK",
                        "table_name": "ORDERS",
                        "constraint_type": "P",
                        "status": "ENABLED",
                        "validated": "VALIDATED",
                        "columns": "ID",
                    },
                    {
                        "constraint_name": "ORDERS_CUST_FK",
                        "table_name": "ORDERS",
                        "constraint_type": "R",
                        "status": "DISABLED",
                        "validated": "NOT VALIDATED",
                        "columns": "CUSTOMER_ID",
                    },
                ],
            ),
        ]
    )
    out = asyncio.run(a.list_objects("ORCLPDB1", "SHOP", "constraint"))
    assert [(o.name, o.parent, o.extra["constraint_type"]) for o in out] == [
        ("ORDERS_PK", "ORDERS", "PRIMARY KEY"),
        ("ORDERS_CUST_FK", "ORDERS", "FOREIGN KEY"),
    ]
    assert out[1].extra["validated"] is False and out[1].extra["status"] == "DISABLED"
    sql, params = next((s, p) for s, p in conn.log if "FROM dba_constraints c" in s)
    assert params == {"owner": "SHOP"} and "generated = 'USER NAME'" in sql


def test_root_connection_lists_the_local_users_of_its_pdbs() -> None:
    routes = [
        ("FROM dba_users", []),
        ("FROM dba_role_privs", []),
        ("FROM dba_sys_privs", []),
        ("FROM v$pwfile_users", []),
        (
            "FROM cdb_users u JOIN v$pdbs p",
            [
                {
                    "pdb": "SALESPDB",
                    "username": "APP_OWNER",
                    "account_status": "OPEN",
                    "lock_date": None,
                    "expiry_date": None,
                    "profile": "DEFAULT",
                    "created": None,
                    "authentication_type": "PASSWORD",
                    "last_login": None,
                }
            ],
        ),
    ]
    a, _ = adapter([*base_routes(con_name="CDB$ROOT", con_id=1), *routes])
    users = asyncio.run(a.list_users())
    assert [(u.name, u.can_login, u.attributes["container"]) for u in users] == [
        ("SALESPDB:APP_OWNER", True, "SALESPDB")
    ]
    # connected to a PDB: no CDB_ view is read
    b, conn = adapter([*base_routes(), *routes])
    assert asyncio.run(b.list_users()) == []
    assert not any("cdb_users" in s for s, _ in conn.log)
