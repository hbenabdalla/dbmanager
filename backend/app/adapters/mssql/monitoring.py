"""SQL Server monitoring through DMVs: sessions, blocking, locks, performance counters, CPU,
waits, plan-cache statistics, Always On and configuration."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.adapters.base import (
    AdapterNotSupported,
    AdapterPermissionError,
    Capability,
    ConfigParameter,
    LockInfo,
    MetricSnapshot,
    QueryStat,
    ReplicationInfo,
    SessionInfo,
    StorageInfo,
)
from app.adapters.dbapi import AdapterObjectMissing, jsonable, num, text, utc
from app.adapters.mssql import sql as Q
from app.adapters.mssql.base import SQLServerBase

#: cumulative "/sec" performance counters (cntr_type 272696576) → platform counter keys
COUNTERS = {
    ("SQL Statistics", "Batch Requests/sec"): "query.calls",
    ("SQL Statistics", "SQL Compilations/sec"): "mssql.compilations",
    ("SQL Statistics", "SQL Re-Compilations/sec"): "mssql.recompilations",
    ("Databases", "Transactions/sec"): "transactions.commits",
    ("Databases", "Log Flushes/sec"): "mssql.log_flushes",
    ("Databases", "Log Bytes Flushed/sec"): "mssql.log_bytes_flushed",
    ("Locks", "Lock Waits/sec"): "mssql.lock_waits",
    ("Locks", "Number of Deadlocks/sec"): "deadlocks",
    ("Locks", "Lock Timeouts/sec"): "mssql.lock_timeouts",
    ("Buffer Manager", "Page reads/sec"): "io.blocks_read",
    ("Buffer Manager", "Page writes/sec"): "mssql.page_writes",
    ("Buffer Manager", "Lazy writes/sec"): "mssql.lazy_writes",
    ("Buffer Manager", "Checkpoint pages/sec"): "mssql.checkpoint_pages",
    ("Access Methods", "Full Scans/sec"): "mssql.full_scans",
    ("Access Methods", "Index Searches/sec"): "mssql.index_searches",
    ("Access Methods", "Page Splits/sec"): "mssql.page_splits",
    ("General Statistics", "Logins/sec"): "mssql.logins",
}
GAUGES = {
    ("General Statistics", "Processes blocked"): "mssql.processes_blocked",
    ("Buffer Manager", "Page life expectancy"): "mssql.page_life_expectancy",
    ("Memory Manager", "Memory Grants Pending"): "mssql.memory_grants_pending",
}


def _counter_key(row: dict[str, Any]) -> tuple[str, str]:
    obj = (text(row["object_name"]) or "").split(":", 1)[-1]
    return obj, text(row["counter_name"]) or ""


class SQLServerMonitoringMixin(SQLServerBase):
    # ---- sessions & locks -----------------------------------------------------------------
    async def get_sessions(self) -> list[SessionInfo]:
        out = []
        for r in await self._query(Q.SESSIONS):
            active = r["request_status"] is not None
            status = (
                "background"
                if not r["is_user_process"]
                else (
                    "active"
                    if active
                    else ("idle in transaction" if (r["open_transaction_count"] or 0) > 0 else "idle")
                )
            )
            blocker = int(r["blocking_session_id"] or 0)
            out.append(
                SessionInfo(
                    session_id=str(r["session_id"]),
                    username=text(r["login_name"]),
                    database=text(r["database_name"]),
                    application=text(r["program_name"]),
                    client_address=text(r["client_net_address"]) or text(r["host_name"]),
                    state=status,
                    query=text(r["statement_text"]) if active else None,
                    query_start=utc(r["start_time"]) if active else None,
                    backend_start=utc(r["login_time"]),
                    xact_start=utc(r["transaction_begin_time"]),
                    duration_seconds=(num(r["total_elapsed_time"], 0) or 0) / 1000.0 if active else None,
                    wait_event_type="Lock"
                    if (text(r["wait_type"]) or "").startswith("LCK_")
                    else (text(r["wait_type"]) and "Wait"),
                    wait_event=text(r["wait_type"]),
                    blocked_by=[str(blocker)] if blocker > 0 else [],
                    backend_type=text(r["command"]) if not r["is_user_process"] else "client",
                    ssl=(text(r["encrypt_option"]) or "").upper() == "TRUE"
                    if r["encrypt_option"] is not None
                    else None,
                )
            )
        return out

    async def get_locks(self) -> list[LockInfo]:
        try:
            rows = await self._query(Q.LOCKS)
        except AdapterPermissionError as exc:
            raise AdapterNotSupported(Capability.LOCKS, "sys.dm_tran_locks needs VIEW SERVER STATE") from exc
        return [
            LockInfo(
                session_id=str(r["request_session_id"]),
                lock_type=text(r["resource_type"]) or "",
                mode=text(r["request_mode"]) or "",
                granted=(text(r["request_status"]) or "") == "GRANT",
                database=text(r["database_name"]),
                relation=text(r["object_name"]) or (text(r["resource_description"]) or None),
                blocked_by=[str(r["blocking_session_id"])] if r["blocking_session_id"] else [],
                waiting_seconds=(num(r["wait_duration_ms"]) or 0) / 1000.0
                if r["wait_duration_ms"] is not None
                else None,
            )
            for r in rows
        ]

    async def cancel_session(self, session_id: str, terminate: bool = False) -> bool:
        if not session_id.isdigit():
            raise AdapterNotSupported(Capability.SESSION_CANCEL, "invalid session id")
        if not terminate:
            raise AdapterNotSupported(
                Capability.SESSION_CANCEL,
                "SQL Server cannot cancel another session's statement from T-SQL; terminate the session (KILL) instead",
            )
        await self._query(f"KILL {int(session_id)}")
        return True

    # ---- metrics --------------------------------------------------------------------------
    async def get_performance_metrics(self, slow: bool = True) -> MetricSnapshot:
        snap = MetricSnapshot(collected_at=datetime.now(UTC))
        g, c = snap.gauges, snap.counters
        info = await self._server()
        g["availability.up"] = 1.0
        sc = await self._one(Q.SESSION_COUNTS)
        if sc:
            g.update(
                {
                    "connections.total": float(sc["total"] or 0),
                    "connections.active": float(sc["active"] or 0),
                    "connections.idle": float((sc["total"] or 0) - (sc["active"] or 0)),
                    "connections.idle_in_transaction": float(sc["idle_in_txn"] or 0),
                    "sessions.blocked": float(sc["blocked"] or 0),
                    "sessions.waiting": float(sc["blocked"] or 0),
                    "query.longest_running_seconds": float(sc["longest_query_s"] or 0),
                    "mssql.unencrypted_sessions": float(sc["unencrypted"] or 0),
                }
            )
        cfg = {
            text(r["name"]): num(r["value_in_use"])
            for r in await self._query(Q.CONFIG_VALUES.format(names="'user connections', 'max server memory (MB)'"))
        }
        limit = cfg.get("user connections") or 0
        if limit > 0:
            g["connections.max"] = limit
            if sc:
                g["connections.utilization"] = round(100.0 * float(sc["total"] or 0) / limit, 2)
        else:
            snap.unavailable["connections.utilization"] = (
                "no connection limit configured ('user connections' = 0, up to 32,767)"
            )
        counters = await self._try(Q.PERF_COUNTERS)
        if counters is None:
            snap.unavailable["transactions.per_sec"] = "performance counters need VIEW SERVER STATE"
        else:
            vals = {_counter_key(r): num(r["cntr_value"], 0) or 0.0 for r in counters}
            for key, metric in COUNTERS.items():
                if key in vals:
                    c[metric] = vals[key]
            for key, metric in GAUGES.items():
                if key in vals:
                    g[metric] = vals[key]
            lookups, reads = (
                vals.get(("Buffer Manager", "Page lookups/sec")),
                vals.get(("Buffer Manager", "Page reads/sec")),
            )
            if lookups is not None and reads is not None:
                c["io.blocks_hit"] = max(lookups - reads, 0.0)
            total_mem = vals.get(("Memory Manager", "Total Server Memory (KB)"))
            target_mem = vals.get(("Memory Manager", "Target Server Memory (KB)"))
            if total_mem is not None:
                g["memory.used_bytes"] = total_mem * 1024
            if target_mem is not None:
                g["memory.total_bytes"] = target_mem * 1024
        totals = await self._try_one(Q.QUERY_TOTALS)
        if totals and totals["calls"] is not None:
            # plan-cache totals give a consistent mean latency (Batch Requests/sec stays a separate counter)
            c["mssql.batch_requests"] = c.pop("query.calls", 0.0)
            c["query.calls"], c["query.total_ms"] = num(totals["calls"], 0) or 0.0, num(totals["total_ms"], 0) or 0.0
        if info["is_azure_db"]:
            rs = await self._try_one(Q.AZURE_RESOURCE_STATS)
            if rs:
                g["cpu.percent"] = num(rs["avg_cpu_percent"], 0) or 0.0
                g["mssql.data_io_percent"] = num(rs["avg_data_io_percent"], 0) or 0.0
                g["mssql.log_write_percent"] = num(rs["avg_log_write_percent"], 0) or 0.0
        else:
            cpu = await self._try_one(Q.CPU_RING_BUFFER)
            if cpu and cpu["system_idle"] is not None:
                g["cpu.percent"] = float(100 - int(cpu["system_idle"]))
                g["mssql.sql_cpu_percent"] = float(cpu["sql_cpu"] or 0)
            else:
                snap.unavailable["cpu.percent"] = "scheduler monitor ring buffer needs VIEW SERVER STATE"
        for r in await self._try(Q.CURRENT_WAITS) or []:
            snap.labeled.append(("wait.active_sessions", {"type": text(r["wait_type"]) or "?"}, float(r["n"])))
        if slow and not info["is_azure_db"]:
            size = await self._try_one(Q.DATA_SIZE)
            if size and size["bytes"] is not None:
                g["storage.database_bytes"] = float(size["bytes"])
            logs = await self._try(Q.LOG_SPACE)
            if logs:
                pct = [num(r.get("log space used (%)")) for r in logs]
                pct = [p for p in pct if p is not None]
                if pct:
                    g["mssql.log_used_percent_max"] = max(pct)
        ag = await self._try(Q.AG_DATABASES) if info.get("is_hadr_enabled") else None
        if ag:
            lags = [
                num(r["secondary_lag_seconds"])
                for r in ag
                if not r["is_local"] and r["secondary_lag_seconds"] is not None
            ]
            local = [r for r in ag if r["is_local"]]
            g["replication.is_replica"] = 0.0
            if lags:
                g["replication.lag_seconds"] = max(lag for lag in lags if lag is not None)
            g["replication.replica_count"] = float(
                len({text(r["replica_server_name"]) for r in ag if not r["is_local"]})
            )
            sync = [r for r in local if (text(r["synchronization_health_desc"]) or "") != "HEALTHY"]
            g["mssql.ag_unhealthy_databases"] = float(len(sync))
        snap.unavailable.setdefault(
            "storage.utilization", "volume capacity needs sys.dm_os_volume_stats (not collected)"
        )
        return snap

    # ---- plan-cache query statistics --------------------------------------------------------
    async def get_query_statistics(self, limit: int = 100) -> list[QueryStat]:
        spills = "SUM(qs.total_spills) AS spills," if await self._version() >= 14 else "NULL AS spills,"
        try:
            try:
                rows = await self._query(Q.QUERY_STATS.format(spills=spills), [int(limit)])
            except AdapterObjectMissing:
                # total_spills arrived in 2017 CU3 / 2016 SP2; older builds lack the column
                rows = await self._query(Q.QUERY_STATS.format(spills="NULL AS spills,"), [int(limit)])
        except AdapterPermissionError as exc:
            raise AdapterNotSupported(
                Capability.QUERY_STATISTICS, "grant VIEW SERVER STATE to read sys.dm_exec_query_stats"
            ) from exc
        out = []
        for r in rows:
            calls = int(r["calls"] or 0)
            total = num(r["total_ms"], 0.0) or 0.0
            out.append(
                QueryStat(
                    query_id=text(r["query_hash"]) or "",
                    query_text=text(r["statement_text"]) or "",
                    database=text(r["database_name"]),
                    calls=calls,
                    total_ms=total,
                    mean_ms=total / calls if calls else 0.0,
                    max_ms=num(r["max_ms"]),
                    rows=int(num(r["rows_count"], 0) or 0),
                    cpu_ms=num(r["cpu_ms"]),
                    logical_reads=int(num(r["logical_reads"], 0) or 0),
                    physical_reads=int(num(r["physical_reads"], 0) or 0),
                    temp_blocks=int(num(r["spills"], 0) or 0) if r["spills"] is not None else None,
                    plans=int(num(r["plans"], 0) or 0) or None,
                    extra=jsonable(
                        {
                            "logical_writes": r["logical_writes"],
                            "last_execution": r["last_execution_time"],
                            "first_cached": r["first_cached"],
                            "source": "sys.dm_exec_query_stats (cumulative while the plan stays cached)",
                            "reads_unit": "8 KB pages",
                        }
                    ),
                )
            )
        return out

    # ---- replication / configuration / storage --------------------------------------------
    async def get_replication_status(self) -> ReplicationInfo:
        info = await self._server()
        if not info.get("is_hadr_enabled"):
            return ReplicationInfo(
                role="standalone",
                notes=["Always On availability groups are not enabled; log shipping and replication are not monitored"],
            )
        replicas = await self._try(Q.AG_REPLICAS)
        dbs = await self._try(Q.AG_DATABASES)
        if replicas is None:
            return ReplicationInfo(role="unknown", notes=["availability group DMVs need VIEW SERVER STATE"])
        local = [r for r in replicas if r["is_local"]]
        role = (
            "primary"
            if any((text(r["role_desc"]) or "") == "PRIMARY" for r in local)
            else ("replica" if local else "unknown")
        )
        lags = [num(r["secondary_lag_seconds"]) for r in dbs or [] if r["secondary_lag_seconds"] is not None]
        return ReplicationInfo(
            role=role,
            replicas=[jsonable(dict(r)) for r in replicas if not r["is_local"]],
            upstream=jsonable(dict(local[0])) if local and role == "replica" else None,
            lag_seconds=max(lag for lag in lags if lag is not None) if lags else None,
            slots=[jsonable(dict(r)) for r in dbs or []],
            notes=[f"availability groups: {', '.join(sorted({text(r['ag_name']) or '' for r in replicas}))}"],
        )

    async def get_configuration(self) -> list[ConfigParameter]:
        return [
            ConfigParameter(
                name=text(r["name"]) or "",
                value=text(r["value_in_use"]),
                category="Advanced" if r["is_advanced"] else "Server",
                description=f"{text(r['description'])} (range {text(r['min_value'])}–{text(r['max_value'])})",
                default_value=None,
                requires_restart=not bool(r["is_dynamic"]),
                pending_restart=text(r["configured_value"]) != text(r["value_in_use"]),
            )
            for r in await self._query(Q.CONFIG)
        ]

    async def get_storage_metrics(self) -> StorageInfo:
        dbs = await self.list_databases()  # type: ignore[attr-defined]
        notes = []
        logs = {}
        try:
            for r in await self._query(Q.LOG_SPACE):
                logs[text(r.get("database name")) or ""] = jsonable(dict(r))
        except (AdapterPermissionError, AdapterObjectMissing):
            notes.append("log space usage (DBCC SQLPERF) needs VIEW SERVER STATE")
        top: list[dict[str, Any]] = []
        if self.spec.database:
            top = [
                jsonable(dict(r))
                for r in await self._try(
                    """
                    SELECT TOP (20) s.name AS [schema], o.name, SUM(ps.used_page_count) * 8192 AS total_bytes,
                           SUM(CASE WHEN ps.index_id IN (0, 1) THEN ps.used_page_count ELSE 0 END) * 8192 AS table_bytes,
                           SUM(CASE WHEN ps.index_id > 1 THEN ps.used_page_count ELSE 0 END) * 8192 AS index_bytes,
                           SUM(CASE WHEN ps.index_id IN (0, 1) THEN ps.row_count ELSE 0 END) AS row_estimate
                    FROM sys.dm_db_partition_stats ps
                    JOIN sys.objects o ON o.object_id = ps.object_id AND o.type = 'U' AND o.is_ms_shipped = 0
                    JOIN sys.schemas s ON s.schema_id = o.schema_id
                    GROUP BY s.name, o.name
                    ORDER BY SUM(ps.used_page_count) DESC
                    """,
                    database=self.spec.database,
                )
                or []
            ]
            notes.append(f"largest tables are for database {self.spec.database!r}")
        return StorageInfo(
            total_bytes=sum(d.size_bytes or 0 for d in dbs),
            databases=[
                {
                    "name": d.name,
                    "size_bytes": d.size_bytes,
                    "data_bytes": d.extra.get("data_bytes"),
                    "log_bytes": d.extra.get("log_bytes"),
                    "log_used_percent": (logs.get(d.name) or {}).get("log space used (%)"),
                    "recovery_model": d.extra.get("recovery_model"),
                    "log_reuse_wait": d.extra.get("log_reuse_wait"),
                }
                for d in dbs
            ],
            tablespaces=[],
            top_tables=top,
            notes=notes + ["SQL Server stores data in filegroups/files; tablespaces do not apply"],
        )
