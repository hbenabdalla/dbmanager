"""Db2 LUW and Db2 for z/OS adapter units with scripted connections. No Db2 server is available
in CI; real-environment validation is pending (tests/integration/test_db2.py runs when
DBM_IT_DB2_* is set)."""

from __future__ import annotations

import asyncio
import re

import pytest

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
from app.adapters.db2 import sql as Q
from app.adapters.db2.adapter import Db2Adapter
from app.adapters.db2.base import parse_service_level
from app.adapters.db2.plans import build_tree
from app.adapters.db2zos import sql as ZQ
from app.adapters.db2zos.adapter import Db2zOSAdapter, parse_zos_version
from app.adapters.dbapi import AdapterObjectMissing
from app.domain.plan_analysis import analyze_plan
from tests.unit.fakedb import FakeConnection, attach

SPEC = ConnectionSpec(host="db2.test", port=50000, username="dbmmon", password="Db2;Secret{1}", database="SAMPLE")
ZSPEC = ConnectionSpec(host="zos.test", port=446, username="DBMMON", password="Zos-Pass1", database="DSNLOC1")


def db2_error(code: int, state: str, text: str) -> Exception:
    return Exception(f"[IBM][CLI Driver][DB2/LINUXX8664] SQL{abs(code):04d}N  {text}  SQLSTATE={state} SQLCODE={code}")


def luw_routes(**over: object) -> list:
    return [
        (
            "CURRENT SERVER AS DB_NAME",
            [
                {
                    "db_name": "SAMPLE",
                    "cur_user": "DBMMON",
                    "sess_user": "DBMMON",
                    "cur_schema": "DBMMON",
                    "cur_member": 0,
                }
            ],
        ),
        (
            "FROM SYSIBMADM.ENV_INST_INFO",
            [
                {
                    "inst_name": "db2inst1",
                    "service_level": "DB2 v11.5.9.0",
                    "bld_level": "s2310270807",
                    "fixpack_num": 0,
                    "release_num": "0601",
                    "num_members": 1,
                }
            ],
        ),
        (
            "FROM SYSIBMADM.ENV_PROD_INFO",
            [
                {
                    "installed_prod": "DEC",
                    "installed_prod_fullname": "IBM Db2 Standard Edition",
                    "license_installed": "Y",
                    "prod_release": "11.5",
                    "license_type": "VPC",
                }
            ],
        ),
        (
            "FROM SYSIBMADM.ENV_SYS_INFO",
            [
                {
                    "os_name": "Linux",
                    "os_version": "5",
                    "os_release": "15",
                    "host_name": "db2host",
                    "total_cpus": 8,
                    "configured_cpus": 8,
                    "total_memory": 32000,
                }
            ],
        ),
        (
            "AUTH_LIST_AUTHORITIES_FOR_AUTHID",
            over.get(
                "authorities",
                [
                    {
                        "authority": "SQLADM",
                        "d_user": "Y",
                        "d_group": "N",
                        "d_public": "N",
                        "role_user": "N",
                        "role_group": "N",
                        "role_public": "N",
                        "d_role": "N",
                    },
                    {
                        "authority": "DBADM",
                        "d_user": "N",
                        "d_group": "N",
                        "d_public": "N",
                        "role_user": "N",
                        "role_group": "N",
                        "role_public": "N",
                        "d_role": "N",
                    },
                ],
            ),
        ),
        ("SELECT COUNT(*) AS N FROM TABLE(MON_GET_CONNECTION", over.get("conn_count", [{"n": 12}])),
    ]


def adapter(routes: list, cls: type = Db2Adapter, spec: ConnectionSpec = SPEC) -> tuple:
    a = cls(spec)
    return a, attach(a, FakeConnection(routes))


# ---------------------------------------------------------------------------------------------
def test_versions() -> None:
    assert parse_service_level("DB2 v11.5.9.0") == (11, 5, 9, 0)
    assert parse_service_level("garbage") == (0, 0, 0, 0)
    assert parse_zos_version("DSN13015") == (13, 1, 5)
    assert parse_zos_version("DSN12015") == (12, 1, 5)


def test_templates_format_and_use_qmark_binds() -> None:
    for mod in (Q, ZQ):
        for name, sql in vars(mod).items():
            if name.isupper() and isinstance(sql, str) and "{" in sql:
                sql.format(types="'T'", table_filter="", limit=10, schema='"S"', keys=Q.EXPLAIN_KEYS, names="'a'")
            if name.isupper() and isinstance(sql, str):
                assert "%s" not in sql and not re.search(r":[a-z_]+\b", sql.replace("::", "")), name


def test_connection_string_tls_and_credentials_quoting() -> None:
    a = Db2Adapter(
        SPEC.model_copy(
            update={
                "tls_mode": "verify-full",
                "tls_ca_pem": "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n",
            }
        )
    )
    cs = a.connection_string()
    assert "SECURITY=SSL" in cs and "SSLServerCertificate=" in cs and "DATABASE=SAMPLE" in cs
    assert "PWD" not in cs and "Secret" not in cs
    assert "SSLClientHostnameValidation=Basic" in cs  # verify-full also checks the host name
    asyncio.run(a.close())
    ca_only = Db2Adapter(SPEC.model_copy(update={"tls_mode": "verify-ca"})).connection_string()
    assert "SECURITY=SSL" in ca_only and "SSLClientHostnameValidation" not in ca_only
    plain = Db2Adapter(SPEC.model_copy(update={"tls_mode": "prefer"})).connection_string()
    assert "SECURITY" not in plain
    with pytest.raises(AdapterConnectionError):
        Db2Adapter(SPEC.model_copy(update={"database": "X;PWD=y"})).connection_string()
    from app.adapters.db2.base import _cli_value

    assert _cli_value("Db2;Secret{1}") == "{Db2;Secret{1}}}"
    assert _cli_value("plain") == "plain"


@pytest.mark.parametrize(
    ("code", "state", "msg", "expected"),
    [
        (
            -30082,
            "08001",
            'Security processing failed with reason "24" ("USERNAME AND/OR PASSWORD INVALID").',
            AdapterAuthenticationError,
        ),
        (
            -1013,
            "42705",
            'The database alias name or database name "NOPE" could not be found.',
            AdapterDatabaseNotFound,
        ),
        (
            -30081,
            "08001",
            "A communication error has been detected. Protocol: TCP/IP. Function: connect.",
            AdapterConnectionError,
        ),
        (-30081, "08001", "A communication error ... GSKit Error: 414 (SSL handshake)", AdapterTLSError),
        (
            -551,
            "42501",
            "The statement failed because the authorization ID does not have the required authorization.",
            AdapterPermissionError,
        ),
        (
            -440,
            "42884",
            'No authorized routine named "MON_GET_INSTANCE" of type "FUNCTION" was found.',
            AdapterObjectMissing,
        ),
        (
            -911,
            "40001",
            "The current transaction has been rolled back because of a deadlock or timeout.",
            AdapterTimeout,
        ),
        (-952, "57014", "Processing was cancelled due to an interrupt.", AdapterTimeout),
        (
            -1598,
            "42968",
            "An attempt to connect to the database server failed because of a licensing problem.",
            AdapterConnectionError,
        ),
    ],
)
def test_error_classification(code: int, state: str, msg: str, expected: type) -> None:
    err = Db2Adapter(SPEC)._classify(db2_error(code, state, msg))
    assert isinstance(err, expected), (code, type(err))


def test_license_error_mentions_db2_connect() -> None:
    err = Db2zOSAdapter(ZSPEC)._classify(db2_error(-1598, "42968", "licensing problem"))
    assert "Db2 Connect" in str(err)


def test_server_info_and_discovery() -> None:
    a, _ = adapter(
        [
            *luw_routes(),
            ("MON_GET_DATABASE", [{"started": None}]),
            ("MON_GET_INSTANCE", AdapterObjectMissing("routine not found")),
            ("EXPLAIN_STATEMENT' AND TABSCHEMA", []),
            ("NAME IN ('mon_act_metrics')", [{"name": "mon_act_metrics", "value": "NONE"}]),
            ("NAME IN ('svcename')", [{"name": "svcename", "value": "50000"}]),
            ("MON_GET_HADR", []),
            ("ENV_GET_SYSTEM_RESOURCES", [{"cpu_usage_total": 12}]),
        ]
    )
    info = asyncio.run(a.get_server_info())
    assert info.version == "11.5.9.0" and info.version_num == 11050900 and info.edition == "IBM Db2 Standard Edition"
    assert info.extra["authorities"] == ["SQLADM"]
    caps = asyncio.run(a.discover_capabilities())
    assert caps[Capability.EXPLAIN].level == CapabilityLevel.REQUIRES_CONFIGURATION
    assert caps[Capability.QUERY_STATISTICS].level == CapabilityLevel.REQUIRES_CONFIGURATION
    assert caps[Capability.SESSION_TERMINATE].level == CapabilityLevel.REQUIRES_PRIVILEGE
    assert caps[Capability.SESSION_CANCEL].level == CapabilityLevel.REQUIRES_PRIVILEGE
    assert "not configured" in (caps[Capability.REPLICATION].note or "")
    assert caps[Capability.EXPLAIN_ANALYZE].level == CapabilityLevel.NOT_SUPPORTED


def test_no_monitor_execute_marks_components_not_connection() -> None:
    denied = AdapterPermissionError("SQL0551N authorization")
    a, _ = adapter([*luw_routes(conn_count=denied), (re.compile(r"."), denied)])
    caps = asyncio.run(a.discover_capabilities())
    assert caps[Capability.SESSIONS].level == CapabilityLevel.REQUIRES_PRIVILEGE
    assert caps[Capability.CONNECTION].level == CapabilityLevel.FULL
    snap = asyncio.run(a.get_performance_metrics())
    assert snap.gauges == {"availability.up": 1.0} and "connections.total" in snap.unavailable


def test_metrics_mapping() -> None:
    a, _ = adapter(
        [
            *luw_routes(),
            ("FROM SYSIBMADM.MON_CURRENT_SQL", [{"n": 3, "longest": 7}]),
            ("FROM SYSIBMADM.MON_LOCKWAITS", [{"n": 1}]),
            ("NAME IN ('maxappls')", [{"name": "maxappls", "value": "200"}]),
            (
                "MON_GET_WORKLOAD",
                [
                    {
                        "commits": 100,
                        "rollbacks": 2,
                        "rows_read": 9000,
                        "rows_returned": 500,
                        "rows_modified": 40,
                        "l_reads": 10000,
                        "p_reads": 300,
                        "temp_p_reads": 0,
                        "deadlocks": 1,
                        "lock_timeouts": 0,
                        "lock_escals": 0,
                        "lock_waits": 5,
                        "lock_wait_time": 80,
                        "total_sorts": 50,
                        "sort_overflows": 1,
                        "pkg_cache_inserts": 10,
                        "pkg_cache_lookups": 1000,
                        "acts": 600,
                        "act_time_ms": 1200,
                        "cpu_us": 5000,
                    }
                ],
            ),
            ("MON_GET_BUFFERPOOL", [{"bp_name": "IBMDEFAULTBP", "l_reads": 10000, "p_reads": 100}]),
            ("MON_GET_MEMORY_SET", [{"used_kb": 2048}]),
            ("ENV_GET_SYSTEM_RESOURCES", [{"cpu_usage_total": 33}]),
            ("MON_GET_TRANSACTION_LOG", [{"used": 300, "available": 700}]),
            ("MON_GET_HADR", []),
            ("MON_GET_TABLESPACE", [{"tbsp_name": "USERSPACE1", "tbsp_page_size": 4096, "used_pages": 10}]),
        ]
    )
    snap = asyncio.run(a.get_performance_metrics(slow=True))
    g, c = snap.gauges, snap.counters
    assert g["connections.total"] == 12 and g["connections.active"] == 3 and g["connections.utilization"] == 6.0
    assert g["sessions.blocked"] == 1 and g["cpu.percent"] == 33 and g["memory.used_bytes"] == 2048 * 1024
    assert g["db2.log_used_percent"] == 30.0 and g["db2.bufferpool_hit_ratio"] == 99.0
    assert g["storage.database_bytes"] == 40960
    assert c["transactions.commits"] == 100 and c["io.blocks_hit"] == 9700 and c["query.total_ms"] == 1200


def test_sessions_with_blocking() -> None:
    a, _ = adapter(
        [
            *luw_routes(),
            (
                "FROM TABLE(MON_GET_CONNECTION(CAST(NULL AS BIGINT), -2)) AS c ORDER BY",
                [
                    {
                        "application_handle": 7,
                        "application_name": "app",
                        "application_id": "x",
                        "client_hostname": "h",
                        "client_ipaddr": "10.0.0.5",
                        "session_auth_id": "APPUSER",
                        "system_auth_id": "APPUSER",
                        "connection_start_time": None,
                        "member": 0,
                    },
                    {
                        "application_handle": 8,
                        "application_name": "batch",
                        "application_id": "y",
                        "client_hostname": "h2",
                        "client_ipaddr": None,
                        "session_auth_id": "BATCH",
                        "system_auth_id": "BATCH",
                        "connection_start_time": None,
                        "member": 0,
                    },
                ],
            ),
            (
                "FROM SYSIBMADM.MON_CURRENT_SQL",
                [
                    {
                        "application_handle": 8,
                        "elapsed_time_sec": 42,
                        "activity_state": "EXECUTING",
                        "activity_type": "READ_DML",
                        "stmt_text": "SELECT * FROM T",
                    }
                ],
            ),
            (
                "MON_GET_UNIT_OF_WORK",
                [
                    {
                        "application_handle": 7,
                        "uow_start_time": "2026-10-17T10:00:00",
                        "workload_occurrence_state": "UOWWAIT",
                    }
                ],
            ),
            ("FROM SYSIBMADM.MON_LOCKWAITS", [{"req_application_handle": 8, "hld_application_handle": 7}]),
        ]
    )
    s = {x.session_id: x for x in asyncio.run(a.get_sessions())}
    assert s["7"].state == "idle in transaction" and s["8"].state == "active" and s["8"].blocked_by == ["7"]
    assert s["8"].duration_seconds == 42 and s["8"].wait_event_type == "Lock"


def test_cancel_and_force() -> None:
    a, conn = adapter([*luw_routes(), ("MON_GET_ACTIVITY", [{"uow_id": 3, "activity_id": 1}]), ("CALL SYSPROC", [])])
    asyncio.run(a.cancel_session("42"))
    asyncio.run(a.cancel_session("42", terminate=True))
    assert any("WLM_CANCEL_ACTIVITY" in s and p == [42, 3, 1] for s, p in conn.log)
    assert conn.executed("ADMIN_CMD('FORCE APPLICATION (42)')")
    with pytest.raises(AdapterNotSupported):
        asyncio.run(a.cancel_session("1); DROP"))


def test_explain_tree_from_explain_tables() -> None:
    ops = [
        {"operator_id": 1, "operator_type": "RETURN", "total_cost": 820.5},
        {"operator_id": 2, "operator_type": "HSJOIN", "total_cost": 820.0},
        {"operator_id": 3, "operator_type": "TBSCAN", "total_cost": 790.0},
        {"operator_id": 4, "operator_type": "IXSCAN", "total_cost": 7.5},
    ]
    streams = [
        {"source_type": "O", "source_id": 2, "target_type": "O", "target_id": 1, "stream_count": 120},
        {"source_type": "O", "source_id": 3, "target_type": "O", "target_id": 2, "stream_count": 50000},
        {"source_type": "O", "source_id": 4, "target_type": "O", "target_id": 2, "stream_count": 1},
        {
            "source_type": "D",
            "source_id": None,
            "target_type": "O",
            "target_id": 3,
            "object_schema": "SHOP",
            "object_name": "ORDERS",
            "stream_count": 50000,
        },
        {
            "source_type": "D",
            "source_id": None,
            "target_type": "O",
            "target_id": 4,
            "object_schema": "SHOP",
            "object_name": "CUST_PK",
            "stream_count": 1,
        },
    ]
    preds = [{"operator_id": 3, "how_applied": "SARG", "predicate_text": "(Q1.TOTAL > 10)"}]
    root = build_tree(ops, streams, preds)
    assert root is not None and root.operator == "RETURN" and root.children[0].operator == "HSJOIN"
    scan = root.children[0].children[0]
    assert scan.relation == "SHOP.ORDERS" and scan.estimated_rows == 50000 and "TOTAL" in (scan.filter or "")
    assert root.children[0].children[1].index == "SHOP.CUST_PK"
    assert "FULL_SCAN" in {i.code for i in analyze_plan(root, analyzed=False)}


def test_explain_flow_rolls_back() -> None:
    head = {
        "explain_requester": "DBMMON",
        "explain_time": "2026-10-17 10:00:00",
        "source_name": "SYSSH200",
        "source_schema": "NULLID",
        "source_version": "",
        "stmtno": 1,
        "sectno": 1,
        "total_cost": 12.5,
    }
    a, conn = adapter(
        [
            *luw_routes(),
            ("EXPLAIN_STATEMENT' AND TABSCHEMA", [{"tabschema": "DBMMON"}]),
            (re.compile(r"^EXPLAIN PLAN SET QUERYTAG = 'DBM[0-9A-F]+' FOR SELECT"), []),
            ('FROM "DBMMON".EXPLAIN_STATEMENT', [head]),
            ('FROM "DBMMON".EXPLAIN_OPERATOR', [{"operator_id": 1, "operator_type": "RETURN", "total_cost": 12.5}]),
            ('FROM "DBMMON".EXPLAIN_STREAM', []),
            ('FROM "DBMMON".EXPLAIN_PREDICATE', []),
        ]
    )
    res = asyncio.run(a.explain_query("SELECT * FROM shop.orders"))
    assert res.root is not None and res.total_cost == 12.5 and conn.rollbacks >= 1 and not res.executed
    with pytest.raises(AdapterError):
        asyncio.run(a.explain_query("SELECT 1", analyze=True))


def test_every_read_path_survives_an_empty_database() -> None:
    a, conn = adapter([*luw_routes(), (re.compile(r"."), [])])

    async def run() -> None:
        await a.discover_capabilities()
        await a.get_server_info()
        await a.list_databases()
        await a.list_schemas("SAMPLE")
        await a.list_objects("SAMPLE", "SHOP")
        await a.list_users()
        await a.list_roles()
        await a.get_privileges()
        await a.get_sessions()
        await a.get_locks()
        await a.get_performance_metrics(slow=True)
        await a.get_query_statistics(10)
        await a.get_replication_status()
        await a.get_storage_metrics()
        sec = await a.get_security_context()
        assert sec["public_grants"] == []
        tun = await a.get_tuning_context()
        assert tun["tables_needing_reorg"] == []

    asyncio.run(run())
    assert conn.commits > 0  # monitoring reads end their unit of work


def test_admin_builders() -> None:
    a = Db2Adapter(SPEC)
    g = a.build_admin("grant", {"object_type": "database", "grantee": "appuser", "privileges": ["CONNECT"]})
    assert g.statements == ['GRANT CONNECT ON DATABASE TO USER "APPUSER"']
    r = a.build_admin(
        "grant", {"object_type": "role", "role": "reporting", "grantee": "analysts", "grantee_type": "group"}
    )
    assert r.statements == ['GRANT ROLE "REPORTING" TO GROUP "ANALYSTS"']
    t = a.build_admin(
        "revoke",
        {"object_type": "table", "schema": "shop", "object_name": "orders", "grantee": "x", "privileges": ["select"]},
    )
    assert t.statements == ['REVOKE SELECT ON TABLE "SHOP"."ORDERS" FROM USER "X"']
    with pytest.raises(AdapterNotSupported):
        a.build_admin("create_user", {"name": "x", "password": "longenough"})
    with pytest.raises(A.AdminValidationError):
        a.build_admin("grant", {"object_type": "database", "grantee": "x", "privileges": ["DBADM"]})
    with pytest.raises(AdapterNotSupported):
        Db2zOSAdapter(ZSPEC).build_admin("create_role", {"name": "x"})


# ---- Db2 for z/OS -------------------------------------------------------------------------
def zos_routes() -> list:
    return [
        (
            "GETVARIABLE('SYSIBM.VERSION')",
            [
                {
                    "version": "DSN13015",
                    "ssid": "DB2A",
                    "dsgroup": "",
                    "location": "DSNLOC1",
                    "sqlid": "DBMMON",
                    "authid": "DBMMON",
                    "member_name": "",
                }
            ],
        ),
        ("CURRENT APPLICATION COMPATIBILITY", [{"applcompat": "V13R1M500"}]),
    ]


def test_zos_classification_is_honest() -> None:
    caps = Db2zOSAdapter.capability_profile()
    assert caps[Capability.PERFORMANCE_METRICS].level == CapabilityLevel.REQUIRES_EXTERNAL_MONITORING
    assert caps[Capability.SESSIONS].level == CapabilityLevel.NOT_IMPLEMENTED
    assert caps[Capability.QUERY_STATISTICS].level == CapabilityLevel.NOT_IMPLEMENTED
    assert caps[Capability.PRIVILEGES].level == CapabilityLevel.FULL
    assert Db2zOSAdapter.support_level == "partial"
    assert "Db2 Connect" in (caps[Capability.CONNECTION].note or "")


def test_zos_discovery_and_metrics_do_not_fabricate() -> None:
    a, _ = adapter([*zos_routes(), ("PLAN_TABLE' AND CREATOR", []), ("SYSTABLESPACESTATS", [])], Db2zOSAdapter, ZSPEC)
    info = asyncio.run(a.get_server_info())
    assert (
        info.version == "13.1.5"
        and info.extra["ssid"] == "DB2A"
        and info.extra["application_compatibility"] == "V13R1M500"
    )
    caps = asyncio.run(a.discover_capabilities())
    assert caps[Capability.EXPLAIN].level == CapabilityLevel.REQUIRES_CONFIGURATION
    assert caps[Capability.CONNECTION].level == CapabilityLevel.FULL
    snap = asyncio.run(a.get_performance_metrics(slow=False))
    assert snap.gauges == {"availability.up": 1.0} and "cpu.percent" in snap.unavailable


def test_zos_rts_recommendations() -> None:
    rows = [
        {
            "dbname": "APPDB",
            "name": "TS1",
            "partition": 0,
            "totalrows": 1000,
            "nactive": 10,
            "space": 400,
            "reorglasttime": None,
            "reorginserts": 300,
            "reorgdeletes": 0,
            "reorgupdates": 0,
            "reorgunclustins": 0,
            "reorgmassdelete": 0,
            "statslasttime": None,
            "statsinserts": 0,
            "statsdeletes": 0,
            "statsupdates": 0,
            "copylasttime": None,
            "copyupdatedpages": 12,
            "updatestatstime": None,
        },
        {
            "dbname": "APPDB",
            "name": "TS2",
            "partition": 0,
            "totalrows": 1000,
            "nactive": 10,
            "space": 400,
            "reorglasttime": None,
            "reorginserts": 1,
            "reorgdeletes": 1,
            "reorgupdates": 0,
            "reorgunclustins": 0,
            "reorgmassdelete": 0,
            "statslasttime": "2026-10-16 00:00:00",
            "statsinserts": 1,
            "statsdeletes": 0,
            "statsupdates": 0,
            "copylasttime": "2026-10-17 00:00:00",
            "copyupdatedpages": 0,
            "updatestatstime": None,
        },
    ]
    a, _ = adapter(
        [*zos_routes(), ("FROM SYSIBM.SYSTABLESPACESTATS WHERE DBNAME NOT LIKE", rows)], Db2zOSAdapter, ZSPEC
    )
    t = asyncio.run(a.get_tuning_context())
    assert [x["object"] for x in t["reorg_recommended"]] == ["APPDB.TS1"]
    assert [x["object"] for x in t["runstats_recommended"]] == ["APPDB.TS1"]
    assert [x["object"] for x in t["copy_recommended"]] == ["APPDB.TS1"]


def test_zos_explain_from_plan_table() -> None:
    plan = [
        {
            "qblockno": 1,
            "planno": 1,
            "method": 0,
            "creator": "APP",
            "tname": "ORDERS",
            "tabno": 1,
            "accesstype": "R",
            "matchcols": 0,
            "accesscreator": "",
            "accessname": "",
            "indexonly": "N",
            "sortn_join": "N",
            "sortc_uniq": "N",
            "sortc_orderby": "N",
            "sortc_groupby": "N",
            "prefetch": "S",
            "join_type": "",
            "qblock_type": "SELECT",
        },
        {
            "qblockno": 1,
            "planno": 2,
            "method": 1,
            "creator": "APP",
            "tname": "CUST",
            "tabno": 2,
            "accesstype": "I",
            "matchcols": 1,
            "accesscreator": "APP",
            "accessname": "CUSTIX1",
            "indexonly": "N",
            "sortn_join": "N",
            "sortc_uniq": "N",
            "sortc_orderby": "Y",
            "sortc_groupby": "N",
            "prefetch": "",
            "join_type": "",
            "qblock_type": "SELECT",
        },
    ]
    a, conn = adapter(
        [
            *zos_routes(),
            ("PLAN_TABLE' AND CREATOR", [{"creator": "DBMMON"}]),
            (re.compile(r"^EXPLAIN PLAN SET QUERYNO = \d+ FOR SELECT"), []),
            ("FROM PLAN_TABLE WHERE QUERYNO", plan),
            ("FROM DSN_STATEMNT_TABLE", [{"procms": 3, "procsu": 120, "cost_category": "A"}]),
            ("SELECT CARDF FROM SYSIBM.SYSTABLES", [{"cardf": 2_000_000.0}]),
        ],
        Db2zOSAdapter,
        ZSPEC,
    )
    res = asyncio.run(a.explain_query("SELECT * FROM APP.ORDERS O JOIN APP.CUST C ON C.ID = O.CID"))
    block = res.root.children[0]  # type: ignore[union-attr]
    assert block.children[0].operator.startswith("TABLE SCAN") and block.children[0].estimated_rows == 2_000_000
    assert block.children[1].operator == "NESTED LOOP JOIN / INDEX SCAN" and block.children[1].index == "APP.CUSTIX1"
    assert "FULL_SCAN" in {i.code for i in res.issues} and res.total_cost == 120 and conn.rollbacks >= 1


def test_workspace_refuses_another_database_instead_of_ignoring_it() -> None:
    """Db2 LUW connects to one database: a statement for another one is refused before
    anything runs (it used to run silently in the connected database)."""
    from app.adapters.base import AdapterError, ConnectionSpec

    a = Db2Adapter(ConnectionSpec(host="db2.example.com", port=50000, database="SAMPLE", username="u", password="p"))
    with pytest.raises(AdapterError, match="register 'OTHER'"):
        asyncio.run(a.execute_sql("SELECT 1 FROM SYSIBM.SYSDUMMY1", "OTHER", read_only=True, max_rows=1, timeout_s=5))
    with pytest.raises(AdapterError, match="register 'OTHER'"):
        asyncio.run(a.explain_query("SELECT 1 FROM SYSIBM.SYSDUMMY1", "OTHER"))
    a._check_database("sample")  # same database, any case: accepted  # noqa: SLF001
    a._check_database(None)  # noqa: SLF001


def test_largest_tables_follow_the_storage_contract() -> None:
    a, _ = adapter(
        [
            *luw_routes(),
            (
                "FROM SYSCAT.TABLES t",
                [
                    {
                        "schema_name": "SHOP    ",
                        "name": "ORDERS",
                        "row_estimate": 120000,
                        "stats_time": None,
                        "table_bytes": 8 * 1024 * 1024,
                        "index_bytes": 2 * 1024 * 1024,
                    }
                ],
            ),
            (re.compile(r"."), []),
        ]
    )
    st = asyncio.run(a.get_storage_metrics())
    assert st.top_tables == [
        {
            "schema": "SHOP",
            "name": "ORDERS",
            "row_estimate": 120000,
            "table_bytes": 8 * 1024 * 1024,
            "index_bytes": 2 * 1024 * 1024,
            "total_bytes": 10 * 1024 * 1024,
            "stats_time": None,
        }
    ]


def test_routine_package_sequence_and_column_grants_are_listed() -> None:
    def grant(**cols: object) -> dict[str, object]:
        return {"grantor": "DBA1", "grantee": "APPUSER", "granteetype": "U", "objschema": "SHOP  ", **cols}

    a, _ = adapter(
        [
            *luw_routes(),
            ("FROM SYSCAT.ROUTINEAUTH", [grant(objname="SQL240101_PLACE_ORDER", executeauth="G")]),
            ("FROM SYSCAT.PACKAGEAUTH", [grant(objname="P1", controlauth="N", bindauth="Y", executeauth="Y")]),
            ("FROM SYSCAT.SEQUENCEAUTH", [grant(objname="ORDER_SEQ", usageauth="Y", alterauth="N")]),
            ("FROM SYSCAT.TBSPACEAUTH", [grant(objschema="", objname="USERSPACE1", useauth="Y")]),
            (
                "FROM SYSCAT.COLAUTH",
                [
                    {
                        "grantor": "DBA1",
                        "grantee": "APPUSER",
                        "granteetype": "U",
                        "tabschema": "SHOP",
                        "tabname": "ORDERS",
                        "colname": "STATUS",
                        "privtype": "U",
                        "grantable": "N",
                    }
                ],
            ),
            (re.compile(r"."), []),
        ]
    )
    got = {(p.object_type, p.object_name, p.privilege, p.grantable) for p in asyncio.run(a.get_privileges("SAMPLE"))}
    assert {
        ("routine", "SHOP.SQL240101_PLACE_ORDER", "EXECUTE", True),
        ("package", "SHOP.P1", "BIND", False),
        ("package", "SHOP.P1", "EXECUTE", False),
        ("sequence", "SHOP.ORDER_SEQ", "USAGE", False),
        ("tablespace", "USERSPACE1", "USE", False),
        ("column", "SHOP.ORDERS.STATUS", "UPDATE", False),
    } <= got
    assert not any(
        p.privilege == "CONTROL" for p in asyncio.run(a.get_privileges("SAMPLE")) if p.object_type == "package"
    )
