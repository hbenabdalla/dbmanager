"""PostgreSQL reference adapter (asyncpg).

Design notes
------------
* Connections are opened lazily, one per database touched, and closed with the adapter.
* Every connection sets ``statement_timeout``, ``lock_timeout`` and an
  ``application_name`` so DBAs can identify platform traffic on the target.
* Read paths never modify the target. Workspace read-only statements run inside
  ``BEGIN READ ONLY``; EXPLAIN ANALYZE always runs inside a transaction that is rolled back.
* Optional facilities (pg_stat_statements, superuser-only catalogs) are probed at runtime
  and reported through capability discovery rather than assumed.
"""

from __future__ import annotations

import asyncio
import json
import re
import ssl
import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import asyncpg

from app.adapters.base import (
    FULL,
    NOT_SUPPORTED,
    PARTIAL,
    REQUIRES_EXTENSION,
    REQUIRES_PRIVILEGE,
    AdapterAuthenticationError,
    AdapterConnectionError,
    AdapterDatabaseNotFound,
    AdapterError,
    AdapterNotSupported,
    AdapterPermissionError,
    AdapterTimeout,
    AdapterTLSError,
    BaseDatabaseAdapter,
    Capability,
    CapabilityMap,
    ColumnInfo,
    ConfigParameter,
    DatabaseInfo,
    DbUser,
    ExecutionResult,
    LockInfo,
    MetricSnapshot,
    ObjectDetail,
    ObjectInfo,
    Privilege,
    QueryStat,
    ReplicationInfo,
    SchemaInfo,
    ServerInfo,
    SessionInfo,
    StorageInfo,
    cap,
)
from app.adapters.postgres import sql as Q
from app.domain.plan_analysis import ExplainResult, PlanNode, analyze_plan

SYSTEM_DATABASES = {"template0", "template1"}
POWERFUL_BUILTIN_ROLES = {
    "pg_read_server_files": "can read any server file",
    "pg_write_server_files": "can write any server file",
    "pg_execute_server_program": "can execute OS programs",
    "pg_signal_backend": "can cancel/terminate sessions",
    "pg_read_all_data": "can read all data",
    "pg_write_all_data": "can write all data",
    "rds_superuser": "AWS RDS administrative role",
    "azure_pg_admin": "Azure administrative role",
    "cloudsqlsuperuser": "Cloud SQL administrative role",
}
NO_TXN_STATEMENTS = re.compile(
    r"^\s*(VACUUM|CREATE\s+DATABASE|DROP\s+DATABASE|ALTER\s+SYSTEM|REINDEX\s+.*CONCURRENTLY|"
    r"CREATE\s+(UNIQUE\s+)?INDEX\s+CONCURRENTLY|DROP\s+INDEX\s+CONCURRENTLY|CREATE\s+TABLESPACE|DROP\s+TABLESPACE)",
    re.I | re.S,
)


def _ssl_arg(mode: str, ca_pem: str | None) -> Any:
    mode = (mode or "prefer").lower()
    if mode in ("disable", "allow", "prefer", "require") and not ca_pem:
        return mode
    if mode == "disable":
        return False
    ctx = ssl.create_default_context(cadata=ca_pem) if ca_pem else ssl.create_default_context()
    if mode in ("require", "prefer"):
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE if not ca_pem else ssl.CERT_REQUIRED
    elif mode == "verify-ca":
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_REQUIRED
    else:  # verify-full
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx


def _jsonable(v: Any) -> Any:
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray, memoryview)):
        return "\\x" + bytes(v).hex()[:2048]
    if isinstance(v, (UUID,)):
        return str(v)
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    return str(v)


def _to_bytes(setting: str | None, unit: str | None) -> float | None:
    if setting is None:
        return None
    try:
        val = float(setting)
    except ValueError:
        return None
    mult = {"B": 1, "kB": 1024, "8kB": 8192, "16kB": 16384, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
    return val * mult.get(unit or "B", 1)


class PostgreSQLAdapter(BaseDatabaseAdapter):
    engine = "postgresql"
    display_name = "PostgreSQL"
    default_port = 5432
    driver = "asyncpg"
    driver_key = "asyncpg"
    implementation_phase = 2
    implemented = True
    support_level = "supported"
    support_note = "PostgreSQL 12–17 (reference adapter); Aurora, RDS, Azure and Cloud SQL detected automatically"
    static_capabilities: CapabilityMap = {
        Capability.CONNECTION: cap(FULL),
        Capability.SERVER_INFO: cap(FULL),
        Capability.DATABASES: cap(FULL),
        Capability.DATABASE_CREATE: cap(FULL, "via SQL workspace with typed confirmation"),
        Capability.DATABASE_DROP: cap(FULL, "via SQL workspace with typed confirmation"),
        Capability.SCHEMAS: cap(FULL),
        Capability.OBJECTS: cap(FULL),
        Capability.OBJECT_DDL: cap(PARTIAL, "table DDL is reconstructed from the catalog"),
        Capability.USERS: cap(FULL),
        Capability.ROLES: cap(FULL),
        Capability.PRIVILEGES: cap(FULL),
        Capability.SESSIONS: cap(FULL),
        Capability.SESSION_CANCEL: cap(FULL),
        Capability.SESSION_TERMINATE: cap(FULL),
        Capability.LOCKS: cap(FULL),
        Capability.PERFORMANCE_METRICS: cap(FULL),
        Capability.CPU_METRICS: cap(
            NOT_SUPPORTED, "PostgreSQL does not expose host CPU through SQL; use node/cloud metrics"
        ),
        Capability.MEMORY_METRICS: cap(NOT_SUPPORTED, "PostgreSQL does not expose host memory through SQL"),
        Capability.WAIT_EVENTS: cap(FULL, "sampled from pg_stat_activity"),
        Capability.QUERY_STATISTICS: cap(REQUIRES_EXTENSION, "requires pg_stat_statements"),
        Capability.QUERY_HISTORY: cap(PARTIAL, "derived from periodic pg_stat_statements snapshots"),
        Capability.EXPLAIN: cap(FULL),
        Capability.EXPLAIN_ANALYZE: cap(FULL, "executes the statement inside a rolled-back transaction"),
        Capability.REPLICATION: cap(FULL),
        Capability.CONFIGURATION: cap(FULL),
        Capability.STORAGE: cap(FULL),
        Capability.TABLESPACES: cap(FULL),
        Capability.SECURITY_SCAN: cap(FULL),
        Capability.LOGS: cap(NOT_SUPPORTED, "server log files require file-system access; planned via log shipping"),
        Capability.SQL_WORKSPACE: cap(FULL),
        Capability.READ_ONLY_TRANSACTIONS: cap(FULL),
        Capability.ADMINISTRATION: cap(FULL, "create database/schema/role, grant, revoke, CREATE INDEX CONCURRENTLY"),
        Capability.RECOMMENDATIONS: cap(FULL),
    }

    def __init__(self, spec):  # type: ignore[no-untyped-def]
        super().__init__(spec)
        self._conns: dict[str, asyncpg.Connection] = {}
        self._version_num: int | None = None
        self._pgss: bool | None = None

    # ------------------------------------------------------------------------------------
    # connection management
    # ------------------------------------------------------------------------------------
    @property
    def _default_db(self) -> str:
        return self.spec.database or "postgres"

    async def _conn(self, database: str | None = None) -> asyncpg.Connection:
        db = database or self._default_db
        conn = self._conns.get(db)
        if conn is not None and not conn.is_closed():
            return conn
        stmt_ms = int(self.spec.statement_timeout * 1000)
        try:
            conn = await asyncpg.connect(
                host=self.spec.host,
                port=self.spec.port,
                user=self.spec.username,
                password=self.spec.password,
                database=db,
                ssl=_ssl_arg(self.spec.tls_mode, self.spec.tls_ca_pem),
                timeout=self.spec.connect_timeout,
                command_timeout=self.spec.statement_timeout + 5,
                statement_cache_size=0,  # compatible with PgBouncer transaction pooling
                server_settings={
                    "application_name": self.spec.application_name,
                    "statement_timeout": str(stmt_ms),
                    "lock_timeout": "5000",
                    "idle_in_transaction_session_timeout": str(max(stmt_ms * 2, 60000)),
                },
            )
        except (asyncpg.InvalidPasswordError, asyncpg.InvalidAuthorizationSpecificationError) as exc:
            raise AdapterAuthenticationError(f"authentication failed for user {self.spec.username!r}") from exc
        except asyncpg.InvalidCatalogNameError as exc:
            raise AdapterDatabaseNotFound(f"database {db!r} does not exist") from exc
        except TimeoutError as exc:
            raise AdapterConnectionError(
                f"connection to {self.spec.host}:{self.spec.port} timed out after {self.spec.connect_timeout}s"
            ) from exc
        except (OSError, asyncpg.CannotConnectNowError, asyncpg.ConnectionDoesNotExistError) as exc:
            raise AdapterConnectionError(f"cannot connect to {self.spec.host}:{self.spec.port}: {exc}") from exc
        except asyncpg.PostgresError as exc:
            raise AdapterConnectionError(f"connection rejected: {exc}") from exc
        except ssl.SSLError as exc:
            raise AdapterTLSError(f"TLS negotiation failed: {exc.reason}") from exc
        self._conns[db] = conn
        return conn

    async def connect(self) -> None:
        await self._conn()

    async def close(self) -> None:
        conns, self._conns = list(self._conns.values()), {}
        for c in conns:
            try:
                await asyncio.wait_for(c.close(), timeout=3)
            except Exception:  # noqa: BLE001
                c.terminate()

    async def _fetch(self, query: str, *args: Any, database: str | None = None) -> list[asyncpg.Record]:
        conn = await self._conn(database)
        try:
            return await conn.fetch(query, *args)
        except asyncpg.InsufficientPrivilegeError as exc:
            raise AdapterPermissionError(f"insufficient privilege: {exc}") from exc
        except asyncpg.QueryCanceledError as exc:
            raise AdapterTimeout("query cancelled (statement timeout)") from exc
        except asyncpg.PostgresError as exc:
            raise AdapterError(str(exc)) from exc
        except (OSError, asyncpg.InterfaceError) as exc:
            raise AdapterConnectionError(str(exc)) from exc

    async def _fetchrow(self, query: str, *args: Any, database: str | None = None) -> asyncpg.Record | None:
        rows = await self._fetch(query, *args, database=database)
        return rows[0] if rows else None

    async def _try_fetch(self, query: str, *args: Any, database: str | None = None) -> list[asyncpg.Record] | None:
        """Fetch that returns None on permission / missing-object errors (optional facilities)."""
        try:
            return await self._fetch(query, *args, database=database)
        except AdapterPermissionError:
            return None
        except AdapterError as exc:
            if any(s in str(exc) for s in ("does not exist", "must be loaded", "permission denied")):
                return None
            raise

    async def _version(self) -> int:
        if self._version_num is None:
            row = await self._fetchrow("SELECT current_setting('server_version_num')::int AS v")
            self._version_num = int(row["v"]) if row else 0
        return self._version_num

    async def _pgss_available(self) -> bool:
        if self._pgss is None:
            ext = await self._fetch("SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements'")
            if not ext:
                self._pgss = False
            else:
                self._pgss = await self._try_fetch(Q.PGSS_PROBE) is not None
        return self._pgss

    # ------------------------------------------------------------------------------------
    # discovery
    # ------------------------------------------------------------------------------------
    async def get_server_info(self) -> ServerInfo:
        r = await self._fetchrow(Q.SERVER_INFO)
        assert r is not None
        self._version_num = r["version_num"]
        cloud = await self._fetchrow(Q.CLOUD_HINTS)
        provider = None
        if cloud:
            if cloud["aws_aurora"]:
                provider = "aws-aurora"
            elif cloud["aws_rds"]:
                provider = "aws-rds"
            elif cloud["azure"]:
                provider = "azure"
            elif cloud["gcp_alloydb"]:
                provider = "gcp-alloydb"
            elif cloud["gcp_cloudsql"]:
                provider = "gcp-cloudsql"
        edition = "Aurora PostgreSQL" if provider == "aws-aurora" else "PostgreSQL"
        return ServerInfo(
            engine=self.engine,
            version=r["version"],
            version_num=r["version_num"],
            edition=edition,
            started_at=r["started_at"],
            uptime_seconds=float(r["uptime_seconds"] or 0),
            is_replica=r["is_replica"],
            cloud_provider=provider,
            current_user=r["current_user"],
            hostname=r["server_addr"],
            extra={
                "version_full": r["version_full"],
                "is_superuser": r["is_superuser"],
                "has_pg_monitor": r["has_pg_monitor"],
                "has_read_all_stats": r["has_read_all_stats"],
                "connected_database": r["current_database"],
            },
        )

    async def discover_capabilities(self) -> CapabilityMap:
        caps = dict(self.capability_profile())
        info = await self.get_server_info()
        privileged = bool(info.extra.get("is_superuser"))
        stats = privileged or bool(info.extra.get("has_read_all_stats"))
        if await self._pgss_available():
            caps[Capability.QUERY_STATISTICS] = cap(FULL, "pg_stat_statements")
        else:
            caps[Capability.QUERY_STATISTICS] = cap(
                REQUIRES_EXTENSION,
                "install pg_stat_statements (shared_preload_libraries + CREATE EXTENSION) to enable",
            )
            caps[Capability.QUERY_HISTORY] = cap(REQUIRES_EXTENSION, "requires pg_stat_statements")
        if not stats:
            note = "grant pg_monitor to the monitoring account to see other users' query text and sizes"
            caps[Capability.SESSIONS] = cap(PARTIAL, note)
            if caps[Capability.QUERY_STATISTICS].level is FULL:
                caps[Capability.QUERY_STATISTICS] = cap(PARTIAL, note)
        if not privileged:
            caps[Capability.SECURITY_SCAN] = cap(
                PARTIAL, "pg_hba rules and password hash checks require superuser; those checks are skipped"
            )
        if (info.version_num or 0) < 100000:
            caps[Capability.REPLICATION] = cap(PARTIAL, "PostgreSQL < 10 exposes limited replication views")
        if info.cloud_provider:
            caps[Capability.CONFIGURATION] = cap(
                PARTIAL, f"{info.cloud_provider}: parameters are changed through the provider's parameter groups"
            )
            if not privileged:
                caps[Capability.SESSION_TERMINATE] = cap(
                    REQUIRES_PRIVILEGE, "requires pg_signal_backend or rds_superuser"
                )
        return caps

    # ------------------------------------------------------------------------------------
    # inventory
    # ------------------------------------------------------------------------------------
    async def list_databases(self) -> list[DatabaseInfo]:
        rows = await self._fetch(Q.LIST_DATABASES)
        out = []
        for r in rows:
            out.append(
                DatabaseInfo(
                    name=r["name"],
                    owner=r["owner"],
                    size_bytes=r["size_bytes"],
                    encoding=r["encoding"],
                    collation=r["collation"],
                    allow_connections=r["allow_connections"],
                    is_template=r["is_template"],
                    is_system=r["name"] in SYSTEM_DATABASES or r["is_template"],
                    connection_limit=r["connection_limit"],
                    tablespace=r["tablespace"],
                    accessible=r["accessible"],
                    extra={
                        "sessions": r["numbackends"],
                        "commits": r["xact_commit"],
                        "rollbacks": r["xact_rollback"],
                        "cache_hit_ratio": (
                            round(100 * r["blks_hit"] / (r["blks_hit"] + r["blks_read"]), 2)
                            if r["blks_hit"] is not None and (r["blks_hit"] + r["blks_read"]) > 0
                            else None
                        ),
                        "deadlocks": r["deadlocks"],
                        "temp_bytes": r["temp_bytes"],
                        "stats_reset": r["stats_reset"].isoformat() if r["stats_reset"] else None,
                    },
                )
            )
        return out

    async def list_schemas(self, database: str) -> list[SchemaInfo]:
        rows = await self._fetch(Q.LIST_SCHEMAS, database=database)
        return [
            SchemaInfo(
                name=r["name"],
                owner=r["owner"],
                is_system=r["is_system"],
                size_bytes=r["size_bytes"],
                object_counts={
                    k: int(r[k]) for k in ("tables", "views", "materialized_views", "indexes", "sequences", "routines")
                },
            )
            for r in rows
        ]

    async def list_objects(
        self, database: str, schema: str, object_type: str | None = None, limit: int = 5000
    ) -> list[ObjectInfo]:
        out: list[ObjectInfo] = []
        types = (
            [object_type]
            if object_type
            else [
                "table",
                "view",
                "materialized_view",
                "index",
                "sequence",
                "foreign_table",
                "function",
                "procedure",
                "trigger",
                "constraint",
            ]
        )
        rel_kinds = [k for t in types for k in Q.RELKIND_TYPES.get(t, ())]
        if rel_kinds:
            for r in await self._fetch(Q.LIST_RELATIONS, schema, rel_kinds, limit, database=database):
                out.append(
                    ObjectInfo(
                        name=r["name"],
                        schema_name=r["schema_name"],
                        type=r["type"],
                        owner=r["owner"],
                        size_bytes=r["size_bytes"],
                        row_estimate=r["row_estimate"],
                        parent=r["parent"],
                        extra={
                            "partitioned": r["partitioned"],
                            "is_partition": r["is_partition"],
                            "comment": r["comment"],
                        },
                    )
                )
        routine_types = [t for t in types if t in ("function", "procedure")]
        if routine_types:
            filt = routine_types[0] if len(routine_types) == 1 else None
            for r in await self._fetch(Q.LIST_ROUTINES, schema, filt, limit, database=database):
                out.append(
                    ObjectInfo(
                        name=r["name"],
                        schema_name=r["schema_name"],
                        type=r["type"],
                        owner=r["owner"],
                        extra={
                            "arguments": r["arguments"],
                            "language": r["language"],
                            "security_definer": r["security_definer"],
                            "returns": r["returns"],
                        },
                    )
                )
        if "trigger" in types:
            for r in await self._fetch(Q.LIST_TRIGGERS, schema, limit, database=database):
                out.append(
                    ObjectInfo(
                        name=r["name"],
                        schema_name=r["schema_name"],
                        type="trigger",
                        parent=r["parent"],
                        extra={"definition": r["definition"], "enabled": r["enabled"]},
                    )
                )
        if "constraint" in types:
            for r in await self._fetch(Q.LIST_CONSTRAINTS, schema, limit, database=database):
                out.append(
                    ObjectInfo(
                        name=r["name"],
                        schema_name=r["schema_name"],
                        type="constraint",
                        parent=r["parent"],
                        extra={"kind": r["kind"], "definition": r["definition"]},
                    )
                )
        return out

    async def get_object_detail(self, database: str, schema: str, name: str, object_type: str) -> ObjectDetail:
        if object_type in ("function", "procedure"):
            objs = [o for o in await self.list_objects(database, schema, object_type) if o.name == name]
            if not objs:
                raise AdapterError(f"{object_type} {schema}.{name} not found")
            row = await self._fetchrow(Q.FUNC_DEF, schema, name, database=database)
            return ObjectDetail(object=objs[0], definition=row[0] if row else None)

        rel = await self._fetchrow(Q.REL_OID, schema, name, database=database)
        if rel is None:
            raise AdapterError(f"object {schema}.{name} not found")
        oid, relkind = rel["oid"], rel["relkind"]
        objs = [o for o in await self.list_objects(database, schema, None) if o.name == name and o.type == object_type]
        obj = objs[0] if objs else ObjectInfo(name=name, schema_name=schema, type=object_type)
        cols = [ColumnInfo(**dict(r)) for r in await self._fetch(Q.COLUMNS, oid, database=database)]
        detail = ObjectDetail(object=obj, columns=cols)
        if relkind in ("r", "p", "m", "f"):
            detail.indexes = [_jsonable(dict(r)) for r in await self._fetch(Q.INDEXES_OF, oid, database=database)]
            detail.constraints = [dict(r) for r in await self._fetch(Q.CONSTRAINTS_OF, oid, database=database)]
            detail.triggers = [dict(r) for r in await self._fetch(Q.TRIGGERS_OF, oid, database=database)]
            st = await self._fetchrow(Q.TABLE_STATS, oid, database=database)
            detail.statistics = _jsonable(dict(st)) if st else {}
        if relkind in ("v", "m"):
            row = await self._fetchrow(Q.VIEW_DEF, oid, database=database)
            kw = "MATERIALIZED VIEW" if relkind == "m" else "VIEW"
            detail.definition = f'CREATE {kw} "{schema}"."{name}" AS\n{row[0] if row else ""}'
        elif relkind == "S":
            row = await self._fetchrow(Q.SEQ_DEF, schema, name, database=database)
            if row:
                detail.statistics = _jsonable(dict(row))
                detail.definition = (
                    f'CREATE SEQUENCE "{schema}"."{name}" AS {row["data_type"]} INCREMENT BY {row["increment_by"]} '
                    f"MINVALUE {row['min_value']} MAXVALUE {row['max_value']} START WITH {row['start_value']} "
                    f"CACHE {row['cache_size']}{' CYCLE' if row['cycle'] else ''};"
                )
        elif relkind in ("i", "I"):
            row = await self._fetchrow("SELECT pg_get_indexdef($1::oid)", oid, database=database)
            detail.definition = (row[0] + ";") if row else None
        elif relkind in ("r", "p", "f"):
            lines = [
                f'  "{c.name}" {c.data_type}'
                + (f" DEFAULT {c.default}" if c.default else "")
                + ("" if c.nullable else " NOT NULL")
                for c in cols
            ]
            lines += [f'  CONSTRAINT "{c["name"]}" {c["definition"]}' for c in detail.constraints]
            ddl = f'CREATE TABLE "{schema}"."{name}" (\n' + ",\n".join(lines) + "\n);"
            idx = [i["definition"] + ";" for i in detail.indexes if not i.get("is_primary")]
            detail.definition = "\n\n".join([ddl, *idx])
            detail.definition_note = (
                "Reconstructed from the system catalog; storage parameters and partitioning are omitted."
            )
        return detail

    # ------------------------------------------------------------------------------------
    # access
    # ------------------------------------------------------------------------------------
    async def _all_roles(self) -> list[DbUser]:
        out = []
        for r in await self._fetch(Q.ROLES):
            reasons: list[str] = []
            if r["rolsuper"]:
                reasons.append("SUPERUSER")
            if r["rolcreaterole"]:
                reasons.append("CREATEROLE")
            if r["rolbypassrls"]:
                reasons.append("BYPASSRLS")
            if r["rolreplication"]:
                reasons.append("REPLICATION")
            for m in r["member_of"]:
                if m in POWERFUL_BUILTIN_ROLES:
                    reasons.append(f"member of {m}")
            out.append(
                DbUser(
                    name=r["name"],
                    can_login=r["rolcanlogin"],
                    is_superuser=r["rolsuper"],
                    attributes={
                        "inherit": r["rolinherit"],
                        "createrole": r["rolcreaterole"],
                        "createdb": r["rolcreatedb"],
                        "replication": r["rolreplication"],
                        "bypassrls": r["rolbypassrls"],
                    },
                    member_of=list(r["member_of"]),
                    valid_until=r["rolvaliduntil"] if r["rolvaliduntil"] and r["rolvaliduntil"].year < 9999 else None,
                    connection_limit=r["rolconnlimit"] if r["rolconnlimit"] >= 0 else None,
                    is_system=r["name"].startswith("pg_"),
                    admin_reasons=reasons,
                )
            )
        return out

    async def list_users(self) -> list[DbUser]:
        return [u for u in await self._all_roles() if u.can_login]

    async def list_roles(self) -> list[DbUser]:
        return [u for u in await self._all_roles() if not u.can_login]

    async def get_privileges(
        self, database: str | None = None, grantee: str | None = None, limit: int = 2000
    ) -> list[Privilege]:
        db = database or self._default_db
        out = [
            Privilege(
                grantee=r["grantee"],
                object_type=r["object_type"],
                object_name=r["object_name"],
                privilege=r["privilege"],
                grantor=r["grantor"],
                grantable=r["grantable"],
                database=db,
            )
            for r in await self._fetch(Q.PRIVILEGES, grantee, limit, database=db)
        ]
        for r in await self._fetch(Q.ROLE_MEMBERSHIPS, grantee):
            out.append(
                Privilege(
                    grantee=r["grantee"],
                    object_type="role",
                    object_name=r["role"],
                    privilege="MEMBER",
                    grantor=r["grantor"],
                    grantable=r["admin_option"],
                    via_role=r["role"],
                )
            )
        return out

    # ------------------------------------------------------------------------------------
    # activity
    # ------------------------------------------------------------------------------------
    async def get_sessions(self) -> list[SessionInfo]:
        rows = await self._fetch(Q.SESSIONS)
        return [
            SessionInfo(
                session_id=str(r["pid"]),
                username=r["usename"],
                database=r["datname"],
                application=r["application_name"] or None,
                client_address=r["client_addr"],
                state=r["state"],
                query=r["query"],
                query_start=r["query_start"],
                backend_start=r["backend_start"],
                xact_start=r["xact_start"],
                duration_seconds=float(r["duration_seconds"]) if r["duration_seconds"] is not None else None,
                wait_event_type=r["wait_event_type"],
                wait_event=r["wait_event"],
                blocked_by=[str(p) for p in r["blocked_by"] or []],
                backend_type=r["backend_type"],
                ssl=r["ssl"],
            )
            for r in rows
        ]

    async def get_locks(self) -> list[LockInfo]:
        query = Q.LOCKS if await self._version() >= 140000 else Q.LOCKS_LEGACY
        return [
            LockInfo(
                session_id=str(r["pid"]),
                lock_type=r["locktype"],
                mode=r["mode"],
                granted=r["granted"],
                database=r["datname"],
                relation=r["relation"],
                username=r["usename"],
                waiting_seconds=float(r["waiting_seconds"]) if r["waiting_seconds"] is not None else None,
                blocked_by=[str(p) for p in r["blocked_by"] or []],
                query=r["query"],
            )
            for r in await self._fetch(query)
        ]

    async def cancel_session(self, session_id: str, terminate: bool = False) -> bool:
        if not session_id.isdigit():
            raise AdapterError("invalid session id")
        fn = "pg_terminate_backend" if terminate else "pg_cancel_backend"
        row = await self._fetchrow(f"SELECT {fn}($1::int)", int(session_id))
        return bool(row and row[0])

    # ------------------------------------------------------------------------------------
    # metrics
    # ------------------------------------------------------------------------------------
    async def get_performance_metrics(self, slow: bool = True) -> MetricSnapshot:
        snap = MetricSnapshot(collected_at=datetime.now(UTC))
        g, c = snap.gauges, snap.counters

        a = await self._fetchrow(Q.ACTIVITY_METRICS)
        if a:
            usable = max(a["max_connections"] - a["reserved_connections"], 1)
            g.update(
                {
                    "connections.total": a["total"],
                    "connections.active": a["active"],
                    "connections.idle": a["idle"],
                    "connections.idle_in_transaction": a["idle_in_txn"],
                    "connections.max": a["max_connections"],
                    "connections.utilization": round(100.0 * a["total"] / usable, 2),
                    "sessions.waiting": a["waiting"],
                    "sessions.blocked": a["blocked"],
                    "transactions.longest_seconds": float(a["longest_xact"]),
                    "query.longest_running_seconds": float(a["longest_query"]),
                    "pg.idle_in_transaction_longest_seconds": float(a["longest_idle_in_txn"]),
                }
            )
        info = await self._fetchrow(
            "SELECT extract(epoch FROM now() - pg_postmaster_start_time()) AS up, pg_is_in_recovery() AS rec"
        )
        if info:
            g["uptime.seconds"] = float(info["up"])
            g["availability.up"] = 1.0
            is_replica = bool(info["rec"])
            g["replication.is_replica"] = 1.0 if is_replica else 0.0
            if is_replica:
                lag = await self._fetchrow(Q.REPLICA_LAG)
                if lag and lag["lag"] is not None:
                    g["replication.lag_seconds"] = float(lag["lag"])
            else:
                rep = await self._try_fetch(Q.PRIMARY_REPLICATION)
                if rep:
                    g["replication.replica_count"] = float(rep[0]["replicas"])
                    if rep[0]["replicas"]:
                        g["replication.lag_seconds"] = float(rep[0]["max_lag"])

        lk = await self._fetchrow(Q.LOCK_COUNTS)
        if lk:
            g["locks.total"], g["locks.waiting"] = float(lk["total"]), float(lk["waiting"])

        d = await self._fetchrow(Q.DATABASE_COUNTERS)
        if d:
            c.update(
                {
                    "transactions.commits": d["commits"] or 0,
                    "transactions.rollbacks": d["rollbacks"] or 0,
                    "io.blocks_read": d["blks_read"] or 0,
                    "io.blocks_hit": d["blks_hit"] or 0,
                    "rows.returned": d["tup_returned"] or 0,
                    "rows.modified": d["tup_modified"] or 0,
                    "deadlocks": d["deadlocks"] or 0,
                    "temp.files": d["temp_files"] or 0,
                    "temp.bytes": d["temp_bytes"] or 0,
                    "pg.conflicts": d["conflicts"] or 0,
                }
            )
        size = await self._fetchrow(Q.DATABASE_TOTAL_SIZE) if slow else None
        if size:
            g["storage.database_bytes"] = float(size[0])

        ck = await self._try_fetch(Q.CHECKPOINTS_17 if await self._version() >= 170000 else Q.CHECKPOINTS_LEGACY)
        if ck:
            c["pg.checkpoints_timed"], c["pg.checkpoints_requested"] = ck[0]["timed"], ck[0]["requested"]

        for r in await self._fetch(Q.WAIT_EVENTS):
            snap.labeled.append(("wait.active_sessions", {"type": r["wait_type"]}, float(r["n"])))

        if await self._pgss_available():
            total_col = "total_exec_time" if await self._version() >= 130000 else "total_time"
            t = await self._try_fetch(Q.PGSS_TOTALS.format(total=total_col))
            if t and t[0]["calls"] is not None:
                c["query.calls"], c["query.total_ms"] = t[0]["calls"], t[0]["total_ms"]
        else:
            snap.unavailable["query.mean_latency_ms"] = "pg_stat_statements not available"

        snap.unavailable.setdefault("cpu.percent", "not exposed by PostgreSQL through SQL")
        snap.unavailable.setdefault("memory.used_bytes", "not exposed by PostgreSQL through SQL")
        return snap

    async def get_query_statistics(self, limit: int = 100) -> list[QueryStat]:
        if not await self._pgss_available():
            raise AdapterNotSupported(Capability.QUERY_STATISTICS, "pg_stat_statements is not installed or not loaded")
        v = await self._version()
        cols = (
            {"total": "total_exec_time", "mean": "mean_exec_time", "max": "max_exec_time", "stddev": "stddev_exec_time"}
            if v >= 130000
            else {"total": "total_time", "mean": "mean_time", "max": "max_time", "stddev": "stddev_time"}
        )
        rows = await self._fetch(Q.PGSS_TOP.format(**cols), limit)
        return [
            QueryStat(
                query_id=r["query_id"] or "0",
                query_text=r["query"] or "",
                database=r["datname"],
                username=r["rolname"],
                calls=r["calls"],
                total_ms=float(r["total_ms"]),
                mean_ms=float(r["mean_ms"]),
                max_ms=float(r["max_ms"]) if r["max_ms"] is not None else None,
                stddev_ms=float(r["stddev_ms"]) if r["stddev_ms"] is not None else None,
                rows=r["rows"],
                logical_reads=(r["shared_blks_hit"] or 0) + (r["shared_blks_read"] or 0),
                physical_reads=r["shared_blks_read"],
                temp_blocks=(r["temp_blks_written"] or 0) + (r["temp_blks_read"] or 0),
            )
            for r in rows
        ]

    # ------------------------------------------------------------------------------------
    # explain
    # ------------------------------------------------------------------------------------
    @staticmethod
    def _convert_plan(node: dict[str, Any], counter: list[int]) -> PlanNode:
        counter[0] += 1
        nid = counter[0]
        op = node.get("Node Type", "?")
        if node.get("Join Type") and "Join" in op or op == "Nested Loop":
            op = f"{op} ({node.get('Join Type', 'Inner')})"
        if node.get("Strategy") and op == "Aggregate":
            op = f"{node['Strategy'].title()} Aggregate"
        relation = node.get("Relation Name")
        if relation and node.get("Schema"):
            relation = f"{node['Schema']}.{relation}"
        detail_parts = [
            f"{k}: {', '.join(map(str, node[k])) if isinstance(node[k], list) else node[k]}"
            for k in ("Index Cond", "Hash Cond", "Merge Cond", "Join Filter", "Sort Key", "Group Key", "Recheck Cond")
            if node.get(k)
        ]
        pn = PlanNode(
            id=nid,
            operator=op,
            detail="; ".join(str(p) for p in detail_parts) or None,
            relation=relation or node.get("CTE Name") or node.get("Function Name"),
            index=node.get("Index Name"),
            startup_cost=node.get("Startup Cost"),
            total_cost=node.get("Total Cost"),
            estimated_rows=node.get("Plan Rows"),
            actual_rows=node.get("Actual Rows"),
            actual_total_ms=node.get("Actual Total Time"),
            loops=node.get("Actual Loops"),
            shared_hit=node.get("Shared Hit Blocks"),
            shared_read=node.get("Shared Read Blocks"),
            temp_written=node.get("Temp Written Blocks"),
            sort_space_type=node.get("Sort Space Type"),
            filter=node.get("Filter"),
            rows_removed_by_filter=node.get("Rows Removed by Filter"),
            raw={k: v for k, v in node.items() if k != "Plans"},
        )
        pn.children = [PostgreSQLAdapter._convert_plan(c, counter) for c in node.get("Plans", [])]
        return pn

    async def explain_query(self, sql: str, database: str | None = None, analyze: bool = False) -> ExplainResult:
        stmt = sql.strip().rstrip(";")
        if re.match(r"^\s*explain\b", stmt, re.I):
            raise AdapterError("submit the statement without the EXPLAIN keyword")
        conn = await self._conn(database)
        v = await self._version()
        notes: list[str] = []
        opts = ["FORMAT JSON", "COSTS", "VERBOSE false"]
        n_params = max((int(m) for m in re.findall(r"\$(\d+)", stmt)), default=0)
        if analyze and n_params:
            raise AdapterError("EXPLAIN ANALYZE needs literal values instead of $n parameters")
        if analyze:
            opts += ["ANALYZE", "BUFFERS", "TIMING"]
        elif re.search(r"\$\d+", stmt):
            if v >= 160000:
                opts.append("GENERIC_PLAN")
                notes.append("Statement contains parameters; a generic plan was produced (PostgreSQL 16+).")
            else:
                raise AdapterError("parameterized statements need literal values on PostgreSQL < 16")
        explain_sql = f"EXPLAIN ({', '.join(opts)}) {stmt}"
        tr = conn.transaction(readonly=not analyze)
        await tr.start()
        try:
            await conn.execute(f"SET LOCAL statement_timeout = {int(self.spec.statement_timeout * 1000)}")
            # GENERIC_PLAN ignores parameter values; NULLs satisfy the extended protocol.
            raw = await conn.fetchval(explain_sql, *([None] * n_params))
        except asyncpg.InterfaceError as exc:
            raise AdapterError(str(exc)) from exc
        except asyncpg.InsufficientPrivilegeError as exc:
            raise AdapterPermissionError(str(exc)) from exc
        except asyncpg.QueryCanceledError as exc:
            raise AdapterError("EXPLAIN cancelled by statement timeout") from exc
        except asyncpg.PostgresError as exc:
            raise AdapterError(str(exc)) from exc
        finally:
            await tr.rollback()  # always: EXPLAIN ANALYZE side effects are discarded
        data = json.loads(raw) if isinstance(raw, str) else raw
        top = data[0]
        root = self._convert_plan(top["Plan"], [0])
        issues = analyze_plan(root, analyzed=analyze)
        if analyze:
            notes.append("The statement was executed inside a transaction that was rolled back.")
        return ExplainResult(
            engine=self.engine,
            mode="analyze" if analyze else "estimate",
            executed=analyze,
            root=root,
            issues=issues,
            total_cost=root.total_cost,
            planning_ms=top.get("Planning Time"),
            execution_ms=top.get("Execution Time"),
            raw=data,
            notes=notes,
        )

    # ------------------------------------------------------------------------------------
    # replication / configuration / storage
    # ------------------------------------------------------------------------------------
    async def get_replication_status(self) -> ReplicationInfo:
        rec = await self._fetchrow("SELECT pg_is_in_recovery() AS r")
        slots = [_jsonable(dict(r)) for r in (await self._try_fetch(Q.REPLICATION_SLOTS) or [])]
        if rec and rec["r"]:
            lag = await self._fetchrow(Q.REPLICA_LAG)
            recv = await self._try_fetch(Q.WAL_RECEIVER)
            return ReplicationInfo(
                role="replica",
                lag_seconds=float(lag["lag"]) if lag and lag["lag"] is not None else None,
                upstream=_jsonable(dict(recv[0])) if recv else None,
                slots=slots,
                notes=[] if recv is not None else ["pg_stat_wal_receiver requires pg_monitor"],
            )
        reps = await self._try_fetch(Q.REPLICATION_PRIMARY_DETAIL)
        replicas = [_jsonable(dict(r)) for r in reps or []]
        lag = max((r.get("replay_lag_s") or 0 for r in replicas), default=None) if replicas else None
        notes = [] if reps is not None else ["pg_stat_replication details require pg_monitor"]
        return ReplicationInfo(
            role="primary" if replicas else "standalone", replicas=replicas, lag_seconds=lag, slots=slots, notes=notes
        )

    async def get_configuration(self) -> list[ConfigParameter]:
        return [
            ConfigParameter(
                name=r["name"],
                value=r["setting"],
                unit=r["unit"],
                category=r["category"],
                description=r["short_desc"],
                source=r["source"],
                default_value=r["boot_val"],
                requires_restart=r["context"] == "postmaster",
                pending_restart=r["pending_restart"],
                is_default=r["is_default"],
            )
            for r in await self._fetch(Q.SETTINGS)
        ]

    async def get_storage_metrics(self) -> StorageInfo:
        dbs = await self.list_databases()
        ts = await self._try_fetch(Q.TABLESPACES)
        notes = []
        if ts is None:
            ts = await self._fetch(Q.TABLESPACES_NOSIZE)
            notes.append("tablespace sizes require CREATE on the tablespace or pg_read_all_stats")
        temp = await self._fetchrow(Q.TEMP_USAGE)
        return StorageInfo(
            total_bytes=sum(d.size_bytes or 0 for d in dbs),
            databases=[{"name": d.name, "size_bytes": d.size_bytes, "tablespace": d.tablespace} for d in dbs],
            tablespaces=[_jsonable(dict(r)) for r in ts],
            top_tables=[_jsonable(dict(r)) for r in await self._fetch(Q.TOP_TABLES)],
            top_indexes=[_jsonable(dict(r)) for r in await self._fetch(Q.TOP_INDEXES)],
            temp=_jsonable(dict(temp)) if temp else {},
            notes=notes + [f"table and index sizes are for database {self._default_db!r}"],
        )

    # ------------------------------------------------------------------------------------
    # advisor evidence
    # ------------------------------------------------------------------------------------
    async def get_security_context(self) -> dict[str, Any]:
        info = await self.get_server_info()
        settings = {r["name"]: r["setting"] for r in await self._fetch(Q.SECURITY_SETTINGS)}
        roles = await self._all_roles()
        md5 = await self._try_fetch(Q.MD5_PASSWORDS)
        empty_pw = await self._try_fetch(Q.EMPTY_PASSWORD_LOGINS)
        hba = await self._try_fetch(Q.HBA_RULES)
        ssl_conns = await self._fetchrow(Q.SSL_CONNECTIONS)
        public_create = await self._fetchrow(Q.PUBLIC_SCHEMA_CREATE)
        public_db = await self._fetch(Q.PUBLIC_DATABASE_PRIVS)
        direct = await self._fetch(Q.DIRECT_TABLE_GRANTS)
        secdef = await self._fetch(Q.SECURITY_DEFINER_FUNCS)
        exts = {r["extname"]: r["extversion"] for r in await self._fetch(Q.EXTENSIONS)}
        return {
            "engine": self.engine,
            "version_num": info.version_num,
            "version": info.version,
            "cloud_provider": info.cloud_provider,
            "current_user": info.current_user,
            "current_user_superuser": info.extra.get("is_superuser"),
            "settings": settings,
            "roles": [r.model_dump(mode="json") for r in roles],
            "md5_password_roles": [r["rolname"] for r in md5] if md5 is not None else None,
            "empty_password_logins": [r["rolname"] for r in empty_pw] if empty_pw is not None else None,
            "hba_rules": [_jsonable(dict(r)) for r in hba] if hba is not None else None,
            "non_ssl_connections": int(ssl_conns["non_ssl"]) if ssl_conns else None,
            "remote_connections": int(ssl_conns["total"]) if ssl_conns else None,
            "public_schema_create": bool(public_create[0]) if public_create else None,
            "public_database_privileges": [_jsonable(dict(r)) for r in public_db],
            "direct_table_grants": [_jsonable(dict(r)) for r in direct],
            "security_definer_without_search_path": [dict(r) for r in secdef],
            "extensions": exts,
            "database": self._default_db,
        }

    async def get_tuning_context(self) -> dict[str, Any]:
        settings = {}
        for r in await self._fetch(Q.TUNING_SETTINGS):
            settings[r["name"]] = {
                "value": r["setting"],
                "unit": r["unit"],
                "bytes": _to_bytes(r["setting"], r["unit"]),
            }
        reset = await self._fetchrow(Q.STATS_RESET)
        return {
            "engine": self.engine,
            "version_num": await self._version(),
            "database": self._default_db,
            "settings": settings,
            "stats_reset": reset[0].isoformat() if reset and reset[0] else None,
            "seq_scan_tables": [_jsonable(dict(r)) for r in await self._fetch(Q.SEQ_SCAN_TABLES)],
            "dead_tuple_tables": [_jsonable(dict(r)) for r in await self._fetch(Q.DEAD_TUPLE_TABLES)],
            "unused_indexes": [_jsonable(dict(r)) for r in await self._fetch(Q.UNUSED_INDEXES)],
            "duplicate_indexes": [_jsonable(dict(r)) for r in await self._fetch(Q.DUPLICATE_INDEXES)],
            "pg_stat_statements": await self._pgss_available(),
        }

    # ------------------------------------------------------------------------------------
    # workspace
    # ------------------------------------------------------------------------------------
    async def execute_sql(
        self, sql: str, database: str | None, *, read_only: bool, max_rows: int, timeout_s: float
    ) -> ExecutionResult:
        conn = await self._conn(database)
        start = time.perf_counter()
        timeout_ms = int(timeout_s * 1000)
        stmt_text = sql.strip().rstrip(";")
        result = ExecutionResult()
        try:
            if not read_only and NO_TXN_STATEMENTS.match(stmt_text):
                await conn.execute(f"SET statement_timeout = {timeout_ms}")
                try:
                    result.status_message = await conn.execute(stmt_text)
                finally:
                    await conn.execute(f"SET statement_timeout = {int(self.spec.statement_timeout * 1000)}")
                result.committed = True
            else:
                tr = conn.transaction(readonly=read_only)
                await tr.start()
                ok = False
                try:
                    await conn.execute(f"SET LOCAL statement_timeout = {timeout_ms}")
                    prepared = await conn.prepare(stmt_text)
                    attrs = prepared.get_attributes()
                    if attrs:
                        result.columns = [{"name": a.name, "type": a.type.name} for a in attrs]
                        cur = await prepared.cursor()
                        records = await cur.fetch(max_rows + 1)
                        result.truncated = len(records) > max_rows
                        result.rows = [[_jsonable(v) for v in r.values()] for r in records[:max_rows]]
                        result.row_count = len(result.rows)
                        result.status_message = f"SELECT {result.row_count}{'+' if result.truncated else ''}"
                    else:
                        await prepared.fetch()
                        result.status_message = prepared.get_statusmsg()
                        m = re.search(r"(\d+)$", result.status_message or "")
                        result.row_count = int(m.group(1)) if m else 0
                    ok = True
                finally:
                    if ok and not read_only:
                        await tr.commit()
                        result.committed = True
                    else:
                        await tr.rollback()
        except asyncpg.InterfaceError as exc:
            raise AdapterError(f"{exc} (the workspace does not support bind parameters; use literal values)") from exc
        except asyncpg.ReadOnlySQLTransactionError as exc:
            raise AdapterPermissionError("statement attempted to write inside a read-only transaction") from exc
        except asyncpg.InsufficientPrivilegeError as exc:
            raise AdapterPermissionError(str(exc)) from exc
        except asyncpg.QueryCanceledError as exc:
            raise AdapterError(f"statement cancelled after {timeout_s:.0f}s timeout") from exc
        except asyncpg.PostgresError as exc:
            raise AdapterError(str(exc)) from exc
        result.duration_ms = round((time.perf_counter() - start) * 1000, 2)
        return result

    # ------------------------------------------------------------------------------------
    # administration
    # ------------------------------------------------------------------------------------
    def build_admin(self, action: str, params: dict[str, Any]) -> Any:
        from app.adapters.postgres import admin as pg_admin

        return pg_admin.build(action, params)

    async def run_admin(self, stmt: Any) -> dict[str, Any]:
        conn = await self._conn(stmt.database)
        done = 0
        try:
            for sql in stmt.statements:
                # outside an explicit transaction: CREATE DATABASE / INDEX CONCURRENTLY require it
                await conn.execute(sql)
                done += 1
        except asyncpg.InsufficientPrivilegeError as exc:
            raise AdapterPermissionError(str(exc)) from exc
        except asyncpg.QueryCanceledError as exc:
            raise AdapterTimeout("statement cancelled (statement timeout)") from exc
        except asyncpg.PostgresError as exc:
            raise AdapterError(str(exc)) from exc
        return {"statements_executed": done}
