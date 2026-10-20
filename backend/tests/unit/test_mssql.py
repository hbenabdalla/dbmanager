"""SQL Server adapter units: T-SQL syntax of every catalog/DMV query, connection string and TLS
mapping, error classification, showplan parsing, explain flow, capability discovery and the
administration builders. No SQL Server instance is available in CI: real-server validation of
this adapter is pending (see docs/database-engines/index.md)."""

from __future__ import annotations

import re

import pytest
import sqlglot

from app.adapters import admin as A
from app.adapters.base import (
    AdapterAuthenticationError,
    AdapterConnectionError,
    AdapterDatabaseNotFound,
    AdapterError,
    AdapterPermissionError,
    AdapterTimeout,
    AdapterTLSError,
    Capability,
    CapabilityLevel,
    ConnectionSpec,
    DriverNotInstalled,
)
from app.adapters.dbapi import AdapterObjectMissing
from app.adapters.mssql import sql as Q
from app.adapters.mssql.adapter import SQLServerAdapter
from app.adapters.mssql.plans import parse_showplan
from app.domain.plan_analysis import analyze_plan
from tests.unit.fakedb import FakeConnection, attach

SPEC = ConnectionSpec(host="sql.test", port=1433, username="dbm_monitor", password="S3cret-Pw!", database="master")

SHOWPLAN = """<?xml version="1.0" encoding="utf-16"?>
<ShowPlanXML xmlns="http://schemas.microsoft.com/sqlserver/2004/07/showplan" Version="1.6" Build="16.0.1000.6">
 <BatchSequence><Batch><Statements>
  <StmtSimple StatementText="SELECT * FROM dbo.orders WHERE customer_id = 42" StatementType="SELECT"
     StatementSubTreeCost="12.5" StatementOptmLevel="FULL" CardinalityEstimationModelVersion="160">
   <QueryPlan>
    <MissingIndexes>
     <MissingIndexGroup Impact="93.1">
      <MissingIndex Database="[shop]" Schema="[dbo]" Table="[orders]">
       <ColumnGroup Usage="EQUALITY"><Column Name="[customer_id]" ColumnId="2"/></ColumnGroup>
       <ColumnGroup Usage="INCLUDE"><Column Name="[total]" ColumnId="3"/></ColumnGroup>
      </MissingIndex>
     </MissingIndexGroup>
    </MissingIndexes>
    <RelOp NodeId="0" PhysicalOp="Sort" LogicalOp="Sort" EstimateRows="120" EstimateIO="0.01" EstimateCPU="0.02"
           EstimatedTotalSubtreeCost="12.5" Parallel="false">
     <Warnings><SpillToTempDb SpillLevel="1"/></Warnings>
     <Sort Distinct="false">
      <RelOp NodeId="1" PhysicalOp="Clustered Index Scan" LogicalOp="Clustered Index Scan" EstimateRows="120"
             EstimateIO="9.1" EstimateCPU="1.1" EstimatedTotalSubtreeCost="10.2" Parallel="false">
       <IndexScan Ordered="false">
        <Object Database="[shop]" Schema="[dbo]" Table="[orders]" Index="[PK_orders]"/>
        <Predicate><ScalarOperator ScalarString="[shop].[dbo].[orders].[customer_id]=(42)"/></Predicate>
       </IndexScan>
      </RelOp>
     </Sort>
    </RelOp>
   </QueryPlan>
  </StmtSimple>
 </Statements></Batch></BatchSequence>
</ShowPlanXML>"""


def _server_row(**over: object) -> dict[str, object]:
    row: dict[str, object] = {
        "product_version": "16.0.4135.4",
        "product_level": "RTM",
        "update_level": "CU15",
        "edition": "Developer Edition (64-bit)",
        "engine_edition": 3,
        "machine_name": "sql01",
        "server_name": "sql01",
        "instance_name": None,
        "is_clustered": 0,
        "is_hadr_enabled": 0,
        "integrated_security_only": 0,
        "collation_name": "SQL_Latin1_General_CP1_CI_AS",
        "version_full": "Microsoft SQL Server 2022 (RTM-CU15)\n...",
        "login_name": "dbm_monitor",
        "current_db": "master",
        "is_sysadmin": 0,
        "view_server_state": 1,
        "view_any_definition": 1,
    }
    row.update(over)
    return row


def _db_row(name: str, did: int, qs: int | None = 1) -> dict[str, object]:
    r: dict[str, object] = {
        "name": name,
        "database_id": did,
        "owner_name": "sa",
        "state_desc": "ONLINE",
        "recovery_model_desc": "FULL",
        "compatibility_level": 160,
        "collation_name": "SQL_Latin1_General_CP1_CI_AS",
        "is_read_only": 0,
        "user_access_desc": "MULTI_USER",
        "create_date": None,
        "is_encrypted": 0,
        "is_trustworthy_on": 0,
        "is_auto_shrink_on": 0,
        "is_auto_close_on": 0,
        "log_reuse_wait_desc": "NOTHING",
        "is_read_committed_snapshot_on": 0,
        "snapshot_isolation_state_desc": "OFF",
        "accessible": 1,
    }
    if qs is not None:
        r["is_query_store_on"] = qs
    return r


def _adapter(routes: list, **spec: object) -> tuple[SQLServerAdapter, FakeConnection]:
    a = SQLServerAdapter(SPEC.model_copy(update=spec) if spec else SPEC)
    return a, attach(a, FakeConnection(routes))


# ---------------------------------------------------------------------------------------------
def test_every_query_is_valid_tsql() -> None:
    fill = {
        "query_store": "d.is_query_store_on,",
        "spills": "NULL AS spills,",
        "names": "'a'",
        "types": "'U', 'V'",
        "table_filter": "AND o.name = ?",
    }
    checked = 0
    for name, sql in vars(Q).items():
        if not name.isupper() or not isinstance(sql, str) or sql.startswith("DBCC"):
            continue
        stmt = sql.format(**fill) if "{" in sql else sql
        sqlglot.parse(stmt, read="tsql", error_level=sqlglot.ErrorLevel.RAISE)
        checked += 1
    assert checked > 40


def test_connection_string_tls_mapping_and_no_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.adapters import drivers

    monkeypatch.setattr(drivers, "_odbc_drivers", lambda: ["PostgreSQL Unicode", "ODBC Driver 18 for SQL Server"])
    a = SQLServerAdapter(SPEC.model_copy(update={"tls_mode": "verify-full"}))
    cs, _ = a.connection_string("shop")
    assert "Encrypt=yes" in cs and "TrustServerCertificate=no" in cs and "DATABASE={shop}" in cs
    assert "S3cret" not in cs and "PWD" not in cs and "UID" not in cs
    cs, _ = SQLServerAdapter(SPEC.model_copy(update={"tls_mode": "prefer"})).connection_string(None)
    assert "Encrypt=Optional" in cs and "TrustServerCertificate=yes" in cs
    strict = SQLServerAdapter(SPEC.model_copy(update={"tls_mode": "require", "options": {"encrypt": "strict"}}))
    cs, _ = strict.connection_string(None)
    assert "Encrypt=Strict" in cs and "TrustServerCertificate=no" in cs
    named = SQLServerAdapter(
        SPEC.model_copy(update={"options": {"instance_name": "SQL2019", "application_intent": "ReadOnly"}})
    )
    cs, _ = named.connection_string(None)
    assert r"SERVER={sql.test\SQL2019}" in cs and "ApplicationIntent=ReadOnly" in cs
    # brace-quoting prevents connection-string injection through option values
    evil = SQLServerAdapter(SPEC.model_copy(update={"options": {"host_name_in_certificate": "x};PWD=y"}}))
    cs, _ = evil.connection_string(None)
    assert "HostNameInCertificate={x}};PWD=y}" in cs


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (("28000", "[28000] Login failed for user 'x'. (18456)"), AdapterAuthenticationError),
        (("42000", '[42000] Cannot open database "nope" requested by the login. (4060)'), AdapterDatabaseNotFound),
        (("08001", "[08001] TCP Provider: Error code 0x2749 (10057)"), AdapterConnectionError),
        (("08001", "[08001] SSL Provider: certificate verify failed (-1)"), AdapterTLSError),
        (
            ("01000", "[01000] [unixODBC][Driver Manager]Can't open lib 'ODBC Driver 18 for SQL Server'"),
            DriverNotInstalled,
        ),
        (("42000", "[42000] VIEW SERVER STATE permission was denied on object 'server' (300)"), AdapterPermissionError),
        (("42S02", "[42S02] Invalid object name 'sys.dm_db_resource_stats'. (208)"), AdapterObjectMissing),
        (("HY000", "[HY000] Lock request time out period exceeded. (1222)"), AdapterTimeout),
        (("HY008", "[HY008] Operation canceled (0)"), AdapterTimeout),
    ],
)
def test_error_classification(args: tuple[str, str], expected: type) -> None:
    a = SQLServerAdapter(SPEC)
    err = a._classify(Exception(*args))
    assert isinstance(err, expected), (args, type(err))


def test_error_messages_mask_passwords() -> None:
    err = SQLServerAdapter(SPEC)._classify(Exception("HY000", "bad connection string PWD={S3cret-Pw!};x"))
    assert "S3cret" not in str(err)


def test_showplan_parsing_and_issues() -> None:
    root, missing, attrs = parse_showplan(SHOWPLAN)
    assert root is not None and root.operator == "Sort" and root.total_cost == 12.5
    scan = root.children[0]
    assert scan.operator == "Clustered Index Scan" and scan.relation == "dbo.orders" and scan.index == "PK_orders"
    assert "customer_id" in (scan.filter or "")
    assert root.raw["engine_warnings"][0]["code"] == "TEMP_SPILL"
    assert missing == [{"impact": 93.1, "table": "shop.dbo.orders", "equality": ["customer_id"], "include": ["total"]}]
    assert attrs["StatementOptmLevel"] == "FULL"
    codes = {i.code for i in analyze_plan(root, analyzed=False)}
    assert "TEMP_SPILL" in codes


def test_explain_uses_showplan_and_never_executes() -> None:
    a, conn = _adapter(
        [
            ("SET SHOWPLAN_XML ON", []),
            ("SET SHOWPLAN_XML OFF", []),
            ("SELECT * FROM dbo.orders", [{"plan": SHOWPLAN}]),
        ]
    )
    import asyncio

    res = asyncio.run(a.explain_query("SELECT * FROM dbo.orders WHERE customer_id = 42;", "shop"))
    assert res.executed is False and res.mode == "estimate"
    assert [s for s, _ in conn.log] == [
        "SET SHOWPLAN_XML ON",
        "SELECT * FROM dbo.orders WHERE customer_id = 42",
        "SET SHOWPLAN_XML OFF",
    ]
    hint = next(i for i in res.issues if i.code == "MISSING_INDEX_HINT")
    assert "evidence" in hint.title and "Check existing similar indexes" in hint.detail
    with pytest.raises(AdapterError):
        asyncio.run(a.explain_query("SET SHOWPLAN_XML OFF"))


def test_explain_analyze_rolls_back() -> None:
    a, conn = _adapter(
        [
            ("SET STATISTICS XML", []),
            ("UPDATE dbo.orders", [{"Microsoft SQL Server 2005 XML Showplan": SHOWPLAN}]),
        ]
    )
    import asyncio

    res = asyncio.run(a.explain_query("UPDATE dbo.orders SET total = total WHERE id = 1", "shop", analyze=True))
    assert res.executed is True and conn.rollbacks == 1 and conn.commits == 0
    assert conn.executed("SET STATISTICS XML OFF")
    assert "rolled back" in res.notes[0]


def test_discovery_without_view_server_state_marks_components_only() -> None:
    import asyncio

    a, _ = _adapter(
        [
            ("SERVERPROPERTY('ProductVersion')", [_server_row(view_server_state=0, view_any_definition=0)]),
            ("FROM sys.dm_os_sys_info", AdapterPermissionError("VIEW SERVER STATE permission was denied (300)")),
            ("WHERE name = 'tempdb'", [{"started": None}]),
            ("FROM sys.databases d", [_db_row("master", 1), _db_row("shop", 5, qs=0)]),
            (re.compile(r"FROM sys\.master_files"), AdapterPermissionError("denied (300)")),
        ]
    )
    caps = asyncio.run(a.discover_capabilities())
    assert caps[Capability.LOCKS].level == CapabilityLevel.REQUIRES_PRIVILEGE
    assert caps[Capability.SESSIONS].level == CapabilityLevel.PARTIAL
    assert caps[Capability.QUERY_STATISTICS].level == CapabilityLevel.REQUIRES_PRIVILEGE
    assert caps[Capability.QUERY_HISTORY].level == CapabilityLevel.REQUIRES_CONFIGURATION
    assert caps[Capability.DATABASES].level == CapabilityLevel.FULL
    assert caps[Capability.SESSION_CANCEL].level == CapabilityLevel.NOT_SUPPORTED
    assert "not configured" in (caps[Capability.REPLICATION].note or "")


def test_server_info_versions_and_azure() -> None:
    import asyncio

    a, _ = _adapter(
        [
            ("SERVERPROPERTY('ProductVersion')", [_server_row(product_version="12.0.2000.8", engine_edition=5)]),
            ("WHERE name = 'tempdb'", [{"started": None}]),
        ]
    )
    info = asyncio.run(a.get_server_info())
    assert info.version_num == 12_000_000 + 2000 and info.cloud_provider == "azure-sql"


def test_admin_create_login_masks_password_and_maps_database_user() -> None:
    a = SQLServerAdapter(SPEC)
    stmt = a.build_admin("create_user", {"name": "app_ro", "password": "Very-Secret-1", "database": "shop"})
    assert stmt.statements[0].startswith("CREATE LOGIN [app_ro] WITH PASSWORD = N'Very-Secret-1'")
    assert all("Very-Secret-1" not in d for d in stmt.display)
    assert stmt.statement_databases == [None, "shop"]
    assert stmt.statements[1] == "CREATE USER [app_ro] FOR LOGIN [app_ro]"
    quoted = a.build_admin("create_user", {"name": "x", "password": "it's-a-secret"})
    assert "N'it''s-a-secret'" in quoted.statements[0]


def test_admin_grants_and_validation() -> None:
    a = SQLServerAdapter(SPEC)
    g = a.build_admin(
        "grant",
        {"object_type": "schema", "database": "shop", "schema": "sales", "grantee": "app_ro", "privileges": ["SELECT"]},
    )
    assert g.statements == ["GRANT SELECT ON SCHEMA::[sales] TO [app_ro]"] and g.database == "shop"
    r = a.build_admin(
        "revoke", {"object_type": "role", "database": "shop", "role": "db_datareader", "grantee": "app_ro"}
    )
    assert r.statements == ["ALTER ROLE [db_datareader] DROP MEMBER [app_ro]"]
    idx = a.build_admin(
        "create_index",
        {
            "database": "shop",
            "table": "orders",
            "name": "ix_cust",
            "columns": ["customer_id"],
            "include": ["total"],
            "online": True,
        },
    )
    assert idx.statements == [
        "CREATE NONCLUSTERED INDEX [ix_cust] ON [dbo].[orders] ([customer_id]) INCLUDE ([total]) WITH (ONLINE = ON)"
    ]
    with pytest.raises(A.AdminValidationError):
        a.build_admin("create_database", {"name": "x]; DROP DATABASE prod; --"})
    with pytest.raises(A.AdminValidationError):
        a.build_admin(
            "grant", {"object_type": "database", "database": "shop", "grantee": "u", "privileges": ["CONTROL"]}
        )


def test_admin_runs_each_statement_in_its_database() -> None:
    import asyncio

    a = SQLServerAdapter(SPEC)
    opened: list[str | None] = []
    conns: dict[str | None, FakeConnection] = {}

    def _open(database: str | None) -> FakeConnection:
        opened.append(database)
        return conns.setdefault(database, FakeConnection([("CREATE", [])], empty_has_description=False))

    a._open = _open  # type: ignore[method-assign]
    stmt = a.build_admin("create_user", {"name": "app_ro", "password": "Very-Secret-1", "database": "shop"})
    out = asyncio.run(a.run_admin(stmt))
    assert out["statements_executed"] == 2
    assert conns[None].executed("CREATE LOGIN") and conns["shop"].executed("CREATE USER")
    assert not conns["shop"].executed("CREATE LOGIN")


def test_query_stats_fallback_and_privilege_mapping() -> None:
    import asyncio

    from app.adapters.base import AdapterNotSupported

    stat = {
        "query_hash": "0xABC",
        "statement_text": "SELECT 1",
        "database_name": "shop",
        "calls": 4,
        "total_ms": 20.0,
        "max_ms": 9.0,
        "rows_count": 4,
        "cpu_ms": 3.0,
        "logical_reads": 40,
        "physical_reads": 1,
        "spills": None,
        "plans": 2,
        "logical_writes": 0,
        "last_execution_time": None,
        "first_cached": None,
    }
    a, conn = _adapter(
        [
            ("SERVERPROPERTY('ProductVersion')", [_server_row(product_version="14.0.1000.169")]),
            ("SUM(qs.total_spills)", AdapterObjectMissing("Invalid column name 'total_spills'. (207)")),
            ("NULL AS spills", [stat]),
        ]
    )
    rows = asyncio.run(a.get_query_statistics(10))
    assert rows[0].mean_ms == 5.0 and rows[0].temp_blocks is None and conn.executed("NULL AS spills")
    assert rows[0].plans == 2 and conn.executed("COUNT(DISTINCT qs.query_plan_hash) AS plans")
    denied, _ = _adapter(
        [
            ("SERVERPROPERTY('ProductVersion')", [_server_row()]),
            ("FROM sys.dm_exec_query_stats", AdapterPermissionError("VIEW SERVER STATE permission was denied (300)")),
        ]
    )
    with pytest.raises(AdapterNotSupported):
        asyncio.run(denied.get_query_statistics(10))


def test_every_read_path_survives_an_empty_instance() -> None:
    """Every collector runs against a server whose DMVs return no rows (fresh instance or a
    login that sees nothing): no KeyError/format error, just empty results."""
    import asyncio

    a, conn = _adapter(
        [
            ("SERVERPROPERTY('ProductVersion')", [_server_row()]),
            ("FROM sys.databases d", [_db_row("master", 1), _db_row("shop", 5)]),
            (re.compile(r"."), []),
        ]
    )

    async def run() -> None:
        await a.discover_capabilities()
        await a.list_databases()
        await a.list_schemas("shop")
        await a.list_objects("shop", "dbo")
        await a.list_users()
        await a.list_roles()
        await a.get_sessions()
        await a.get_locks()
        await a.get_performance_metrics(slow=True)
        await a.get_query_statistics(10)
        await a.get_replication_status()
        await a.get_configuration()
        await a.get_storage_metrics()
        await a.get_privileges("shop")
        await a.get_security_context()
        await a.get_tuning_context()

    asyncio.run(run())
    assert conn.log


def test_constraints_are_listed_for_a_schema() -> None:
    import asyncio

    a, conn = _adapter(
        [
            ("SERVERPROPERTY('ProductVersion')", [_server_row()]),
            ("FROM sys.databases d", [_db_row("master", 1), _db_row("shop", 5)]),
            (
                "FROM sys.key_constraints kc WHERE kc.schema_id",
                [
                    {
                        "name": "PK_orders",
                        "constraint_type": "PRIMARY_KEY_CONSTRAINT",
                        "table_name": "orders",
                        "is_disabled": False,
                        "is_not_trusted": False,
                    },
                    {
                        "name": "FK_orders_customer",
                        "constraint_type": "FOREIGN_KEY_CONSTRAINT",
                        "table_name": "orders",
                        "is_disabled": False,
                        "is_not_trusted": True,
                    },
                ],
            ),
        ]
    )
    out = asyncio.run(a.list_objects("shop", "dbo", "constraint"))
    assert [(o.name, o.parent) for o in out] == [("PK_orders", "orders"), ("FK_orders_customer", "orders")]
    assert out[1].extra == {"constraint_type": "FOREIGN_KEY_CONSTRAINT", "disabled": False, "trusted": False}
    assert any("SCHEMA_ID(?)" in s for s, _ in conn.log)


def test_roles_of_every_accessible_database_on_a_server_registration() -> None:
    import asyncio

    def role(name: str, fixed: bool = False) -> dict[str, object]:
        return {"name": name, "type": "R", "is_fixed_role": fixed}

    routes = [
        ("SERVERPROPERTY('ProductVersion')", [_server_row()]),
        ("FROM sys.server_principals WHERE type = 'R'", [{"name": "sysadmin", "is_fixed_role": True}]),
        ("HAS_DBACCESS(name)", [{"name": "shop"}, {"name": "hr"}, {"name": "master"}]),
        ("FROM sys.database_role_members", []),
    ]
    a, conn = _adapter([*routes, ("FROM sys.database_principals", [role("db_owner", True), role("app_reader")])])
    names = [r.name for r in asyncio.run(a.list_roles())]
    assert {"shop:app_reader", "hr:app_reader", "master:db_owner"} <= set(names)
    # a database registration reads that database only
    b, conn = _adapter([*routes, ("FROM sys.database_principals", [role("app_reader")])], database="shop")
    names = [r.name for r in asyncio.run(b.list_roles())]
    assert [n for n in names if ":" in n] == ["shop:app_reader"]
    assert not any("HAS_DBACCESS" in s for s, _ in conn.log)
