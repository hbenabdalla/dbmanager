"""Oracle Database adapter (Phase 5): 12.2, 18c, 19c, 21c, 23ai — non-CDB, CDB root and PDBs,
on-premises, Exadata, OCI Base Database and Amazon RDS for Oracle.

Licensing: the adapter only uses views available without the Diagnostics or Tuning Pack (see
``app/adapters/oracle/sql.py``). AWR, ASH, ADDM, SQL Monitoring and the SQL Tuning Advisor
are never queried; the per-server ``diagnostics_pack`` / ``tuning_pack`` flags (default off)
are recorded and displayed but do not unlock pack-licensed views in this release."""

from __future__ import annotations

import re
import time
import uuid
from typing import Any, ClassVar

from app.adapters.base import (
    FULL,
    NOT_IMPLEMENTED,
    NOT_SUPPORTED,
    PARTIAL,
    REQUIRES_PRIVILEGE,
    AdapterError,
    Capability,
    CapabilityMap,
    ExecutionResult,
    cap,
)
from app.adapters.dbapi import jsonable, text
from app.adapters.oracle import admin as ora_admin
from app.adapters.oracle import sql as Q
from app.adapters.oracle.evidence import OracleEvidenceMixin
from app.adapters.oracle.inventory import OracleInventoryMixin
from app.adapters.oracle.monitoring import OracleMonitoringMixin
from app.adapters.oracle.plans import build_tree
from app.domain.plan_analysis import ExplainResult, analyze_plan

C = Capability
READ_STMT = re.compile(r"^\s*(SELECT|WITH)\b", re.I)
PLANNABLE = re.compile(r"^\s*(SELECT|WITH|INSERT|UPDATE|DELETE|MERGE)\b", re.I)
FETCH_CHUNK = 1000
MAX_FETCHED = 1_000_000


#: objects of the Oracle Diagnostics Pack (AWR, ASH, ADDM, metrics history) and Tuning Pack
#: (SQL Tuning / Access Advisor, SQL Monitoring); matched as whole identifiers
_DIAGNOSTICS = re.compile(
    r"(?<![\w$#])(?:"
    r"(?:DBA|CDB)_HIST_\w+|G?V\$ACTIVE_SESSION_HISTORY|DBA_ADDM_\w+|CDB_ADDM_\w+|DBMS_WORKLOAD_REPOSITORY"
    r"|DBMS_ADDM|DBMS_ADVISOR|DBMS_XPLAN\s*\.\s*DISPLAY_AWR|G?V\$(?:SYS|CON)?METRIC\w*|AWR_(?:ROOT|PDB|CDB)_\w+"
    r")(?![\w$#])",
    re.IGNORECASE,
)
_TUNING = re.compile(
    r"(?<![\w$#])(?:DBMS_SQLTUNE|DBMS_SQL_MONITOR|G?V\$SQL_MONITOR\w*|G?V\$SQL_PLAN_MONITOR|DBA_SQLTUNE_\w+"
    r"|DBA_ADVISOR_SQL\w*)(?![\w$#])",
    re.IGNORECASE,
)


def licensed_pack_needed(sql: str) -> str | None:
    """'diagnostics_pack' / 'tuning_pack' when a statement references objects of that pack."""
    if _TUNING.search(sql):
        return "tuning_pack"
    if _DIAGNOSTICS.search(sql):
        return "diagnostics_pack"
    return None


class OracleAdapter(OracleEvidenceMixin, OracleMonitoringMixin, OracleInventoryMixin):
    implementation_phase = 5
    implemented = True
    support_level = "supported"
    support_note = (
        "Oracle 12.2–23ai through python-oracledb thin mode (no Oracle Client). No Diagnostics/Tuning Pack "
        "features are used. Native Network Encryption needs TCPS instead (thin mode)."
    )
    licensable_features: ClassVar[list[dict[str, Any]]] = [
        {
            "key": "diagnostics_pack",
            "label": "Oracle Diagnostics Pack",
            "default": False,
            "covers": "AWR (DBA_HIST_*), ASH (V$ACTIVE_SESSION_HISTORY), ADDM",
            "used_by_db_manager": "Never queried by DBManager; the SQL workspace refuses statements on these "
            "objects unless the server is marked as licensed",
        },
        {
            "key": "tuning_pack",
            "label": "Oracle Tuning Pack",
            "default": False,
            "covers": "SQL Tuning Advisor (DBMS_SQLTUNE), SQL Access Advisor, SQL Monitoring",
            "used_by_db_manager": "Never queried by DBManager; the SQL workspace refuses statements on these "
            "objects unless the server is marked as licensed",
        },
    ]
    static_capabilities: ClassVar[CapabilityMap] = {
        C.CONNECTION: cap(FULL, "Oracle Net (TCP or TCPS/TLS) with service name or SID; password authentication"),
        C.SERVER_INFO: cap(FULL, "V$INSTANCE / V$DATABASE; CDB/PDB aware"),
        C.DATABASES: cap(FULL, "PDBs of a CDB (V$PDBS) or the non-CDB database"),
        C.DATABASE_CREATE: cap(NOT_SUPPORTED, "PDB creation needs storage/keystore decisions: use DBCA or SQL*Plus"),
        C.DATABASE_DROP: cap(NOT_SUPPORTED, "PDB removal is not offered"),
        C.SCHEMAS: cap(FULL, "Oracle users owning objects (DBA_USERS / CDB_USERS)"),
        C.OBJECTS: cap(
            FULL, "tables, views, materialized views, indexes, sequences, PL/SQL units, triggers, synonyms, types"
        ),
        C.OBJECT_DDL: cap(FULL, "DBMS_METADATA.GET_DDL (SELECT_CATALOG_ROLE for other schemas)"),
        C.USERS: cap(FULL, "DBA_USERS with status, profile, password verifiers, last login"),
        C.ROLES: cap(FULL, "DBA_ROLES with members and system privileges"),
        C.PRIVILEGES: cap(FULL, "system, object and role grants; SYSDBA-class privileges from V$PWFILE_USERS"),
        C.SESSIONS: cap(FULL, "V$SESSION with blocking chains (FINAL_BLOCKING_SESSION)"),
        C.SESSION_CANCEL: cap(FULL, "ALTER SYSTEM CANCEL SQL (18c+, needs ALTER SYSTEM)"),
        C.SESSION_TERMINATE: cap(FULL, "ALTER SYSTEM KILL SESSION … IMMEDIATE (needs ALTER SYSTEM)"),
        C.LOCKS: cap(FULL, "V$LOCK (TM/TX/UL) with blockers"),
        C.PERFORMANCE_METRICS: cap(FULL, "V$SYSSTAT counters, V$RESOURCE_LIMIT, V$SESSION"),
        C.CPU_METRICS: cap(FULL, "host CPU from V$OSSTAT busy/idle time (derived between samples)"),
        C.MEMORY_METRICS: cap(FULL, "V$SGA and V$PGASTAT"),
        C.WAIT_EVENTS: cap(FULL, "current waits from V$SESSION, cumulative V$SYSTEM_WAIT_CLASS — no ASH"),
        C.QUERY_STATISTICS: cap(FULL, "V$SQLSTATS (no pack license required)"),
        C.QUERY_HISTORY: cap(PARTIAL, "time-series history of V$SQLSTATS snapshots; AWR (DBA_HIST_*) is not read"),
        C.EXPLAIN: cap(FULL, "EXPLAIN PLAN into the session's PLAN_TABLE (not executed)"),
        C.EXPLAIN_ANALYZE: cap(PARTIAL, "STATISTICS_LEVEL=ALL run + V$SQL_PLAN_STATISTICS_ALL; DML is rolled back"),
        C.REPLICATION: cap(
            PARTIAL, "Data Guard: role, redo destinations, V$DATAGUARD_STATS lag; GoldenGate not monitored"
        ),
        C.CONFIGURATION: cap(FULL, "V$PARAMETER with SPFILE pending values"),
        C.STORAGE: cap(FULL, "datafiles, redo, FRA and top segments"),
        C.TABLESPACES: cap(FULL, "DBA_TABLESPACE_USAGE_METRICS (autoextend-aware)"),
        C.SECURITY_SCAN: cap(FULL),
        C.LOGS: cap(NOT_IMPLEMENTED, "alert log (V$DIAG_ALERT_EXT) is not read by this adapter"),
        C.SQL_WORKSPACE: cap(FULL),
        C.READ_ONLY_TRANSACTIONS: cap(FULL, "SET TRANSACTION READ ONLY"),
        C.ADMINISTRATION: cap(PARTIAL, "create user/role, grant, revoke, create index; no PDB lifecycle"),
        C.RECOMMENDATIONS: cap(FULL),
    }
    engine_capabilities: ClassVar[CapabilityMap] = static_capabilities

    async def discover_capabilities(self) -> CapabilityMap:
        caps = dict(self.capability_profile())
        info = await self._server()
        if not info["v_views"]:
            note = "grant SELECT_CATALOG_ROLE (or SELECT ANY DICTIONARY) to the monitoring user"
            for c in (
                C.SESSIONS,
                C.LOCKS,
                C.PERFORMANCE_METRICS,
                C.CPU_METRICS,
                C.MEMORY_METRICS,
                C.WAIT_EVENTS,
                C.QUERY_STATISTICS,
                C.QUERY_HISTORY,
                C.REPLICATION,
                C.CONFIGURATION,
                C.EXPLAIN_ANALYZE,
            ):
                caps[c] = cap(REQUIRES_PRIVILEGE, note)
        dba_ok = await self._try_one("SELECT 1 AS ok FROM dba_users WHERE ROWNUM = 1") is not None
        if not dba_ok:
            note = "DBA_ views are not visible: grant SELECT_CATALOG_ROLE (inventory is limited to ALL_ views)"
            for c in (C.SCHEMAS, C.OBJECTS, C.USERS, C.ROLES, C.PRIVILEGES, C.TABLESPACES, C.STORAGE, C.SECURITY_SCAN):
                caps[c] = cap(REQUIRES_PRIVILEGE, note)
        privs = set(info["session_privs"])
        if "ALTER SYSTEM" not in privs:
            caps[C.SESSION_TERMINATE] = cap(REQUIRES_PRIVILEGE, "needs ALTER SYSTEM (administration credential)")
            caps[C.SESSION_CANCEL] = cap(REQUIRES_PRIVILEGE, "needs ALTER SYSTEM (administration credential)")
        if int(info["major"]) < 18:
            caps[C.SESSION_CANCEL] = cap(NOT_SUPPORTED, "ALTER SYSTEM CANCEL SQL arrived in 18c; terminate instead")
        if info["is_cdb"] and not info["in_root"]:
            caps[C.DATABASES] = cap(PARTIAL, f"connected to PDB {info['con_name']}: other PDBs need their own service")
        role = text(info.get("database_role"))
        if role == "PRIMARY" and info["v_views"]:
            dests = await self._try(Q.ARCHIVE_DESTS)
            if dests == []:
                caps[C.REPLICATION] = cap(FULL, "not configured: no Data Guard redo transport destination")
        caps[C.QUERY_HISTORY] = cap(
            caps[C.QUERY_HISTORY].level if caps[C.QUERY_HISTORY].level == REQUIRES_PRIVILEGE else PARTIAL,
            "time-series history of V$SQLSTATS snapshots. AWR history: "
            + (
                "Diagnostics Pack marked as licensed, but AWR is not read in this release"
                if self.diagnostics_pack
                else "requires the Diagnostics Pack (not marked as licensed)"
            ),
        )
        caps[C.LOGS] = cap(NOT_IMPLEMENTED, "alert log is not read by this adapter")
        return caps

    # ---- explain ------------------------------------------------------------------------------
    def _explain_sync(self, stmt: str, analyze: bool) -> tuple[list[dict[str, Any]], float, dict[str, Any]]:
        conn = self._get_conn(None)
        cur = conn.cursor()
        start = time.perf_counter()
        meta: dict[str, Any] = {}

        def rows_of(c: Any) -> list[dict[str, Any]]:
            cols = [str(d[0]).lower() for d in c.description or []]
            return [dict(zip(cols, r, strict=False)) for r in c.fetchall()]

        try:
            if not analyze:
                sid = "DBM" + uuid.uuid4().hex[:24].upper()
                try:
                    cur.execute(f"EXPLAIN PLAN SET STATEMENT_ID = '{sid}' FOR {stmt}")
                    cur.execute(Q.PLAN_ROWS, {"sid": sid})
                    return rows_of(cur), (time.perf_counter() - start) * 1000, meta
                finally:
                    conn.rollback()  # PLAN_TABLE is a temporary table: discard the rows
            cur.execute("SELECT value FROM v$parameter WHERE name = 'statistics_level'")
            prev = cur.fetchone()
            level = (prev[0] if prev else "TYPICAL") or "TYPICAL"
            if level.upper() not in ("BASIC", "TYPICAL", "ALL"):
                level = "TYPICAL"
            cur.execute("ALTER SESSION SET STATISTICS_LEVEL = ALL")
            try:
                if READ_STMT.match(stmt):
                    cur.execute("SET TRANSACTION READ ONLY")
                cur.execute(stmt)
                fetched = 0
                if cur.description:
                    while fetched < MAX_FETCHED:
                        chunk = cur.fetchmany(FETCH_CHUNK)
                        if not chunk:
                            break
                        fetched += len(chunk)
                meta["rows_fetched"] = fetched
                elapsed = (time.perf_counter() - start) * 1000
                cur.execute(Q.PREV_CURSOR)
                prev_cursor = cur.fetchone()
                conn.rollback()
                if not prev_cursor or not prev_cursor[0]:
                    return [], elapsed, meta
                meta["sql_id"], meta["child_number"] = prev_cursor[0], prev_cursor[1]
                cur.execute(Q.PLAN_STATS, {"sql_id": prev_cursor[0], "child": prev_cursor[1]})
                return rows_of(cur), elapsed, meta
            finally:
                try:
                    conn.rollback()
                finally:
                    cur.execute(f"ALTER SESSION SET STATISTICS_LEVEL = {level.upper()}")
        finally:
            cur.close()

    async def execute_sql(
        self, sql: str, database: str | None, *, read_only: bool, max_rows: int, timeout_s: float
    ) -> ExecutionResult:
        """Statements run in the connected container: another PDB is refused, not ignored.
        Statements touching Diagnostics/Tuning Pack objects are refused unless the server is
        marked as licensed for that pack (using them records feature usage in the database)."""
        pack = licensed_pack_needed(sql)
        if pack and pack not in (self.spec.licensed_features or []):
            label = "Diagnostics Pack" if pack == "diagnostics_pack" else "Tuning Pack"
            raise AdapterError(
                f"this statement uses Oracle {label} features (AWR, ASH, ADDM, SQL Monitoring or the SQL "
                f"tuning advisors), which need a separate licence: mark the server as licensed for the "
                f"{label} under Server → Settings → Oracle management packs to run it"
            )
        if database:
            info = await self._server()
            if database not in (info["con_name"], text(info.get("db_name"))):
                raise AdapterError(
                    f"statements run in the connected container {info['con_name']}: register {database} "
                    "(its service name) as its own server"
                )
        return await super().execute_sql(sql, None, read_only=read_only, max_rows=max_rows, timeout_s=timeout_s)

    async def explain_query(self, sql: str, database: str | None = None, analyze: bool = False) -> ExplainResult:
        stmt = sql.strip().rstrip(";").strip()
        if not PLANNABLE.match(stmt):
            raise AdapterError("only SELECT, WITH, INSERT, UPDATE, DELETE and MERGE statements can be explained")
        info = await self._server()
        if database and database not in (info["con_name"], text(info.get("db_name"))):
            raise AdapterError(f"the plan is produced in the connected container {info['con_name']}")
        rows, elapsed, meta = await self._run(self._explain_sync, stmt, analyze)
        if not rows:
            raise AdapterError("Oracle returned no plan rows (V$SQL_PLAN_STATISTICS_ALL needs SELECT_CATALOG_ROLE)")
        root = build_tree(rows, actual=analyze)
        if root is None:
            raise AdapterError("the plan has no root operation")
        issues = analyze_plan(root, analyzed=analyze)
        notes = []
        if analyze:
            notes.append(
                "The statement was executed with STATISTICS_LEVEL=ALL"
                + (" in a read-only transaction." if READ_STMT.match(stmt) else "; its changes were rolled back.")
            )
        notes.append("Costs are optimizer estimates (cumulative per operation, as in DBMS_XPLAN).")
        return ExplainResult(
            engine=self.engine,
            mode="analyze" if analyze else "estimate",
            executed=analyze,
            root=root,
            issues=issues,
            total_cost=root.total_cost,
            execution_ms=round(elapsed, 2) if analyze else None,
            raw={"plan_rows": jsonable(rows), **jsonable(meta)},
            notes=notes,
        )

    # ---- administration -------------------------------------------------------------------------
    def build_admin(self, action: str, params: dict[str, Any]) -> Any:
        return ora_admin.build(action, params)
