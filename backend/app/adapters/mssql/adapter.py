"""Microsoft SQL Server adapter (Phase 4): SQL Server 2016+, Azure SQL Managed Instance and,
with reduced server-level views, Azure SQL Database."""

from __future__ import annotations

import re
import time
from typing import Any, ClassVar

from app.adapters.base import (
    FULL,
    NOT_IMPLEMENTED,
    NOT_SUPPORTED,
    PARTIAL,
    REQUIRES_CONFIGURATION,
    REQUIRES_PRIVILEGE,
    AdapterError,
    Capability,
    CapabilityMap,
    ExecutionResult,
    cap,
)
from app.adapters.dbapi import jsonable, text
from app.adapters.mssql import admin as mssql_admin
from app.adapters.mssql.evidence import SQLServerEvidenceMixin
from app.adapters.mssql.inventory import SQLServerInventoryMixin
from app.adapters.mssql.monitoring import SQLServerMonitoringMixin
from app.adapters.mssql.plans import parse_showplan
from app.domain.plan_analysis import ExplainResult, PlanIssue, analyze_plan

C = Capability


class SQLServerAdapter(SQLServerEvidenceMixin, SQLServerMonitoringMixin, SQLServerInventoryMixin):
    implementation_phase = 4
    implemented = True
    support_level = "supported"
    support_note = (
        "SQL Server 2016–2022 and Azure SQL Managed Instance with SQL logins; Azure SQL Database with database-scoped "
        "views. Windows integrated (Kerberos) authentication is not implemented."
    )
    static_capabilities: ClassVar[CapabilityMap] = {
        C.CONNECTION: cap(FULL, "SQL logins over TLS (Encrypt optional/mandatory/strict); Entra ID via ODBC options"),
        C.SERVER_INFO: cap(FULL),
        C.DATABASES: cap(FULL, "state, recovery model, compatibility level, data/log size"),
        C.DATABASE_CREATE: cap(FULL, "guarded administration action"),
        C.DATABASE_DROP: cap(FULL, "guarded administration action with typed confirmation"),
        C.SCHEMAS: cap(FULL),
        C.OBJECTS: cap(FULL, "tables, views, indexes, procedures, functions, triggers, sequences, synonyms"),
        C.OBJECT_DDL: cap(
            PARTIAL, "module definitions via OBJECT_DEFINITION; table DDL reconstructed from sys.columns"
        ),
        C.USERS: cap(FULL, "server logins (sys.server_principals); database users per database"),
        C.ROLES: cap(FULL, "server roles and database roles"),
        C.PRIVILEGES: cap(FULL, "server and database permissions, GRANT/DENY, role memberships"),
        C.SESSIONS: cap(FULL, "sys.dm_exec_sessions / requests (VIEW SERVER STATE shows every session)"),
        C.SESSION_CANCEL: cap(NOT_SUPPORTED, "T-SQL cannot cancel another session's statement; use terminate (KILL)"),
        C.SESSION_TERMINATE: cap(FULL, "KILL <spid> (ALTER ANY CONNECTION)"),
        C.LOCKS: cap(FULL, "sys.dm_tran_locks with blocking chains from sys.dm_os_waiting_tasks"),
        C.PERFORMANCE_METRICS: cap(FULL, "sys.dm_os_performance_counters, session DMVs"),
        C.CPU_METRICS: cap(FULL, "scheduler monitor ring buffer (sys.dm_db_resource_stats on Azure SQL Database)"),
        C.MEMORY_METRICS: cap(FULL, "Memory Manager counters (total / target server memory)"),
        C.WAIT_EVENTS: cap(FULL, "current waits per request; cumulative sys.dm_os_wait_stats for advice"),
        C.QUERY_STATISTICS: cap(
            FULL, "plan cache (sys.dm_exec_query_stats) grouped by query_hash; Query Store not required"
        ),
        C.QUERY_HISTORY: cap(PARTIAL, "time-series history of plan-cache snapshots; Query Store detected per database"),
        C.EXPLAIN: cap(FULL, "SET SHOWPLAN_XML ON (compiles, does not execute)"),
        C.EXPLAIN_ANALYZE: cap(FULL, "SET STATISTICS XML ON inside a transaction that is rolled back"),
        C.REPLICATION: cap(
            PARTIAL, "Always On availability groups; log shipping / transactional replication not monitored"
        ),
        C.CONFIGURATION: cap(FULL, "sys.configurations (pending values flagged)"),
        C.STORAGE: cap(FULL, "database files and log usage"),
        C.TABLESPACES: cap(NOT_SUPPORTED, "SQL Server uses filegroups and files (shown under storage)"),
        C.SECURITY_SCAN: cap(FULL),
        C.LOGS: cap(NOT_IMPLEMENTED, "error log (xp_readerrorlog) is not read by this adapter"),
        C.SQL_WORKSPACE: cap(FULL),
        C.READ_ONLY_TRANSACTIONS: cap(
            PARTIAL, "no read-only transactions in SQL Server: classifier + permissions + rollback"
        ),
        C.ADMINISTRATION: cap(FULL, "create database/schema/login/role, grant, revoke, create index"),
        C.RECOMMENDATIONS: cap(FULL),
    }
    engine_capabilities: ClassVar[CapabilityMap] = static_capabilities

    async def discover_capabilities(self) -> CapabilityMap:
        caps = dict(self.capability_profile())
        info = await self.get_server_info()
        vss = bool(info.extra.get("view_server_state"))
        if not vss:
            note = "grant VIEW SERVER STATE (SQL Server 2022: VIEW SERVER PERFORMANCE STATE) to the monitoring login"
            for c in (C.SESSIONS, C.PERFORMANCE_METRICS, C.WAIT_EVENTS):
                caps[c] = cap(PARTIAL, note)
            for c in (C.LOCKS, C.QUERY_STATISTICS, C.CPU_METRICS, C.QUERY_HISTORY):
                caps[c] = cap(REQUIRES_PRIVILEGE, note)
        if not info.extra.get("view_any_definition") and not info.extra.get("is_sysadmin"):
            caps[C.OBJECT_DDL] = cap(PARTIAL, "grant VIEW ANY DEFINITION to see module definitions of other owners")
        dbs = await self.list_databases()
        qs = [d.extra.get("query_store") for d in dbs if not d.is_system and d.extra.get("query_store") is not None]
        if qs and not any(qs):
            caps[C.QUERY_HISTORY] = cap(
                REQUIRES_CONFIGURATION,
                "Query Store is off in every user database (ALTER DATABASE … SET QUERY_STORE = ON)",
            )
        elif qs:
            caps[C.QUERY_HISTORY] = cap(
                PARTIAL, f"Query Store on in {sum(1 for x in qs if x)} of {len(qs)} user databases"
            )
        if not info.extra.get("is_hadr_enabled"):
            caps[C.REPLICATION] = cap(FULL, "not configured: Always On availability groups are not enabled")
        if info.cloud_provider == "azure-sql":
            caps[C.CPU_METRICS] = cap(FULL, "sys.dm_db_resource_stats (database-level)")
            caps[C.STORAGE] = cap(PARTIAL, "Azure SQL Database: sys.master_files and DBCC SQLPERF are not available")
            caps[C.REPLICATION] = cap(NOT_SUPPORTED, "geo-replication is managed by Azure")
            caps[C.CONFIGURATION] = cap(
                PARTIAL, "server configuration is managed by Azure; database-scoped settings apply"
            )
        return caps

    # ---- explain ----------------------------------------------------------------------------
    def _explain_sync(self, stmt: str, database: str | None, analyze: bool) -> tuple[str, float]:
        conn = self._get_conn(database)
        cur = conn.cursor()
        start = time.perf_counter()
        try:
            if analyze:
                conn.autocommit = False
                cur.execute("SET STATISTICS XML ON")
                cur.execute(stmt)
                xml = None
                while True:
                    if (
                        cur.description
                        and len(cur.description) == 1
                        and "showplan" in str(cur.description[0][0]).lower()
                    ):
                        row = cur.fetchone()
                        xml = row[0] if row else None
                    elif cur.description:
                        cur.fetchall()
                    if not cur.nextset():
                        break
                return xml or "", (time.perf_counter() - start) * 1000
            cur.execute("SET SHOWPLAN_XML ON")
            try:
                cur.execute(stmt)
                row = cur.fetchone()
                return (row[0] if row else ""), (time.perf_counter() - start) * 1000
            finally:
                cur.execute("SET SHOWPLAN_XML OFF")
        finally:
            if analyze:
                try:
                    conn.rollback()
                    cur.execute("SET STATISTICS XML OFF")
                finally:
                    conn.autocommit = True
            cur.close()

    async def explain_query(self, sql: str, database: str | None = None, analyze: bool = False) -> ExplainResult:
        stmt = sql.strip().rstrip(";")
        if re.match(r"^\s*SET\s+(SHOWPLAN|STATISTICS)", stmt, re.I):
            raise AdapterError("submit the statement without SET SHOWPLAN / STATISTICS options")
        xml, elapsed = await self._run(self._explain_sync, stmt, database, analyze)
        if not xml:
            raise AdapterError("SQL Server returned no plan (statement may not be plannable, e.g. DDL)")
        root, missing, attrs = parse_showplan(xml)
        if root is None:
            raise AdapterError("the plan contains no operators")
        issues = analyze_plan(root, analyzed=analyze)
        for m in missing:
            issues.append(
                PlanIssue(
                    node_id=root.id,
                    severity="low",
                    code="MISSING_INDEX_HINT",
                    title="Optimizer missing-index hint (evidence, not a guarantee)",
                    detail=f"{m['table']}: equality {m.get('equality', [])}, inequality {m.get('inequality', [])}, "
                    f"include {m.get('include', [])}; estimated impact {m['impact']}%. Check existing similar indexes first.",
                )
            )
        notes = [
            f"{k}: {v}" for k, v in attrs.items() if k in ("StatementOptmLevel", "CardinalityEstimationModelVersion")
        ]
        if analyze:
            notes.insert(
                0, "The statement was executed with SET STATISTICS XML ON inside a transaction that was rolled back."
            )
        return ExplainResult(
            engine=self.engine,
            mode="analyze" if analyze else "estimate",
            executed=analyze,
            root=root,
            issues=issues,
            total_cost=root.total_cost,
            execution_ms=round(elapsed, 2) if analyze else None,
            raw={"showplan_xml": xml[:200_000], "missing_indexes": jsonable(missing)},
            notes=notes,
        )

    # ---- workspace: explicit transactions (the monitoring connection runs in autocommit) ----
    def _execute_sync(
        self, sql: str, database: str | None, read_only: bool, max_rows: int, timeout_s: float
    ) -> ExecutionResult:
        conn = self._get_conn(database)
        conn.autocommit = False
        try:
            return super()._execute_sync(sql, database, read_only, max_rows, timeout_s)
        finally:
            conn.autocommit = True

    # ---- administration -------------------------------------------------------------------
    def build_admin(self, action: str, params: dict[str, Any]) -> Any:
        return mssql_admin.build(action, params)


__all__ = ["SQLServerAdapter", "text"]
