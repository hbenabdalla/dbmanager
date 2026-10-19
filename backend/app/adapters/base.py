"""Database adapter contract.

Every engine integration implements :class:`BaseDatabaseAdapter`. The core platform only
talks to this interface — vendor SQL never leaves the adapter package.

Lifecycle: an adapter instance is cheap and short-lived. It is created for one unit of
work (an API call or a collector job), opens connections lazily, and must be closed
(``async with adapter:``). This avoids holding persistent connections to every server.
"""

from __future__ import annotations

from abc import ABC
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import BaseModel, Field

from app.domain.plan_analysis import ExplainResult

if TYPE_CHECKING:
    from app.adapters.admin import AdminStatement


# ----------------------------------------------------------------------------------------
# Capabilities
# ----------------------------------------------------------------------------------------
class Capability(StrEnum):
    CONNECTION = "connection"
    SERVER_INFO = "server_info"
    DATABASES = "databases"
    DATABASE_CREATE = "database_create"
    DATABASE_DROP = "database_drop"
    SCHEMAS = "schemas"
    OBJECTS = "objects"
    OBJECT_DDL = "object_ddl"
    USERS = "users"
    ROLES = "roles"
    PRIVILEGES = "privileges"
    SESSIONS = "sessions"
    SESSION_CANCEL = "session_cancel"
    SESSION_TERMINATE = "session_terminate"
    LOCKS = "locks"
    PERFORMANCE_METRICS = "performance_metrics"
    CPU_METRICS = "cpu_metrics"
    MEMORY_METRICS = "memory_metrics"
    WAIT_EVENTS = "wait_events"
    QUERY_STATISTICS = "query_statistics"
    QUERY_HISTORY = "query_history"
    EXPLAIN = "explain"
    EXPLAIN_ANALYZE = "explain_analyze"
    REPLICATION = "replication"
    CONFIGURATION = "configuration"
    STORAGE = "storage"
    TABLESPACES = "tablespaces"
    SECURITY_SCAN = "security_scan"
    LOGS = "logs"
    SQL_WORKSPACE = "sql_workspace"
    READ_ONLY_TRANSACTIONS = "read_only_transactions"
    #: adapter-specific administration statements (create database/schema/user/role, grant,
    #: revoke, create index) generated and executed through the guarded admin API
    ADMINISTRATION = "administration"
    #: engine-specific recommendation rules exist and are fed by this adapter's evidence
    RECOMMENDATIONS = "recommendations"


class CapabilityLevel(StrEnum):
    FULL = "FULL"
    PARTIAL = "PARTIAL"
    NOT_SUPPORTED = "NOT_SUPPORTED"
    REQUIRES_EXTENSION = "REQUIRES_EXTENSION"
    REQUIRES_LICENSE = "REQUIRES_LICENSE"
    REQUIRES_PRIVILEGE = "REQUIRES_PRIVILEGE"
    #: the engine offers it, but a setting/facility must be enabled first (e.g. Query Store,
    #: performance_schema, explain tables)
    REQUIRES_CONFIGURATION = "REQUIRES_CONFIGURATION"
    #: only available through infrastructure outside SQL (Db2 for z/OS IFI/IFCID traces, RMF, OMEGAMON)
    REQUIRES_EXTERNAL_MONITORING = "REQUIRES_EXTERNAL_MONITORING"
    #: the engine could support it, this adapter does not implement it (yet)
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    PLANNED = "PLANNED"  # engine supports it, adapter implementation scheduled for a later phase

    @property
    def usable(self) -> bool:
        return self in (CapabilityLevel.FULL, CapabilityLevel.PARTIAL)


class CapabilityStatus(BaseModel):
    level: CapabilityLevel
    note: str | None = None


CapabilityMap = dict[Capability, CapabilityStatus]


def cap(level: CapabilityLevel, note: str | None = None) -> CapabilityStatus:
    return CapabilityStatus(level=level, note=note)


FULL = CapabilityLevel.FULL
PARTIAL = CapabilityLevel.PARTIAL
NOT_SUPPORTED = CapabilityLevel.NOT_SUPPORTED
REQUIRES_EXTENSION = CapabilityLevel.REQUIRES_EXTENSION
REQUIRES_LICENSE = CapabilityLevel.REQUIRES_LICENSE
REQUIRES_PRIVILEGE = CapabilityLevel.REQUIRES_PRIVILEGE
REQUIRES_CONFIGURATION = CapabilityLevel.REQUIRES_CONFIGURATION
REQUIRES_EXTERNAL_MONITORING = CapabilityLevel.REQUIRES_EXTERNAL_MONITORING
NOT_IMPLEMENTED = CapabilityLevel.NOT_IMPLEMENTED
PLANNED = CapabilityLevel.PLANNED


# ----------------------------------------------------------------------------------------
# Connection specification
# ----------------------------------------------------------------------------------------
class ConnectionSpec(BaseModel):
    host: str
    port: int
    database: str | None = None
    username: str | None = None
    password: str | None = Field(default=None, repr=False)
    tls_mode: str = "prefer"  # disable|prefer|require|verify-ca|verify-full
    tls_ca_pem: str | None = Field(default=None, repr=False)
    connect_timeout: float = 8.0
    statement_timeout: float = 30.0
    application_name: str = "dbmanager"
    options: dict[str, Any] = Field(default_factory=dict)
    licensed_features: list[str] = Field(default_factory=list)
    # Demo adapter only: identifies the simulated server profile.
    demo_profile: dict[str, Any] | None = None


# ----------------------------------------------------------------------------------------
# DTOs returned by adapters (also used directly as API response models)
# ----------------------------------------------------------------------------------------
class ServerInfo(BaseModel):
    engine: str
    version: str
    version_num: int | None = None
    edition: str | None = None
    started_at: datetime | None = None
    uptime_seconds: float | None = None
    is_replica: bool | None = None
    cloud_provider: str | None = None
    current_user: str | None = None
    hostname: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class DatabaseInfo(BaseModel):
    name: str
    owner: str | None = None
    size_bytes: int | None = None
    encoding: str | None = None
    collation: str | None = None
    allow_connections: bool | None = None
    is_template: bool = False
    is_system: bool = False
    connection_limit: int | None = None
    tablespace: str | None = None
    accessible: bool = True
    extra: dict[str, Any] = Field(default_factory=dict)


class SchemaInfo(BaseModel):
    name: str
    owner: str | None = None
    is_system: bool = False
    object_counts: dict[str, int] = Field(default_factory=dict)
    size_bytes: int | None = None


class ObjectInfo(BaseModel):
    name: str
    schema_name: str
    type: str  # table|view|materialized_view|index|sequence|function|procedure|trigger|constraint|synonym|foreign_table
    owner: str | None = None
    size_bytes: int | None = None
    row_estimate: int | None = None
    parent: str | None = None  # e.g. table of an index/trigger/constraint
    extra: dict[str, Any] = Field(default_factory=dict)


class ColumnInfo(BaseModel):
    name: str
    data_type: str
    nullable: bool
    default: str | None = None
    position: int
    comment: str | None = None


class ObjectDetail(BaseModel):
    object: ObjectInfo
    columns: list[ColumnInfo] = Field(default_factory=list)
    indexes: list[dict[str, Any]] = Field(default_factory=list)
    constraints: list[dict[str, Any]] = Field(default_factory=list)
    triggers: list[dict[str, Any]] = Field(default_factory=list)
    statistics: dict[str, Any] = Field(default_factory=dict)
    definition: str | None = None
    definition_note: str | None = None


class DbUser(BaseModel):
    name: str
    can_login: bool
    is_superuser: bool = False
    attributes: dict[str, Any] = Field(default_factory=dict)
    member_of: list[str] = Field(default_factory=list)
    valid_until: datetime | None = None
    last_login_at: datetime | None = None
    connection_limit: int | None = None
    is_system: bool = False
    admin_reasons: list[str] = Field(default_factory=list)


class Privilege(BaseModel):
    grantee: str
    object_type: str  # database|schema|table|view|sequence|function|role|server
    object_name: str
    privilege: str
    grantor: str | None = None
    grantable: bool = False
    via_role: str | None = None
    database: str | None = None


class SessionInfo(BaseModel):
    session_id: str
    username: str | None = None
    database: str | None = None
    application: str | None = None
    client_address: str | None = None
    state: str | None = None
    query: str | None = None
    query_start: datetime | None = None
    backend_start: datetime | None = None
    xact_start: datetime | None = None
    duration_seconds: float | None = None
    wait_event_type: str | None = None
    wait_event: str | None = None
    blocked_by: list[str] = Field(default_factory=list)
    backend_type: str | None = None
    ssl: bool | None = None


class LockInfo(BaseModel):
    session_id: str
    lock_type: str
    mode: str
    granted: bool
    database: str | None = None
    relation: str | None = None
    username: str | None = None
    waiting_seconds: float | None = None
    blocked_by: list[str] = Field(default_factory=list)
    query: str | None = None


class MetricSnapshot(BaseModel):
    """Values collected in one pass.

    ``gauges`` are instantaneous values stored as-is. ``counters`` are cumulative values
    the collector turns into per-second rates using the previous snapshot.
    Keys follow the normalized catalog (see ``app.services.metric_catalog``); engine-only
    metrics use an engine prefix (``pg.``, ``oracle.``, ...).
    """

    collected_at: datetime
    gauges: dict[str, float] = Field(default_factory=dict)
    counters: dict[str, float] = Field(default_factory=dict)
    labeled: list[tuple[str, dict[str, str], float]] = Field(default_factory=list)
    unavailable: dict[str, str] = Field(default_factory=dict)  # metric -> reason


class QueryStat(BaseModel):
    query_id: str
    query_text: str
    database: str | None = None
    username: str | None = None
    calls: int
    total_ms: float
    mean_ms: float
    max_ms: float | None = None
    stddev_ms: float | None = None
    rows: int | None = None
    cpu_ms: float | None = None
    logical_reads: int | None = None
    physical_reads: int | None = None
    temp_blocks: int | None = None
    lock_wait_ms: float | None = None
    #: distinct execution plans currently cached for the statement (Oracle, SQL Server)
    plans: int | None = None
    #: identifier of the plan that accounts for most of the statement's time (Oracle
    #: ``plan_hash_value``); used to detect plan changes, never shown as a cost
    plan_id: int | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class ReplicationInfo(BaseModel):
    role: str  # primary|replica|standalone|unknown
    replicas: list[dict[str, Any]] = Field(default_factory=list)
    upstream: dict[str, Any] | None = None
    lag_seconds: float | None = None
    slots: list[dict[str, Any]] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ConfigParameter(BaseModel):
    name: str
    value: str | None
    unit: str | None = None
    category: str | None = None
    description: str | None = None
    source: str | None = None
    default_value: str | None = None
    requires_restart: bool | None = None
    pending_restart: bool | None = None
    is_default: bool | None = None


class StorageInfo(BaseModel):
    total_bytes: int | None = None
    databases: list[dict[str, Any]] = Field(default_factory=list)
    tablespaces: list[dict[str, Any]] = Field(default_factory=list)
    top_tables: list[dict[str, Any]] = Field(default_factory=list)
    top_indexes: list[dict[str, Any]] = Field(default_factory=list)
    temp: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class ExecutionResult(BaseModel):
    columns: list[dict[str, str]] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    status_message: str | None = None
    duration_ms: float = 0.0
    committed: bool = False


# ----------------------------------------------------------------------------------------
# Errors
# ----------------------------------------------------------------------------------------
class AdapterErrorCode(StrEnum):
    """Normalized, engine-independent error classes (shown to users instead of stack traces)."""

    CONNECTION_FAILED = "CONNECTION_FAILED"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    TLS_ERROR = "TLS_ERROR"
    DATABASE_NOT_FOUND = "DATABASE_NOT_FOUND"
    INSUFFICIENT_PRIVILEGES = "INSUFFICIENT_PRIVILEGES"
    QUERY_TIMEOUT = "QUERY_TIMEOUT"
    FEATURE_NOT_AVAILABLE = "FEATURE_NOT_AVAILABLE"
    DRIVER_NOT_INSTALLED = "DRIVER_NOT_INSTALLED"
    QUERY_FAILED = "QUERY_FAILED"


class AdapterError(Exception):
    """Base error raised by adapters. Messages must never contain credentials."""

    code: AdapterErrorCode = AdapterErrorCode.QUERY_FAILED

    def __init__(self, message: str = "", *, code: AdapterErrorCode | None = None):
        super().__init__(message)
        if code is not None:
            self.code = code


class AdapterConnectionError(AdapterError):
    """The server could not be reached or refused the session (counts toward the circuit breaker)."""

    code = AdapterErrorCode.CONNECTION_FAILED


class AdapterAuthenticationError(AdapterConnectionError):
    code = AdapterErrorCode.AUTHENTICATION_FAILED


class AdapterTLSError(AdapterConnectionError):
    code = AdapterErrorCode.TLS_ERROR


class AdapterDatabaseNotFound(AdapterConnectionError):
    code = AdapterErrorCode.DATABASE_NOT_FOUND


class AdapterPermissionError(AdapterError):
    code = AdapterErrorCode.INSUFFICIENT_PRIVILEGES


class AdapterTimeout(AdapterError):
    code = AdapterErrorCode.QUERY_TIMEOUT


class DriverNotInstalled(AdapterError):
    code = AdapterErrorCode.DRIVER_NOT_INSTALLED


class AdapterNotSupported(AdapterError):
    code = AdapterErrorCode.FEATURE_NOT_AVAILABLE

    def __init__(self, capability: Capability | str, reason: str = "not supported by this adapter"):
        super().__init__(f"{capability}: {reason}")
        self.capability = str(capability)
        self.reason = reason


# ----------------------------------------------------------------------------------------
# Adapter contract
# ----------------------------------------------------------------------------------------
class BaseDatabaseAdapter(ABC):  # noqa: B024 - every method has a safe default
    engine: ClassVar[str]
    display_name: ClassVar[str]
    default_port: ClassVar[int]
    driver: ClassVar[str] = ""
    #: key into :mod:`app.adapters.drivers` (installed driver / client library check)
    driver_key: ClassVar[str] = ""
    implementation_phase: ClassVar[int] = 1
    #: True once the adapter talks to real servers (False: inventory-only stub)
    implemented: ClassVar[bool] = False
    #: overall support statement shown on the Engine Support page
    support_level: ClassVar[str] = "planned"  # supported | partial | planned
    support_note: ClassVar[str] = ""
    #: engine-specific connection settings accepted in ``ConnectionSpec.options``
    connection_options: ClassVar[list[dict[str, Any]]] = []
    #: separately licensed vendor features an administrator can mark as licensed per server
    #: (``licensed_features``); every one is OFF unless explicitly enabled
    licensable_features: ClassVar[list[dict[str, Any]]] = []
    #: Static profile: what this adapter implements for the engine.
    static_capabilities: ClassVar[CapabilityMap] = {}
    #: What the *engine* offers (target classification for the support matrix). Defaults to
    #: ``static_capabilities`` when the adapter fully implements the engine profile.
    engine_capabilities: ClassVar[CapabilityMap] = {}

    def __init__(self, spec: ConnectionSpec):
        self.spec = spec

    # --- lifecycle ---------------------------------------------------------------------
    async def __aenter__(self) -> BaseDatabaseAdapter:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def connect(self) -> None:
        raise AdapterNotSupported(Capability.CONNECTION, self._planned_reason())

    async def close(self) -> None:
        return None

    async def test_connection(self) -> ServerInfo:
        await self.connect()
        return await self.get_server_info()

    # --- discovery -------------------------------------------------------------------------
    @classmethod
    def capability_profile(cls) -> CapabilityMap:
        return {c: cls.static_capabilities.get(c, cap(NOT_SUPPORTED)) for c in Capability}

    @classmethod
    def engine_profile(cls) -> CapabilityMap:
        src = cls.engine_capabilities or cls.static_capabilities
        return {c: src.get(c, cap(NOT_SUPPORTED)) for c in Capability}

    async def discover_capabilities(self) -> CapabilityMap:
        """Static profile refined by runtime facts (version, extensions, privileges, licenses)."""
        return self.capability_profile()

    def _planned_reason(self) -> str:
        return f"{self.display_name} adapter is scheduled for implementation phase {self.implementation_phase}"

    def _ns(self, c: Capability) -> AdapterNotSupported:
        status = self.static_capabilities.get(c)
        if status and status.level is PLANNED:
            return AdapterNotSupported(c, self._planned_reason())
        return AdapterNotSupported(c, (status.note if status else None) or "not supported by this engine/adapter")

    # --- read operations -------------------------------------------------------------------
    async def get_server_info(self) -> ServerInfo:
        raise self._ns(Capability.SERVER_INFO)

    async def list_databases(self) -> list[DatabaseInfo]:
        raise self._ns(Capability.DATABASES)

    async def list_schemas(self, database: str) -> list[SchemaInfo]:
        raise self._ns(Capability.SCHEMAS)

    async def list_objects(self, database: str, schema: str, object_type: str | None = None) -> list[ObjectInfo]:
        raise self._ns(Capability.OBJECTS)

    async def list_tables(self, database: str, schema: str) -> list[ObjectInfo]:
        return await self.list_objects(database, schema, "table")

    async def get_object_detail(self, database: str, schema: str, name: str, object_type: str) -> ObjectDetail:
        raise self._ns(Capability.OBJECT_DDL)

    async def list_users(self) -> list[DbUser]:
        raise self._ns(Capability.USERS)

    async def list_roles(self) -> list[DbUser]:
        raise self._ns(Capability.ROLES)

    async def get_privileges(
        self, database: str | None = None, grantee: str | None = None, limit: int = 2000
    ) -> list[Privilege]:
        raise self._ns(Capability.PRIVILEGES)

    async def get_sessions(self) -> list[SessionInfo]:
        raise self._ns(Capability.SESSIONS)

    async def get_locks(self) -> list[LockInfo]:
        raise self._ns(Capability.LOCKS)

    async def get_storage_metrics(self) -> StorageInfo:
        raise self._ns(Capability.STORAGE)

    async def get_performance_metrics(self, slow: bool = True) -> MetricSnapshot:
        """One collection pass. ``slow=False`` asks the adapter to skip expensive catalog
        queries (database sizes, tablespace usage) that belong to the SLOW interval class."""
        raise self._ns(Capability.PERFORMANCE_METRICS)

    async def get_query_statistics(self, limit: int = 100) -> list[QueryStat]:
        raise self._ns(Capability.QUERY_STATISTICS)

    async def explain_query(self, sql: str, database: str | None = None, analyze: bool = False) -> ExplainResult:
        raise self._ns(Capability.EXPLAIN_ANALYZE if analyze else Capability.EXPLAIN)

    async def get_replication_status(self) -> ReplicationInfo:
        raise self._ns(Capability.REPLICATION)

    async def get_configuration(self) -> list[ConfigParameter]:
        raise self._ns(Capability.CONFIGURATION)

    async def get_security_context(self) -> dict[str, Any]:
        """Raw evidence consumed by the security rule engine (users, auth config, grants...)."""
        raise self._ns(Capability.SECURITY_SCAN)

    async def get_tuning_context(self) -> dict[str, Any]:
        """Raw evidence consumed by the performance rule engine (table/index stats...)."""
        return {}

    async def get_security_findings(self) -> dict[str, Any]:
        """Alias kept for the documented interface; findings are produced by the rule engine
        from :meth:`get_security_context` so rules stay auditable and engine-independent."""
        return await self.get_security_context()

    # --- actions ----------------------------------------------------------------------------
    async def execute_sql(
        self, sql: str, database: str | None, *, read_only: bool, max_rows: int, timeout_s: float
    ) -> ExecutionResult:
        raise self._ns(Capability.SQL_WORKSPACE)

    async def cancel_session(self, session_id: str, terminate: bool = False) -> bool:
        raise self._ns(Capability.SESSION_TERMINATE if terminate else Capability.SESSION_CANCEL)

    def build_admin(self, action: str, params: dict[str, Any]) -> AdminStatement:
        """Build the vendor-specific statements for a guarded administration action
        (see :mod:`app.adapters.admin`). Pure function of the request: never connects."""
        raise self._ns(Capability.ADMINISTRATION)

    async def run_admin(self, stmt: AdminStatement) -> dict[str, Any]:
        raise self._ns(Capability.ADMINISTRATION)
