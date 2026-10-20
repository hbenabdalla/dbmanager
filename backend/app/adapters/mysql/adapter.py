"""MySQL and MariaDB adapters.

``MySQLFamilyAdapter`` holds what is identical in both servers; ``MySQLAdapter`` and
``MariaDBAdapter`` implement the parts that differ (grant tables, role catalog, lock views,
deadlock counter, timeouts, explain format, configuration metadata, Galera, userstat).
"""

from __future__ import annotations

import json
import re
from typing import Any, ClassVar

from app.adapters.base import (
    FULL,
    NOT_SUPPORTED,
    PARTIAL,
    REQUIRES_CONFIGURATION,
    REQUIRES_PRIVILEGE,
    AdapterError,
    AdapterNotSupported,
    AdapterPermissionError,
    Capability,
    CapabilityMap,
    ConfigParameter,
    cap,
)
from app.adapters.dbapi import AdapterObjectMissing, jsonable, num, text
from app.adapters.mysql import admin as mysql_admin
from app.adapters.mysql.evidence import MySQLEvidenceMixin
from app.adapters.mysql.inventory import MySQLInventoryMixin
from app.adapters.mysql.monitoring import MySQLMonitoringMixin, _category
from app.adapters.mysql.plans import parse_json, parse_tree
from app.domain.plan_analysis import ExplainResult, analyze_plan

C = Capability

_COMMON: CapabilityMap = {
    C.CONNECTION: cap(FULL),
    C.SERVER_INFO: cap(FULL),
    C.DATABASES: cap(FULL),
    C.DATABASE_CREATE: cap(FULL, "guarded administration action / SQL workspace"),
    C.DATABASE_DROP: cap(FULL, "guarded administration action with typed confirmation"),
    C.SCHEMAS: cap(FULL, "a schema is a database in MySQL/MariaDB"),
    C.OBJECTS: cap(
        FULL,
        "tables, views, indexes, routines, triggers, constraints (routines/triggers are listed only with "
        "SHOW_ROUTINE / TRIGGER privileges)",
    ),
    C.OBJECT_DDL: cap(FULL, "SHOW CREATE … (routine bodies need SHOW_ROUTINE or definer rights)"),
    C.USERS: cap(FULL, "mysql.user (needs SELECT on the mysql schema; falls back to information_schema)"),
    C.ROLES: cap(FULL),
    C.PRIVILEGES: cap(FULL, "global, database, table, column grants and role memberships"),
    C.SESSIONS: cap(FULL, "information_schema.PROCESSLIST (PROCESS privilege shows every session)"),
    C.SESSION_CANCEL: cap(FULL, "KILL QUERY (CONNECTION_ADMIN or SUPER for other users' sessions)"),
    C.SESSION_TERMINATE: cap(FULL, "KILL CONNECTION"),
    C.LOCKS: cap(FULL),
    C.PERFORMANCE_METRICS: cap(FULL, "SHOW GLOBAL STATUS / VARIABLES"),
    C.CPU_METRICS: cap(NOT_SUPPORTED, "not exposed through SQL; use host or cloud metrics"),
    C.WAIT_EVENTS: cap(
        PARTIAL, "row-lock waits from status counters; per-event waits need performance_schema consumers"
    ),
    C.QUERY_STATISTICS: cap(FULL, "performance_schema statement digests"),
    C.QUERY_HISTORY: cap(PARTIAL, "derived from periodic digest snapshots in the time-series database"),
    C.EXPLAIN: cap(FULL),
    C.REPLICATION: cap(FULL, "SHOW REPLICA STATUS (REPLICATION CLIENT privilege)"),
    C.CONFIGURATION: cap(FULL),
    C.STORAGE: cap(FULL, "information_schema table statistics"),
    C.TABLESPACES: cap(PARTIAL, "InnoDB tablespaces (PROCESS privilege)"),
    C.SECURITY_SCAN: cap(FULL),
    C.LOGS: cap(NOT_SUPPORTED, "error log is not read by this adapter"),
    C.SQL_WORKSPACE: cap(FULL),
    C.READ_ONLY_TRANSACTIONS: cap(FULL, "START TRANSACTION READ ONLY"),
    C.ADMINISTRATION: cap(FULL, "create database/user/role, grant, revoke, create index (online)"),
    C.RECOMMENDATIONS: cap(FULL),
}


#: InnoDB lock views of MariaDB and MySQL 5.7 (MySQL 8.0 moved them to performance_schema)
INNODB_LOCK_WAITS_SQL = """
SELECT r.trx_mysql_thread_id AS waiting, b.trx_mysql_thread_id AS blocking,
       TIMESTAMPDIFF(SECOND, r.trx_wait_started, NOW()) AS wait_s, w.requested_lock_id AS lock_id
FROM information_schema.INNODB_LOCK_WAITS w
JOIN information_schema.INNODB_TRX r ON r.trx_id = w.requesting_trx_id
JOIN information_schema.INNODB_TRX b ON b.trx_id = w.blocking_trx_id
"""
INNODB_LOCKS_SQL = """
SELECT t.trx_mysql_thread_id AS session_id, l.lock_type, l.lock_mode,
       CASE WHEN w.requested_lock_id IS NULL THEN 'GRANTED' ELSE 'WAITING' END AS lock_status,
       SUBSTRING_INDEX(REPLACE(l.lock_table, '`', ''), '.', 1) AS object_schema,
       SUBSTRING_INDEX(REPLACE(l.lock_table, '`', ''), '.', -1) AS object_name, l.lock_index AS index_name
FROM information_schema.INNODB_LOCKS l
JOIN information_schema.INNODB_TRX t ON t.trx_id = l.lock_trx_id
LEFT JOIN information_schema.INNODB_LOCK_WAITS w ON w.requested_lock_id = l.lock_id
LIMIT 1000
"""


class MySQLFamilyAdapter(MySQLEvidenceMixin, MySQLMonitoringMixin, MySQLInventoryMixin):
    implemented = True

    async def discover_capabilities(self) -> CapabilityMap:
        caps = dict(self.capability_profile())
        info = await self.get_server_info()
        v = info.version_num or 0
        detected = info.extra.get("flavor_detected")
        if detected and detected != self.flavor:
            caps[C.CONNECTION] = cap(
                PARTIAL,
                f"the server reports itself as {detected}; register it with the {detected} engine for accurate SQL",
            )
        has_process = await self._try("SELECT COUNT(*) AS n FROM information_schema.INNODB_TRX") is not None
        if not has_process:
            note = "grant PROCESS to the monitoring account to see every session and InnoDB lock information"
            caps[C.SESSIONS] = cap(PARTIAL, note)
            caps[C.LOCKS] = cap(REQUIRES_PRIVILEGE, note)
            caps[C.TABLESPACES] = cap(REQUIRES_PRIVILEGE, note)
        if not info.extra.get("performance_schema"):
            note = "set performance_schema=ON in the server configuration (requires a restart)"
            caps[C.QUERY_STATISTICS] = cap(REQUIRES_CONFIGURATION, note)
            caps[C.QUERY_HISTORY] = cap(REQUIRES_CONFIGURATION, note)
            if self.flavor == "mysql":
                caps[C.LOCKS] = cap(PARTIAL, "row-lock details need performance_schema.data_locks")
        elif await self._try("SELECT 1 FROM performance_schema.events_statements_summary_by_digest LIMIT 1") is None:
            caps[C.QUERY_STATISTICS] = cap(
                REQUIRES_PRIVILEGE, "grant SELECT ON performance_schema.* to the monitoring account"
            )
            caps[C.QUERY_HISTORY] = cap(REQUIRES_PRIVILEGE, "grant SELECT ON performance_schema.*")
        if await self._account_rows() is None:
            caps[C.USERS] = cap(
                PARTIAL, "grant SELECT ON mysql.* for account attributes (lock state, plugin, password age)"
            )
            caps[C.ROLES] = cap(PARTIAL, "role catalog needs SELECT on the mysql schema")
            caps[C.SECURITY_SCAN] = cap(PARTIAL, "account checks need SELECT on mysql.user / mysql.global_priv")
        repl = await self._replica_rows()
        if repl is None:
            caps[C.REPLICATION] = cap(REQUIRES_PRIVILEGE, self._replica_privilege_note())
        elif not repl:
            caps[C.REPLICATION] = cap(FULL, "not configured: this server is not a replica")
        caps.update(self._version_caps(v))
        if info.cloud_provider:
            caps[C.CONFIGURATION] = cap(
                PARTIAL, f"{info.cloud_provider}: parameters are changed through the provider's parameter groups"
            )
            caps[C.SESSION_TERMINATE] = cap(
                PARTIAL, "managed service: KILL works for your own sessions; RDS uses CALL mysql.rds_kill"
            )
        return caps

    def _version_caps(self, v: int) -> CapabilityMap:
        return {}

    # ---- explain ------------------------------------------------------------------------------
    def _explain_sync(self, stmt: str, database: str | None, analyze: bool, fmt: str) -> list[dict[str, Any]]:
        conn = self._get_conn(database)
        restore = self._use_database(conn, database)
        cur = conn.cursor()
        try:
            if analyze:
                cur.execute("START TRANSACTION")
            cur.execute(f"{fmt} {stmt}")
            cols = [str(d[0]).lower() for d in cur.description or []]
            return [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]
        finally:
            try:
                conn.rollback()  # EXPLAIN ANALYZE side effects (if any) are discarded
            finally:
                cur.close()
                if restore is not None:
                    restore()

    def _explain_prefix(self, analyze: bool, version_num: int) -> tuple[str, str]:
        raise NotImplementedError

    async def explain_query(self, sql: str, database: str | None = None, analyze: bool = False) -> ExplainResult:
        stmt = sql.strip().rstrip(";")
        if re.match(r"^\s*(explain|analyze|describe|desc)\b", stmt, re.I):
            raise AdapterError("submit the statement without the EXPLAIN / ANALYZE keyword")
        v = await self._version()
        prefix, kind = self._explain_prefix(analyze, v)
        try:
            rows = await self._run(self._explain_sync, stmt, database, analyze, prefix)
        except AdapterObjectMissing as exc:
            raise AdapterError(str(exc)) from exc
        raw_text = text(next(iter(rows[0].values()))) if rows else ""
        notes: list[str] = []
        if kind == "tree":
            root = parse_tree(raw_text or "")
            raw: Any = raw_text
        else:
            raw = json.loads(raw_text or "{}")
            root = parse_json(raw)
        if root is None:
            raise AdapterError("the server returned an empty plan")
        if analyze:
            notes.append("The statement was executed inside a transaction that was rolled back.")
        issues = analyze_plan(root, analyzed=analyze)
        return ExplainResult(
            engine=self.engine,
            mode="analyze" if analyze else "estimate",
            executed=analyze,
            root=root,
            issues=issues,
            total_cost=root.total_cost,
            execution_ms=root.actual_total_ms if analyze else None,
            raw=raw,
            notes=notes,
        )

    # ---- administration -------------------------------------------------------------------------
    def build_admin(self, action: str, params: dict[str, Any]) -> Any:
        return mysql_admin.build(action, params, flavor=self.flavor)


class MySQLAdapter(MySQLFamilyAdapter):
    engine = "mysql"
    display_name = "MySQL"
    flavor = "mysql"
    implementation_phase = 3
    support_level = "supported"
    support_note = "MySQL 8.0, 8.4 LTS and 9.x (5.7 works with reduced explain/role support)"
    static_capabilities: ClassVar[CapabilityMap] = {
        **_COMMON,
        C.MEMORY_METRICS: cap(
            PARTIAL, "memory instrumented by performance_schema (memory_summary_global_by_event_name)"
        ),
        C.EXPLAIN_ANALYZE: cap(
            FULL, "EXPLAIN ANALYZE (8.0.18+) executes the statement inside a rolled-back transaction"
        ),
    }
    engine_capabilities: ClassVar[CapabilityMap] = static_capabilities

    def _session_setup(self, timeout_s: float) -> list[str]:
        # MAX_EXECUTION_TIME bounds read-only SELECTs (5.7.8+); writes rely on the socket timeout
        return [f"SET SESSION MAX_EXECUTION_TIME = {int(timeout_s * 1000)}"]

    async def _flavor_info(self) -> dict[str, Any]:
        r = await self._try_one("SELECT @@server_uuid AS server_uuid, @@super_read_only AS super_read_only")
        return (
            {"server_uuid": text(r["server_uuid"]), "super_read_only": bool(int(r["super_read_only"] or 0))}
            if r
            else {}
        )

    def _version_caps(self, v: int) -> CapabilityMap:
        out: CapabilityMap = {}
        if v < 80018:
            out[C.EXPLAIN_ANALYZE] = cap(NOT_SUPPORTED, "EXPLAIN ANALYZE requires MySQL 8.0.18 or later")
        if v < 80000:
            out[C.ROLES] = cap(NOT_SUPPORTED, "roles require MySQL 8.0")
            out[C.LOCKS] = cap(
                PARTIAL,
                "MySQL 5.7: InnoDB locks involved in waits (INFORMATION_SCHEMA.INNODB_LOCKS); no metadata locks",
            )
        return out

    async def _account_rows(self) -> list[dict[str, Any]] | None:
        rows = await self._try(
            """
            SELECT User AS user, Host AS host, account_locked, password_expired, plugin, authentication_string <> '' AS has_password,
                   password_last_changed, password_lifetime, max_user_connections, ssl_type,
                   Select_priv AS select_priv, Insert_priv AS insert_priv, Super_priv AS super_priv, Grant_priv AS grant_priv,
                   Create_user_priv AS create_user_priv, File_priv AS file_priv, Shutdown_priv AS shutdown_priv,
                   Reload_priv AS reload_priv, Process_priv AS process_priv, Repl_slave_priv AS repl_slave_priv
            FROM mysql.user
            """
        )
        if rows is None:
            return None
        edges = await self._role_edges() or []
        roles = {r for _, r in edges}
        out = []
        for r in rows:
            locked = (text(r["account_locked"]) or "N") == "Y"
            name = f"{text(r['user'])}@{text(r['host'])}"
            # MySQL roles are locked accounts without credentials (CREATE ROLE)
            is_role = name in roles or (locked and not r["has_password"] and text(r["host"]) == "%")
            out.append({**r, "locked": locked, "is_role": is_role})
        return out

    async def _role_edges(self) -> list[tuple[str, str]] | None:
        rows = await self._try("SELECT FROM_USER, FROM_HOST, TO_USER, TO_HOST FROM mysql.role_edges")
        if rows is None:
            return None
        return [
            (f"{text(r['to_user'])}@{text(r['to_host'])}", f"{text(r['from_user'])}@{text(r['from_host'])}")
            for r in rows
        ]

    async def _dynamic_grants(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for r in await self._try("SELECT USER, HOST, PRIV FROM mysql.global_grants") or []:
            out.setdefault(f"{text(r['user'])}@{text(r['host'])}", []).append(text(r["priv"]) or "")
        return out

    async def _lock_wait_pairs(self) -> list[dict[str, Any]] | None:
        if await self._version() < 80000:  # 5.7: the InnoDB lock views (performance_schema has none)
            return await self._try(INNODB_LOCK_WAITS_SQL)
        return await self._try(
            """
            SELECT r.trx_mysql_thread_id AS waiting, b.trx_mysql_thread_id AS blocking,
                   TIMESTAMPDIFF(SECOND, r.trx_wait_started, NOW()) AS wait_s,
                   CONCAT(w.REQUESTING_ENGINE_LOCK_ID) AS lock_id
            FROM performance_schema.data_lock_waits w
            JOIN information_schema.INNODB_TRX r ON r.trx_id = w.REQUESTING_ENGINE_TRANSACTION_ID
            JOIN information_schema.INNODB_TRX b ON b.trx_id = w.BLOCKING_ENGINE_TRANSACTION_ID
            """
        )

    async def _lock_rows(self) -> list[dict[str, Any]] | None:
        if await self._version() < 80000:
            return await self._try(INNODB_LOCKS_SQL)
        rows = await self._try(
            """
            SELECT t.PROCESSLIST_ID AS session_id, l.LOCK_TYPE AS lock_type, l.LOCK_MODE AS lock_mode, l.LOCK_STATUS AS lock_status,
                   l.OBJECT_SCHEMA AS object_schema, l.OBJECT_NAME AS object_name, l.INDEX_NAME AS index_name
            FROM performance_schema.data_locks l
            LEFT JOIN performance_schema.threads t ON t.THREAD_ID = l.THREAD_ID
            WHERE t.PROCESSLIST_ID IS NULL OR t.PROCESSLIST_ID <> CONNECTION_ID()
            LIMIT 1000
            """
        )
        if rows is None:
            return None
        mdl = await self._try(
            """
            SELECT t.PROCESSLIST_ID AS session_id, CONCAT('METADATA ', m.OBJECT_TYPE) AS lock_type, m.LOCK_TYPE AS lock_mode,
                   m.LOCK_STATUS AS lock_status, m.OBJECT_SCHEMA AS object_schema, m.OBJECT_NAME AS object_name, NULL AS index_name
            FROM performance_schema.metadata_locks m
            JOIN performance_schema.threads t ON t.THREAD_ID = m.OWNER_THREAD_ID
            WHERE m.OBJECT_TYPE IN ('TABLE', 'SCHEMA') AND m.OBJECT_SCHEMA NOT IN ('performance_schema', 'mysql', 'sys')
              AND t.PROCESSLIST_ID <> CONNECTION_ID()
            LIMIT 500
            """
        )
        return rows + (mdl or [])

    async def _deadlock_counter(self, status: dict[str, Any]) -> float | None:
        r = await self._try_one(
            "SELECT `COUNT` AS n FROM information_schema.INNODB_METRICS WHERE NAME = 'lock_deadlocks'"
        )
        return num(r["n"]) if r else None

    def _digest_extra_columns(self, version_num: int) -> str:
        if version_num >= 80028:
            return ", SUM_CPU_TIME / 1000000000 AS cpu_ms, QUANTILE_95 / 1000000000 AS p95_ms"
        return ""

    async def _instrumented_memory(self) -> float | None:
        r = await self._try_one(
            "SELECT SUM(CURRENT_NUMBER_OF_BYTES_USED) AS b FROM performance_schema.memory_summary_global_by_event_name"
        )
        return num(r["b"]) if r and r["b"] is not None else None

    async def _configuration_rows(self) -> list[ConfigParameter] | None:
        rows = await self._try(
            """
            SELECT g.VARIABLE_NAME AS name, g.VARIABLE_VALUE AS value, i.VARIABLE_SOURCE AS source,
                   i.MIN_VALUE AS min_value, i.MAX_VALUE AS max_value, i.SET_TIME AS set_time
            FROM performance_schema.global_variables g
            LEFT JOIN performance_schema.variables_info i ON i.VARIABLE_NAME = g.VARIABLE_NAME
            ORDER BY g.VARIABLE_NAME
            """
        )
        if rows is None:
            return None
        return [
            ConfigParameter(
                name=text(r["name"]) or "",
                value=text(r["value"]),
                category=_category(r["name"]),
                source=text(r["source"]),
                is_default=(text(r["source"]) or "") == "COMPILED",
                description=f"range {text(r['min_value'])} … {text(r['max_value'])}"
                if r["max_value"] not in (None, "0")
                else None,
            )
            for r in rows
        ]

    async def _tablespaces(self) -> list[dict[str, Any]] | None:
        rows = await self._try(
            """
            SELECT NAME AS name, SPACE_TYPE AS type, FILE_SIZE AS file_bytes, ALLOCATED_SIZE AS allocated_bytes,
                   ROW_FORMAT AS row_format
            FROM information_schema.INNODB_TABLESPACES
            ORDER BY FILE_SIZE DESC
            LIMIT 100
            """
        )
        return [jsonable(dict(r)) for r in rows] if rows is not None else None

    async def _password_validation(self) -> bool | None:
        comp = await self._try("SELECT component_urn FROM mysql.component")
        variables = await self._try("SHOW GLOBAL VARIABLES LIKE 'validate_password%%'")
        if comp is None and variables is None:
            return None
        return bool(variables) or any("validate_password" in (text(r["component_urn"]) or "") for r in comp or [])

    async def _audit_plugin_active(self, plugins: dict[str, str]) -> bool:
        if plugins.get("audit_log") == "ACTIVE":
            return True
        comp = await self._try("SELECT component_urn FROM mysql.component")
        return any("audit" in (text(r["component_urn"]) or "") for r in comp or [])

    async def _redundant_indexes(self) -> list[dict[str, Any]] | None:
        rows = await self._try(
            """
            SELECT table_schema AS db, table_name, redundant_index_name AS redundant_index,
                   redundant_index_columns AS redundant_columns, dominant_index_name AS dominant_index,
                   dominant_index_columns AS dominant_columns
            FROM sys.schema_redundant_indexes
            LIMIT 50
            """
        )
        return [jsonable(dict(r)) for r in rows] if rows is not None else None

    async def _kill_statement(self, session_id: int, terminate: bool) -> str:
        if (await self._cloud_provider() or "").startswith("aws"):
            return f"CALL mysql.{'rds_kill' if terminate else 'rds_kill_query'}({session_id})"
        return await super()._kill_statement(session_id, terminate)

    def _explain_prefix(self, analyze: bool, version_num: int) -> tuple[str, str]:
        if analyze:
            if version_num < 80018:
                raise AdapterNotSupported(C.EXPLAIN_ANALYZE, "EXPLAIN ANALYZE requires MySQL 8.0.18 or later")
            return "EXPLAIN ANALYZE", "tree"
        if version_num >= 80016:
            return "EXPLAIN FORMAT=TREE", "tree"
        return "EXPLAIN FORMAT=JSON", "json"


class MariaDBAdapter(MySQLFamilyAdapter):
    engine = "mariadb"
    display_name = "MariaDB"
    flavor = "mariadb"
    implementation_phase = 3
    support_level = "supported"
    support_note = "MariaDB 10.5+ (10.6, 10.11 and 11.x LTS); Galera status when wsrep is enabled"
    static_capabilities: ClassVar[CapabilityMap] = {
        **_COMMON,
        C.QUERY_STATISTICS: cap(FULL, "performance_schema digests (performance_schema is OFF by default in MariaDB)"),
        C.LOCKS: cap(
            PARTIAL, "InnoDB locks involved in waits (INFORMATION_SCHEMA.INNODB_LOCKS); held-only locks are not listed"
        ),
        C.MEMORY_METRICS: cap(NOT_SUPPORTED, "MariaDB memory instrumentation is disabled by default"),
        C.EXPLAIN_ANALYZE: cap(FULL, "ANALYZE FORMAT=JSON executes the statement inside a rolled-back transaction"),
        C.REPLICATION: cap(FULL, "SHOW REPLICA STATUS / Galera wsrep status"),
    }
    engine_capabilities: ClassVar[CapabilityMap] = static_capabilities

    def _session_setup(self, timeout_s: float) -> list[str]:
        # max_statement_time (seconds, 10.1+) bounds every statement type, not only SELECT
        return [f"SET SESSION max_statement_time = {max(int(timeout_s), 1)}"]

    def _has_show_replica(self, version_num: int) -> bool:
        return version_num >= 100501

    def _replica_hosts_sql(self, version_num: int) -> str:
        return "SHOW REPLICA HOSTS" if version_num >= 100501 else "SHOW SLAVE HOSTS"

    def _replica_privilege_note(self) -> str:
        return "replica status needs the REPLICA MONITOR privilege (MariaDB 10.5.9+; REPLICATION CLIENT before)"

    def _version_caps(self, v: int) -> CapabilityMap:
        return {}

    async def _account_rows(self) -> list[dict[str, Any]] | None:
        rows = await self._try(
            """
            SELECT u.User AS user, u.Host AS host, u.is_role, u.password_expired, u.plugin,
                   (u.authentication_string <> '' OR u.Password <> '') AS has_password, u.max_user_connections, u.ssl_type,
                   u.Select_priv AS select_priv, u.Insert_priv AS insert_priv, u.Super_priv AS super_priv, u.Grant_priv AS grant_priv,
                   u.Create_user_priv AS create_user_priv, u.File_priv AS file_priv, u.Shutdown_priv AS shutdown_priv,
                   u.Reload_priv AS reload_priv, u.Process_priv AS process_priv, u.Repl_slave_priv AS repl_slave_priv,
                   JSON_VALUE(g.Priv, '$.account_locked') AS account_locked,
                   FROM_UNIXTIME(JSON_VALUE(g.Priv, '$.password_last_changed')) AS password_last_changed,
                   JSON_VALUE(g.Priv, '$.password_lifetime') AS password_lifetime
            FROM mysql.user u
            LEFT JOIN mysql.global_priv g ON g.User = u.User AND g.Host = u.Host
            """
        )
        if rows is None:
            return None
        out = []
        for r in rows:
            lifetime = num(r.get("password_lifetime"))
            out.append(
                {
                    **r,
                    "is_role": (text(r["is_role"]) or "N") == "Y",
                    "locked": (text(r.get("account_locked")) or "").lower() in ("true", "1"),
                    "password_lifetime": int(lifetime) if lifetime and lifetime > 0 else None,
                }
            )
        return out

    async def _role_edges(self) -> list[tuple[str, str]] | None:
        rows = await self._try("SELECT Host AS host, User AS user, Role AS role FROM mysql.roles_mapping")
        if rows is None:
            return None
        out = []
        for r in rows:
            user, host = text(r["user"]) or "", text(r["host"]) or ""
            out.append((f"{user}@{host}" if host else user, text(r["role"]) or ""))
        return out

    async def _lock_wait_pairs(self) -> list[dict[str, Any]] | None:
        return await self._try(INNODB_LOCK_WAITS_SQL)

    async def _lock_rows(self) -> list[dict[str, Any]] | None:
        return await self._try(INNODB_LOCKS_SQL)

    async def _deadlock_counter(self, status: dict[str, Any]) -> float | None:
        return num(status.get("innodb_deadlocks"))

    async def _configuration_rows(self) -> list[ConfigParameter] | None:
        rows = await self._try(
            """
            SELECT VARIABLE_NAME AS name, GLOBAL_VALUE AS value, DEFAULT_VALUE AS default_value, VARIABLE_COMMENT AS description,
                   READ_ONLY AS read_only, GLOBAL_VALUE_ORIGIN AS origin, VARIABLE_SCOPE AS scope
            FROM information_schema.SYSTEM_VARIABLES
            WHERE VARIABLE_SCOPE <> 'SESSION ONLY'
            ORDER BY VARIABLE_NAME
            """
        )
        if rows is None:
            return None
        return [
            ConfigParameter(
                name=(text(r["name"]) or "").lower(),
                value=text(r["value"]),
                category=_category((text(r["name"]) or "").lower()),
                description=text(r["description"]),
                source=text(r["origin"]),
                default_value=text(r["default_value"]),
                requires_restart=(text(r["read_only"]) or "NO") == "YES",
                is_default=text(r["value"]) == text(r["default_value"]),
            )
            for r in rows
        ]

    async def _tablespaces(self) -> list[dict[str, Any]] | None:
        rows = await self._try(
            """
            SELECT NAME AS name, FILE_SIZE AS file_bytes, ALLOCATED_SIZE AS allocated_bytes, ROW_FORMAT AS row_format
            FROM information_schema.INNODB_SYS_TABLESPACES
            ORDER BY FILE_SIZE DESC
            LIMIT 100
            """
        )
        return [jsonable(dict(r)) for r in rows] if rows is not None else None

    async def _password_validation(self) -> bool | None:
        rows = await self._try(
            "SELECT PLUGIN_NAME FROM information_schema.PLUGINS WHERE PLUGIN_TYPE = 'PASSWORD VALIDATION' AND PLUGIN_STATUS = 'ACTIVE'"
        )
        return bool(rows) if rows is not None else None

    async def _audit_plugin_active(self, plugins: dict[str, str]) -> bool:
        if plugins.get("SERVER_AUDIT") != "ACTIVE":
            return False
        r = await self._variables(["server_audit_logging"])
        return (text(r.get("server_audit_logging")) or "OFF").upper() == "ON"

    async def get_tuning_context(self) -> dict[str, Any]:
        ctx = await super().get_tuning_context()
        st = await self._variables(["userstat"])
        if (text(st.get("userstat")) or "OFF").upper() in ("ON", "1") and not ctx.get("unused_indexes"):
            # MariaDB user statistics: indexes never read since the plugin was enabled
            rows = await self._try(
                """
                SELECT s.TABLE_SCHEMA AS db, s.TABLE_NAME AS table_name, s.INDEX_NAME AS index_name
                FROM information_schema.STATISTICS s
                LEFT JOIN information_schema.INDEX_STATISTICS i
                  ON i.TABLE_SCHEMA = s.TABLE_SCHEMA AND i.TABLE_NAME = s.TABLE_NAME AND i.INDEX_NAME = s.INDEX_NAME
                WHERE s.INDEX_NAME <> 'PRIMARY' AND i.INDEX_NAME IS NULL AND s.SEQ_IN_INDEX = 1
                  AND s.TABLE_SCHEMA NOT IN ('mysql', 'performance_schema', 'sys', 'information_schema')
                LIMIT 200
                """
            )
            ctx["unused_indexes"] = [jsonable(dict(r)) for r in rows or []]
            ctx["unused_index_source"] = "userstat (INDEX_STATISTICS)"
        st2 = await self._status()
        if num(st2.get("wsrep_cluster_size")) is not None:
            ctx["galera"] = {
                "cluster_size": num(st2.get("wsrep_cluster_size")),
                "local_state": text(st2.get("wsrep_local_state_comment")),
                "flow_control_paused": num(st2.get("wsrep_flow_control_paused")),
                "cert_failures": num(st2.get("wsrep_local_cert_failures")),
            }
        return ctx

    def _explain_prefix(self, analyze: bool, version_num: int) -> tuple[str, str]:
        return ("ANALYZE FORMAT=JSON", "json") if analyze else ("EXPLAIN FORMAT=JSON", "json")

    async def get_query_statistics(self, limit: int = 100) -> list[Any]:
        try:
            return await super().get_query_statistics(limit)
        except AdapterPermissionError as exc:  # pragma: no cover - mapped by the base
            raise AdapterNotSupported(C.QUERY_STATISTICS, str(exc)) from exc
