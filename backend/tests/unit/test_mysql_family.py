"""MySQL / MariaDB adapter units: plan parsing, admin statement generation, error mapping and
flavor-specific behaviour (real-server coverage is in tests/integration/test_mysql_family.py)."""

from __future__ import annotations

import pymysql
import pytest

from app.adapters import admin as A
from app.adapters.base import (
    AdapterAuthenticationError,
    AdapterConnectionError,
    AdapterDatabaseNotFound,
    AdapterNotSupported,
    AdapterPermissionError,
    AdapterTimeout,
    Capability,
    CapabilityLevel,
    ConnectionSpec,
)
from app.adapters.dbapi import AdapterObjectMissing
from app.adapters.mysql.adapter import MariaDBAdapter, MySQLAdapter
from app.adapters.mysql.base import normalize_grantee, parse_version
from app.adapters.mysql.plans import parse_json, parse_tree
from app.domain.plan_analysis import analyze_plan
from tests.unit.fakedb import FakeConnection, attach

SPEC = ConnectionSpec(host="db.test", port=3306, username="mon", password="pw")

TREE = """-> Sort: o.total  (cost=1050 rows=33333)
    -> Nested loop inner join  (cost=1050 rows=33333)
        -> Filter: (o.total > 10.00)  (cost=1015 rows=33333)
            -> Table scan on o  (cost=1015 rows=100000)
        -> Single-row index lookup on c using PRIMARY (id=o.customer_id)  (cost=0.25 rows=1)
"""
ANALYZE = """-> Aggregate: count(0)  (cost=20.4 rows=1) (actual time=0.231..0.232 rows=1 loops=1)
    -> Filter: (orders.total > 10.00)  (cost=20.2 rows=66.7) (actual time=0.0336..0.21 rows=195 loops=1)
        -> Table scan on orders  (cost=20.2 rows=200) (actual time=0.0317..0.189 rows=200 loops=1)
"""
MARIADB_JSON = {
    "query_block": {
        "select_id": 1,
        "filesort": {
            "sort_key": "o.total",
            "temporary_table": {
                "nested_loop": [
                    {
                        "table": {
                            "table_name": "o",
                            "access_type": "ALL",
                            "rows": 50000,
                            "filtered": 100,
                            "attached_condition": "o.total > 10",
                        }
                    },
                    {
                        "table": {
                            "table_name": "c",
                            "access_type": "eq_ref",
                            "key": "PRIMARY",
                            "rows": 1,
                            "filtered": 100,
                        }
                    },
                ]
            },
        },
    }
}


def test_version_and_grantee_parsing() -> None:
    assert parse_version("8.0.46-0ubuntu0.24.04.4") == 80046
    assert parse_version("10.11.14-MariaDB-0ubuntu0.24.04.1") == 101114
    assert parse_version("garbage") == 0
    assert normalize_grantee("'app'@'%'") == "app@%"
    assert normalize_grantee("`reporting`") == "reporting"


def test_tree_plan_and_issue_detection() -> None:
    root = parse_tree(TREE)
    assert root and root.operator == "Sort" and root.estimated_rows == 33333
    nl = root.children[0]
    assert nl.operator == "Nested loop inner join" and len(nl.children) == 2
    scan = nl.children[0].children[0]
    assert scan.operator == "Table scan" and scan.relation == "o" and scan.total_cost == 1015
    lookup = nl.children[1]
    assert lookup.relation == "c" and lookup.index == "PRIMARY"
    codes = {i.code for i in analyze_plan(root, analyzed=False)}
    assert "FULL_SCAN" in codes and "EXPENSIVE_NESTED_LOOP" not in codes


def test_explain_analyze_tree_has_actuals() -> None:
    root = parse_tree(ANALYZE)
    assert root and root.actual_rows == 1 and root.actual_total_ms == 0.232
    assert root.children[0].children[0].actual_rows == 200 and root.children[0].children[0].loops == 1


def test_mariadb_json_plan() -> None:
    root = parse_json(MARIADB_JSON)
    assert root and root.operator == "Query block #1"
    sort = root.children[0]
    assert sort.operator == "Sort" and sort.detail.startswith("o.total")
    tmp = sort.children[0]
    assert tmp.operator == "Temporary Table"
    nl = tmp.children[0]
    assert nl.operator == "Nested Loop" and [c.operator for c in nl.children] == [
        "Full Table Scan",
        "Unique Index Lookup",
    ]
    assert "FULL_SCAN" in {i.code for i in analyze_plan(root, analyzed=False)}


def test_admin_statements_are_flavor_specific_and_mask_passwords() -> None:
    my, maria = MySQLAdapter(SPEC), MariaDBAdapter(SPEC)
    s = my.build_admin("create_user", {"name": "app@10.0.%", "password": "S3cret-Pass!", "require_tls": True})
    assert s.statements == ["CREATE USER %s@%s IDENTIFIED BY %s REQUIRE SSL"]
    assert s.params == [("app", "10.0.%", "S3cret-Pass!")]
    assert "S3cret" not in s.display[0] and "********" in s.display[0]
    g = my.build_admin(
        "grant",
        {
            "privileges": ["select", "insert"],
            "object_type": "table",
            "database": "shop",
            "object_name": "orders",
            "grantee": "app",
        },
    )
    assert g.statements == ["GRANT SELECT, INSERT ON `shop`.`orders` TO %s@%s"] and g.params == [("app", "%")]
    # MariaDB roles have no host part
    r = maria.build_admin("grant", {"object_type": "role", "role": "reporting", "grantee": "reporting_admin"})
    assert r.statements == ["GRANT %s TO %s"] and r.params == [("reporting", "reporting_admin")]
    idx = my.build_admin(
        "create_index", {"database": "shop", "table": "orders", "name": "ix_t", "columns": "total, id"}
    )
    assert idx.statements[0].endswith("(`total`, `id`) ALGORITHM=INPLACE LOCK=NONE")
    assert my.build_admin("drop_database", {"name": "old"}).risk == "DESTRUCTIVE"


@pytest.mark.parametrize(
    "params",
    [
        {"name": "x`; DROP DATABASE y; --", "password": "longenough"},
        {"name": "ok", "password": "short"},
        {"name": "ok@bad host", "password": "longenough!"},
    ],
)
def test_admin_input_validation(params: dict) -> None:
    with pytest.raises(A.AdminValidationError):
        MySQLAdapter(SPEC).build_admin("create_user", params)


def test_admin_refuses_dangerous_privileges() -> None:
    for priv in ("SUPER", "FILE", "ALL PRIVILEGES", "GRANT OPTION"):
        with pytest.raises(A.AdminValidationError):
            MySQLAdapter(SPEC).build_admin(
                "grant", {"privileges": [priv], "object_type": "database", "database": "shop", "grantee": "app"}
            )


@pytest.mark.parametrize(
    ("exc", "cls"),
    [
        (
            pymysql.err.OperationalError(1045, "Access denied for user 'mon'@'x' (using password: YES)"),
            AdapterAuthenticationError,
        ),
        (pymysql.err.OperationalError(1049, "Unknown database 'nope'"), AdapterDatabaseNotFound),
        (
            pymysql.err.OperationalError(2003, "Can't connect to MySQL server on 'db.test' ([Errno 111])"),
            AdapterConnectionError,
        ),
        (
            pymysql.err.OperationalError(1227, "Access denied; you need (at least one of) the PROCESS privilege(s)"),
            AdapterPermissionError,
        ),
        (pymysql.err.OperationalError(1095, "You are not owner of thread 7"), AdapterPermissionError),
        (
            pymysql.err.ProgrammingError(1146, "Table 'performance_schema.data_locks' doesn't exist"),
            AdapterObjectMissing,
        ),
        (
            pymysql.err.OperationalError(
                3024, "Query execution was interrupted, maximum statement execution time exceeded"
            ),
            AdapterTimeout,
        ),
        (
            pymysql.err.OperationalError(1969, "Query execution was interrupted (max_statement_time exceeded)"),
            AdapterTimeout,
        ),
    ],
)
def test_error_classification(exc: Exception, cls: type) -> None:
    err = MySQLAdapter(SPEC)._classify(exc)
    assert isinstance(err, cls)
    assert "pw" not in str(err)


def test_session_timeouts_differ_by_flavor() -> None:
    assert MySQLAdapter(SPEC)._session_setup(30) == ["SET SESSION MAX_EXECUTION_TIME = 30000"]
    assert MariaDBAdapter(SPEC)._session_setup(30) == ["SET SESSION max_statement_time = 30"]


SERVER_ROW = {
    "version": "10.11.6-MariaDB",
    "version_comment": "MariaDB Server",
    "hostname": "db",
    "port": 3306,
    "current_account": "mon@%",
    "read_only": 0,
    "charset": "utf8mb4",
    "collation_name": "utf8mb4_general_ci",
    "server_id": 1,
    "performance_schema": 0,
    "os": "Linux",
    "machine": "x86_64",
    "max_connections": 151,
    "current_db": None,
}


async def test_mariadb_without_performance_schema_needs_configuration() -> None:
    """MariaDB ships with performance_schema=OFF: query statistics are classified as
    REQUIRES_CONFIGURATION and never faked."""
    a = MariaDBAdapter(SPEC)
    conn = attach(
        a,
        FakeConnection(
            [
                ("SET SESSION", []),
                ("SELECT VERSION() AS version", [SERVER_ROW]),
                ("SHOW GLOBAL STATUS LIKE 'Uptime'", [{"variable_name": "Uptime", "value": "1000"}]),
                ("SHOW REPLICA STATUS", []),
                ("@@basedir", [{"basedir": "/usr"}]),
                ("information_schema.INNODB_TRX", [{"n": 0}]),
                ("FROM mysql.user u", [{"user": "root", "host": "localhost", "is_role": "N"}]),
                ("FROM mysql.roles_mapping", []),
            ]
        ),
    )
    caps = await a.discover_capabilities()
    assert caps[Capability.QUERY_STATISTICS].level is CapabilityLevel.REQUIRES_CONFIGURATION
    assert "performance_schema=ON" in (caps[Capability.QUERY_STATISTICS].note or "")
    assert caps[Capability.REPLICATION].note and "not configured" in caps[Capability.REPLICATION].note
    with pytest.raises(AdapterNotSupported, match="performance_schema is OFF"):
        await a.get_query_statistics()
    assert not conn.executed("events_statements_summary_by_digest")
    await a.close()


async def test_flavor_mismatch_is_reported() -> None:
    a = MySQLAdapter(SPEC)
    attach(
        a,
        FakeConnection(
            [
                ("SET SESSION", []),
                ("@@server_uuid", [{"server_uuid": None, "super_read_only": 0}]),
                ("SELECT VERSION() AS version", [SERVER_ROW]),
                ("SHOW GLOBAL STATUS LIKE 'Uptime'", [{"variable_name": "Uptime", "value": "1000"}]),
                ("SHOW REPLICA STATUS", []),
                ("SHOW SLAVE STATUS", []),
                ("@@basedir", [{"basedir": "/usr"}]),
                ("information_schema.INNODB_TRX", [{"n": 0}]),
                ("FROM mysql.user", []),
                ("FROM mysql.role_edges", []),
            ]
        ),
    )
    caps = await a.discover_capabilities()
    assert caps[Capability.CONNECTION].level is CapabilityLevel.PARTIAL and "mariadb" in (
        caps[Capability.CONNECTION].note or ""
    )
    await a.close()
