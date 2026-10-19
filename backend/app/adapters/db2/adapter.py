"""IBM Db2 for Linux, UNIX and Windows adapter (Phase 6): Db2 11.1, 11.5 and 12.1, including
pureScale members, HADR and Db2 on cloud services (IBM Db2 on Cloud, Amazon RDS for Db2)."""

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
    REQUIRES_CONFIGURATION,
    REQUIRES_PRIVILEGE,
    AdapterError,
    Capability,
    CapabilityMap,
    cap,
)
from app.adapters.db2 import admin as db2_admin
from app.adapters.db2 import sql as Q
from app.adapters.db2.base import qid
from app.adapters.db2.evidence import Db2EvidenceMixin
from app.adapters.db2.inventory import Db2InventoryMixin
from app.adapters.db2.monitoring import Db2MonitoringMixin
from app.adapters.db2.plans import build_tree
from app.adapters.dbapi import jsonable, text
from app.domain.plan_analysis import ExplainResult, analyze_plan

C = Capability
PLANNABLE = re.compile(r"^\s*(SELECT|WITH|VALUES|INSERT|UPDATE|DELETE|MERGE)\b", re.I)
INSTANCE_ADMIN = {"SYSADM", "SYSCTRL", "SYSMAINT"}


class Db2Adapter(Db2EvidenceMixin, Db2MonitoringMixin, Db2InventoryMixin):
    implementation_phase = 6
    implemented = True
    support_level = "supported"
    support_note = (
        "Db2 LUW 11.1–12.1 through ibm_db (CLI driver bundled). Monitoring via MON_GET_* functions; "
        "EXPLAIN needs explain tables; REORG/RUNSTATS are recommended, never executed."
    )
    static_capabilities: ClassVar[CapabilityMap] = {
        C.CONNECTION: cap(
            FULL, "DRDA over TCP/IP, optional TLS (SECURITY=SSL); external (OS/LDAP/Kerberos) authentication"
        ),
        C.SERVER_INFO: cap(FULL, "SYSIBMADM.ENV_INST_INFO / ENV_PROD_INFO / ENV_SYS_INFO"),
        C.DATABASES: cap(PARTIAL, "one database per connection (register each database separately)"),
        C.DATABASE_CREATE: cap(NOT_SUPPORTED, "CREATE DATABASE is a CLP/instance command, not SQL"),
        C.DATABASE_DROP: cap(NOT_SUPPORTED, "DROP DATABASE is a CLP/instance command, not SQL"),
        C.SCHEMAS: cap(FULL, "SYSCAT.SCHEMATA"),
        C.OBJECTS: cap(FULL, "tables, views, MQTs, aliases, nicknames, indexes, routines, triggers, sequences"),
        C.OBJECT_DDL: cap(
            PARTIAL, "view/routine/trigger text from SYSCAT; table DDL reconstructed (db2look for full DDL)"
        ),
        C.USERS: cap(PARTIAL, "authorization IDs from the grant catalog; authentication is external"),
        C.ROLES: cap(FULL, "SYSCAT.ROLES / ROLEAUTH"),
        C.PRIVILEGES: cap(
            FULL, "database authorities (DBADM, SECADM, DATAACCESS, ACCESSCTRL, ...), schema and table privileges"
        ),
        C.SESSIONS: cap(FULL, "MON_GET_CONNECTION, MON_CURRENT_SQL, MON_GET_UNIT_OF_WORK, MON_LOCKWAITS"),
        C.SESSION_CANCEL: cap(FULL, "WLM_CANCEL_ACTIVITY (Db2 11.1+, WLMADM or DBADM)"),
        C.SESSION_TERMINATE: cap(FULL, "ADMIN_CMD('FORCE APPLICATION') — SYSADM, SYSCTRL or SYSMAINT"),
        C.LOCKS: cap(FULL, "MON_GET_LOCKS with lock waits from MON_LOCKWAITS"),
        C.PERFORMANCE_METRICS: cap(
            FULL, "MON_GET_WORKLOAD request metrics, MON_GET_BUFFERPOOL, MON_GET_TRANSACTION_LOG"
        ),
        C.CPU_METRICS: cap(FULL, "SYSPROC.ENV_GET_SYSTEM_RESOURCES (host CPU)"),
        C.MEMORY_METRICS: cap(FULL, "MON_GET_MEMORY_SET"),
        C.WAIT_EVENTS: cap(PARTIAL, "lock waits and activity states; no time-spent breakdown is collected"),
        C.QUERY_STATISTICS: cap(FULL, "MON_GET_PKG_CACHE_STMT (needs MON_ACT_METRICS ≠ NONE)"),
        C.QUERY_HISTORY: cap(PARTIAL, "time-series history of package-cache snapshots"),
        C.EXPLAIN: cap(FULL, "EXPLAIN PLAN into the explain tables (rolled back afterwards)"),
        C.EXPLAIN_ANALYZE: cap(
            NOT_SUPPORTED, "section actuals need an activity event monitor (not created by DBManager)"
        ),
        C.REPLICATION: cap(PARTIAL, "HADR via MON_GET_HADR; Q Replication / CDC not monitored"),
        C.CONFIGURATION: cap(FULL, "SYSIBMADM.DBCFG and DBMCFG (deferred values flagged)"),
        C.STORAGE: cap(FULL, "MON_GET_TABLESPACE and transaction log usage"),
        C.TABLESPACES: cap(FULL, "MON_GET_TABLESPACE"),
        C.SECURITY_SCAN: cap(FULL),
        C.LOGS: cap(NOT_IMPLEMENTED, "db2diag.log (PD_GET_DIAG_HIST) is not read by this adapter"),
        C.SQL_WORKSPACE: cap(FULL),
        C.READ_ONLY_TRANSACTIONS: cap(PARTIAL, "no read-only transaction mode: classifier + privileges + rollback"),
        C.ADMINISTRATION: cap(
            PARTIAL, "create schema/role, grant, revoke, create index; users and databases are external"
        ),
        C.RECOMMENDATIONS: cap(FULL, "REORG/RUNSTATS recommendations only — never executed"),
    }
    engine_capabilities: ClassVar[CapabilityMap] = static_capabilities

    async def _explain_schema(self) -> str | None:
        async def load() -> str | None:
            rows = await self._try(Q.EXPLAIN_TABLES) or []
            schemas = [text(r["tabschema"]) for r in rows]
            me = text((await self._server()).get("cur_user"))
            if me in schemas:
                return me
            return "SYSTOOLS" if "SYSTOOLS" in schemas else None

        return await self._memoized("explain_schema", load)

    async def discover_capabilities(self) -> CapabilityMap:
        caps = dict(self.capability_profile())
        info = await self._server()
        if not info["mon_routines"]:
            note = "grant EXECUTE on the MON_GET_* routines (or SQLADM) to the monitoring user"
            for c in (
                C.SESSIONS,
                C.LOCKS,
                C.PERFORMANCE_METRICS,
                C.MEMORY_METRICS,
                C.WAIT_EVENTS,
                C.QUERY_STATISTICS,
                C.QUERY_HISTORY,
                C.REPLICATION,
                C.STORAGE,
                C.TABLESPACES,
            ):
                caps[c] = cap(REQUIRES_PRIVILEGE, note)
        auths = set(info["authorities"])
        if not auths & INSTANCE_ADMIN:
            caps[C.SESSION_TERMINATE] = cap(REQUIRES_PRIVILEGE, "FORCE APPLICATION needs SYSADM, SYSCTRL or SYSMAINT")
        if not auths & {"WLMADM", "DBADM"}:
            caps[C.SESSION_CANCEL] = cap(REQUIRES_PRIVILEGE, "WLM_CANCEL_ACTIVITY needs WLMADM or DBADM")
        if int(info["major"] or 0) < 11:
            caps[C.SESSION_CANCEL] = cap(NOT_SUPPORTED, "MON_GET_ACTIVITY needs Db2 11.1 or later")
        if await self._explain_schema() is None:
            caps[C.EXPLAIN] = cap(
                REQUIRES_CONFIGURATION,
                "create the explain tables: CALL SYSPROC.SYSINSTALLOBJECTS('EXPLAIN', 'C', CAST(NULL AS VARCHAR(128)), CURRENT USER)",
            )
        cfg = {
            text(r["name"]): text(r["value"])
            for r in await self._try(Q.DBCFG_VALUES.format(names="'mon_act_metrics'")) or []
        }
        if (cfg.get("mon_act_metrics") or "").upper() == "NONE":
            caps[C.QUERY_STATISTICS] = cap(
                REQUIRES_CONFIGURATION, "set MON_ACT_METRICS to BASE (UPDATE DB CFG ... USING MON_ACT_METRICS BASE)"
            )
        if await self._try_one(Q.DBMCFG_VALUES.format(names="'svcename'")) is None:
            caps[C.CONFIGURATION] = cap(
                PARTIAL, "SYSIBMADM.DBMCFG is not readable (grant SELECT and EXECUTE on SYSPROC.DBM_GET_CFG)"
            )
        if info["mon_routines"]:
            hadr = await self._try(Q.HADR)
            if hadr == []:
                caps[C.REPLICATION] = cap(FULL, "not configured: HADR is not enabled for this database")
        if await self._try_one(Q.SYSTEM_RESOURCES) is None:
            caps[C.CPU_METRICS] = cap(REQUIRES_PRIVILEGE, "grant EXECUTE on SYSPROC.ENV_GET_SYSTEM_RESOURCES")
        return caps

    # ---- explain ---------------------------------------------------------------------------------
    def _explain_sync(self, stmt: str, schema: str) -> tuple[dict[str, Any], float]:
        conn = self._get_conn(None)
        cur = conn.cursor()
        tag = "DBM" + uuid.uuid4().hex[:16].upper()
        start = time.perf_counter()

        def rows() -> list[dict[str, Any]]:
            cols = [str(d[0]).lower() for d in cur.description or []]
            return [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]

        try:
            cur.execute(f"EXPLAIN PLAN SET QUERYTAG = '{tag}' FOR {stmt}")
            cur.execute(Q.EXPLAIN_STMT.format(schema=qid(schema)), (tag,))
            head = rows()
            if not head:
                return {}, (time.perf_counter() - start) * 1000
            h = head[0]
            keys = (
                h["explain_requester"],
                h["explain_time"],
                h["source_name"],
                h["source_schema"],
                h["source_version"],
                h["stmtno"],
                h["sectno"],
            )
            cur.execute(Q.EXPLAIN_OPERATORS.format(schema=qid(schema), keys=Q.EXPLAIN_KEYS), keys)
            ops = rows()
            cur.execute(Q.EXPLAIN_STREAMS.format(schema=qid(schema), keys=Q.EXPLAIN_KEYS), keys)
            streams = rows()
            cur.execute(Q.EXPLAIN_PREDICATES.format(schema=qid(schema), keys=Q.EXPLAIN_KEYS), keys)
            preds = rows()
            return {"statement": h, "operators": ops, "streams": streams, "predicates": preds}, (
                time.perf_counter() - start
            ) * 1000
        finally:
            try:
                conn.rollback()  # explain rows are not kept
            finally:
                cur.close()

    async def explain_query(self, sql: str, database: str | None = None, analyze: bool = False) -> ExplainResult:
        if analyze:
            raise AdapterError(
                "Db2 does not provide actual row counts without an activity event monitor; use the estimated plan"
            )
        stmt = sql.strip().rstrip(";").strip()
        if not PLANNABLE.match(stmt):
            raise AdapterError("only SELECT, WITH, VALUES, INSERT, UPDATE, DELETE and MERGE can be explained")
        schema = await self._explain_schema()
        if schema is None:
            raise AdapterError(
                "explain tables are missing: CALL SYSPROC.SYSINSTALLOBJECTS('EXPLAIN', 'C', CAST(NULL AS VARCHAR(128)), CURRENT USER)"
            )
        data, elapsed = await self._run(self._explain_sync, stmt, schema)
        if not data:
            raise AdapterError("no explain rows were produced for the statement")
        root = build_tree(data["operators"], data["streams"], data["predicates"])
        if root is None:
            raise AdapterError("the plan contains no operators")
        return ExplainResult(
            engine=self.engine,
            mode="estimate",
            executed=False,
            root=root,
            issues=analyze_plan(root, analyzed=False),
            total_cost=float(data["statement"].get("total_cost") or 0) or root.total_cost,
            planning_ms=round(elapsed, 2),
            raw=jsonable({k: v for k, v in data.items() if k != "statement"} | {"explain_schema": schema}),
            notes=["Costs are in timerons (optimizer units). Explain rows were rolled back."],
        )

    # ---- administration -----------------------------------------------------------------------------
    def build_admin(self, action: str, params: dict[str, Any]) -> Any:
        return db2_admin.build(action, params)
