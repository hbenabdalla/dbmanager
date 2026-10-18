"""MySQL-family monitoring: sessions, locks, metrics, statement digests, replication,
configuration and storage."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.adapters.base import (
    AdapterError,
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
from app.adapters.dbapi import jsonable, num, text, utc
from app.adapters.mysql import sql as Q
from app.adapters.mysql.base import MySQLFamilyBase

GiB = 1024**3

STATUS_COUNTERS = {
    "com_commit": "transactions.commits",
    "com_rollback": "transactions.rollbacks",
    "innodb_buffer_pool_reads": "io.blocks_read",
    "innodb_rows_read": "rows.returned",
    "created_tmp_files": "temp.files",
    "created_tmp_disk_tables": "mysql.tmp_disk_tables",
    "created_tmp_tables": "mysql.tmp_tables",
    "slow_queries": "mysql.slow_queries",
    "aborted_connects": "mysql.aborted_connects",
    "aborted_clients": "mysql.aborted_clients",
    "innodb_row_lock_waits": "mysql.innodb_row_lock_waits",
    "innodb_data_read": "io.read_bytes",
    "innodb_data_written": "io.write_bytes",
    "bytes_received": "mysql.bytes_received",
    "bytes_sent": "mysql.bytes_sent",
    "handler_commit": "mysql.handler_commit",
    "select_full_join": "mysql.select_full_join",
    "sort_merge_passes": "mysql.sort_merge_passes",
    "table_open_cache_misses": "mysql.table_open_cache_misses",
    "questions": "mysql.questions",
}


def _f(status: dict[str, Any], key: str) -> float | None:
    return num(status.get(key))


class MySQLMonitoringMixin(MySQLFamilyBase):
    # ---- flavor hooks ---------------------------------------------------------------------
    async def _lock_wait_pairs(self) -> list[dict[str, Any]] | None:
        """[{waiting, blocking, wait_s, lock_table, lock_mode}] or None when not readable."""
        raise NotImplementedError

    async def _lock_rows(self) -> list[dict[str, Any]] | None:
        raise NotImplementedError

    async def _deadlock_counter(self, status: dict[str, Any]) -> float | None:
        raise NotImplementedError

    def _digest_extra_columns(self, version_num: int) -> str:
        return ""

    async def _configuration_rows(self) -> list[ConfigParameter] | None:
        return None

    async def _tablespaces(self) -> list[dict[str, Any]] | None:
        return None

    async def _instrumented_memory(self) -> float | None:
        return None

    def _replica_privilege_note(self) -> str:
        return "replica status needs the REPLICATION CLIENT privilege"

    def _replica_hosts_sql(self, version_num: int) -> str:
        return "SHOW REPLICAS" if self._has_show_replica(version_num) else "SHOW SLAVE HOSTS"

    async def _kill_statement(self, session_id: int, terminate: bool) -> str:
        return f"KILL {'CONNECTION' if terminate else 'QUERY'} {session_id}"

    # ---- sessions & locks -------------------------------------------------------------------
    async def get_sessions(self) -> list[SessionInfo]:
        rows = await self._query(Q.PROCESSLIST)
        trx = {int(r["thread_id"]): r for r in await self._try(Q.INNODB_TRX) or [] if r["thread_id"] is not None}
        blockers: dict[int, list[str]] = {}
        for w in await self._lock_wait_pairs() or []:
            blockers.setdefault(int(w["waiting"]), []).append(str(w["blocking"]))
        now = datetime.now(UTC)
        out = []
        for r in rows:
            sid = int(r["id"])
            cmd = text(r["command"]) or ""
            t = trx.get(sid)
            active = cmd not in ("Sleep", "Daemon", "Binlog Dump", "Binlog Dump GTID", "Connect")
            state = "active" if active else ("idle in transaction" if t and cmd == "Sleep" else "idle")
            if cmd in ("Daemon", "Binlog Dump", "Binlog Dump GTID") or text(r["user_name"]) in (
                "system user",
                "event_scheduler",
            ):
                state = "background"
            secs = num(r["time_s"])
            started = utc(t["started"]) if t else None
            out.append(
                SessionInfo(
                    session_id=str(sid),
                    username=text(r["user_name"]),
                    database=text(r["db"]),
                    client_address=text(r["host"]),
                    state=state,
                    query=text(r["info"]) if active else None,
                    duration_seconds=secs if active else None,
                    xact_start=started,
                    wait_event_type="Lock" if sid in blockers else None,
                    wait_event=(text(r["state"]) or None),
                    blocked_by=blockers.get(sid, []),
                    backend_type=cmd if state == "background" else "client",
                    query_start=None
                    if not active or secs is None
                    else datetime.fromtimestamp(now.timestamp() - secs, UTC),
                )
            )
        return out

    async def get_locks(self) -> list[LockInfo]:
        rows = await self._lock_rows()
        if rows is None:
            raise AdapterNotSupported(
                Capability.LOCKS, "lock details need the PROCESS privilege and performance_schema / INNODB_TRX access"
            )
        waits = await self._lock_wait_pairs() or []
        blocked: dict[str, list[str]] = {}
        for w in waits:
            blocked.setdefault(str(w["waiting"]), []).append(str(w["blocking"]))
        out = []
        for r in rows:
            sid = str(r.get("session_id") or "")
            out.append(
                LockInfo(
                    session_id=sid,
                    lock_type=text(r.get("lock_type")) or "",
                    mode=text(r.get("lock_mode")) or "",
                    granted=(text(r.get("lock_status")) or "GRANTED").upper() == "GRANTED",
                    database=text(r.get("object_schema")),
                    relation=".".join(x for x in (text(r.get("object_name")), text(r.get("index_name"))) if x) or None,
                    blocked_by=blocked.get(sid, []) if (text(r.get("lock_status")) or "").upper() != "GRANTED" else [],
                    waiting_seconds=num(r.get("wait_s")),
                )
            )
        return out

    async def cancel_session(self, session_id: str, terminate: bool = False) -> bool:
        if not session_id.isdigit():
            raise AdapterError("invalid session id")
        await self._query(await self._kill_statement(int(session_id), terminate))
        return True

    # ---- metrics ------------------------------------------------------------------------------
    async def get_performance_metrics(self, slow: bool = True) -> MetricSnapshot:
        snap = MetricSnapshot(collected_at=datetime.now(UTC))
        g, c = snap.gauges, snap.counters
        st = await self._status()
        info = await self._server()
        variables = await self._variables(["max_connections", "innodb_buffer_pool_size", "read_only"])
        max_conn = num(variables.get("max_connections")) or num(info.get("max_connections")) or 0
        connected = _f(st, "threads_connected") or 0
        running = _f(st, "threads_running") or 0
        g.update(
            {
                "availability.up": 1.0,
                "uptime.seconds": _f(st, "uptime") or 0.0,
                "connections.total": connected,
                "connections.active": running,
                "connections.idle": max(connected - running, 0.0),
                "connections.max": max_conn,
                "mysql.threads_running": running,
                "mysql.threads_cached": _f(st, "threads_cached") or 0.0,
                "mysql.max_used_connections": _f(st, "max_used_connections") or 0.0,
                "sessions.waiting": _f(st, "innodb_row_lock_current_waits") or 0.0,
                "sessions.blocked": _f(st, "innodb_row_lock_current_waits") or 0.0,
                "locks.waiting": _f(st, "innodb_row_lock_current_waits") or 0.0,
            }
        )
        if max_conn:
            g["connections.utilization"] = round(100.0 * connected / max_conn, 2)
        pages_total, pages_free = _f(st, "innodb_buffer_pool_pages_total"), _f(st, "innodb_buffer_pool_pages_free")
        if pages_total:
            g["mysql.innodb_buffer_pool_used_percent"] = round(
                100.0 * (pages_total - (pages_free or 0)) / pages_total, 2
            )
            dirty = _f(st, "innodb_buffer_pool_pages_dirty") or 0
            g["mysql.innodb_buffer_pool_dirty_percent"] = round(100.0 * dirty / pages_total, 2)
        bp = num(variables.get("innodb_buffer_pool_size"))
        if bp:
            g["mysql.innodb_buffer_pool_bytes"] = bp
        for key, metric in STATUS_COUNTERS.items():
            v = _f(st, key)
            if v is not None:
                c[metric] = v
        reqs, reads = _f(st, "innodb_buffer_pool_read_requests"), _f(st, "innodb_buffer_pool_reads")
        if reqs is not None and reads is not None:
            c["io.blocks_hit"] = max(reqs - reads, 0.0)
        if "rows.returned" not in c and _f(st, "rows_read") is not None:
            c["rows.returned"] = _f(st, "rows_read") or 0.0  # MariaDB: rows read by all engines
        mods = [_f(st, k) for k in ("innodb_rows_inserted", "innodb_rows_updated", "innodb_rows_deleted")]
        if all(v is not None for v in mods):
            c["rows.modified"] = sum(v for v in mods if v is not None)
        else:
            snap.unavailable["rows.modified.per_sec"] = (
                "this server version does not report InnoDB row modification counters"
            )
        dl = await self._deadlock_counter(st)
        if dl is not None:
            c["deadlocks"] = dl
        else:
            snap.unavailable["deadlocks.count"] = "deadlock counter needs the PROCESS privilege (INNODB_METRICS)"
        totals = await self._try_one(Q.DIGEST_TOTALS) if info.get("performance_schema") else None
        if totals and totals["calls"] is not None:
            c["query.calls"], c["query.total_ms"] = num(totals["calls"]) or 0.0, num(totals["total_ms"]) or 0.0
        else:
            q = _f(st, "questions")
            if q is not None:
                c["query.calls"] = q
            snap.unavailable["query.mean_latency_ms"] = (
                "statement latency needs performance_schema=ON and SELECT on performance_schema"
            )
        mem = await self._instrumented_memory()
        if mem is not None:
            g["memory.used_bytes"] = mem
        else:
            snap.unavailable["memory.used_bytes"] = "memory instrumentation not available"
        host_gb = num((self.spec.options or {}).get("host_memory_gb"))
        if host_gb:
            g["memory.total_bytes"] = host_gb * GiB
        repl = await self._replica_rows()
        if repl:
            g["replication.is_replica"] = 1.0
            lag = num(repl[0].get("seconds_behind_source", repl[0].get("seconds_behind_master")))
            if lag is not None:
                g["replication.lag_seconds"] = lag
            io_ok = (text(repl[0].get("replica_io_running", repl[0].get("slave_io_running"))) or "").lower() == "yes"
            sql_ok = (text(repl[0].get("replica_sql_running", repl[0].get("slave_sql_running"))) or "").lower() == "yes"
            g["mysql.replica_threads_running"] = float(io_ok) + float(sql_ok)
        elif repl is not None:
            g["replication.is_replica"] = 0.0
        wsrep = _f(st, "wsrep_cluster_size")
        if wsrep:
            g["mariadb.galera_cluster_size"] = wsrep
            g["mariadb.galera_ready"] = 1.0 if (text(st.get("wsrep_ready")) or "").upper() == "ON" else 0.0
            fc = _f(st, "wsrep_flow_control_paused")
            if fc is not None:
                g["mariadb.galera_flow_control_paused"] = fc
        if slow:
            size = await self._try_one(Q.TOTAL_SIZE)
            if size:
                g["storage.database_bytes"] = num(size["size_bytes"], 0) or 0.0
        snap.unavailable.setdefault("cpu.percent", f"{self.display_name} does not expose host CPU through SQL")
        snap.unavailable.setdefault("storage.utilization", "file-system capacity is not visible through SQL")
        return snap

    # ---- statement digests ----------------------------------------------------------------------
    async def get_query_statistics(self, limit: int = 100) -> list[QueryStat]:
        info = await self._server()
        if not info.get("performance_schema"):
            raise AdapterNotSupported(
                Capability.QUERY_STATISTICS,
                "performance_schema is OFF: set performance_schema=ON in the server configuration (requires a restart)",
            )
        try:
            rows = await self._query(
                Q.DIGEST_TOP.format(extra=self._digest_extra_columns(await self._version())), [int(limit)]
            )
        except AdapterPermissionError as exc:
            raise AdapterNotSupported(
                Capability.QUERY_STATISTICS, "grant SELECT ON performance_schema.* to the monitoring account"
            ) from exc
        out = []
        for r in rows:
            calls = int(r["calls"] or 0)
            total = num(r["total_ms"], 0.0) or 0.0
            out.append(
                QueryStat(
                    query_id=text(r["digest"]) or "",
                    query_text=text(r["digest_text"]) or "",
                    database=text(r["db"]),
                    calls=calls,
                    total_ms=total,
                    mean_ms=num(r["mean_ms"]) or (total / calls if calls else 0.0),
                    max_ms=num(r["max_ms"]),
                    rows=int((num(r["rows_sent"], 0) or 0) + (num(r["rows_affected"], 0) or 0)),
                    cpu_ms=num(r.get("cpu_ms")),
                    logical_reads=int(num(r["rows_examined"], 0) or 0),
                    temp_blocks=int(num(r["tmp_disk_tables"], 0) or 0),
                    lock_wait_ms=num(r["lock_ms"]),
                    extra=jsonable(
                        {
                            "rows_examined": r["rows_examined"],
                            "rows_sent": r["rows_sent"],
                            "rows_affected": r["rows_affected"],
                            "tmp_tables": r["tmp_tables"],
                            "tmp_disk_tables": r["tmp_disk_tables"],
                            "no_index_used": r["no_index_used"],
                            "full_joins": r["full_joins"],
                            "sort_merge_passes": r["sort_merge_passes"],
                            "errors": r["errors"],
                            "first_seen": r["first_seen"],
                            "last_seen": r["last_seen"],
                            "p95_ms": r.get("p95_ms"),
                            "reads_unit": "rows examined",
                        }
                    ),
                )
            )
        return out

    # ---- replication / configuration / storage --------------------------------------------------
    async def get_replication_status(self) -> ReplicationInfo:
        rows = await self._replica_rows()
        notes: list[str] = []
        if rows is None:
            notes.append(self._replica_privilege_note())
            rows = []
        st = await self._status()
        if rows:
            r = rows[0]

            def pick(*keys: str) -> Any:
                for k in keys:
                    if k in r:
                        return r[k]
                return None

            upstream = jsonable(
                {
                    "source_host": pick("source_host", "master_host"),
                    "source_port": pick("source_port", "master_port"),
                    "io_running": pick("replica_io_running", "slave_io_running"),
                    "sql_running": pick("replica_sql_running", "slave_sql_running"),
                    "seconds_behind": pick("seconds_behind_source", "seconds_behind_master"),
                    "last_io_error": pick("last_io_error") or None,
                    "last_sql_error": pick("last_sql_error") or None,
                    "retrieved_gtid_set": pick("retrieved_gtid_set") or None,
                    "executed_gtid_set": pick("executed_gtid_set") or None,
                    "gtid_io_pos": pick("gtid_io_pos") or None,
                    "channels": len(rows),
                }
            )
            return ReplicationInfo(
                role="replica",
                upstream=upstream,
                lag_seconds=num(upstream.get("seconds_behind")),
                notes=notes + ([] if len(rows) == 1 else [f"{len(rows)} replication channels; showing the first"]),
            )
        replicas: list[dict[str, Any]] = []
        v = await self._version()
        try:
            hosts = await self._try(self._replica_hosts_sql(v))
        except AdapterError:
            hosts = None
        if hosts is None:
            notes.append("listing connected replicas needs the REPLICATION SLAVE privilege")
        else:
            replicas = [jsonable(dict(h)) for h in hosts]
        if num(st.get("wsrep_cluster_size")):
            notes.append(
                f"Galera cluster: size {st.get('wsrep_cluster_size')}, state {st.get('wsrep_local_state_comment')}, "
                f"ready {st.get('wsrep_ready')}"
            )
        binlog = await self._variables(["log_bin", "gtid_mode", "binlog_format"])
        if (text(binlog.get("log_bin")) or "").upper() != "ON" and not replicas:
            notes.append("binary logging is disabled: this server cannot act as a replication source")
        return ReplicationInfo(
            role="primary" if replicas else "standalone",
            replicas=replicas,
            notes=notes + [f"{k} = {text(val)}" for k, val in binlog.items()],
        )

    async def get_configuration(self) -> list[ConfigParameter]:
        rows = await self._configuration_rows()
        if rows is not None:
            return rows
        return [
            ConfigParameter(
                name=text(r["variable_name"]) or "", value=text(r["value"]), category=_category(r["variable_name"])
            )
            for r in await self._query(Q.ALL_VARIABLES)
        ]

    async def get_storage_metrics(self) -> StorageInfo:
        dbs = await self.list_databases()
        ts = await self._tablespaces()
        st = await self._status()
        notes = ["sizes come from information_schema statistics (InnoDB estimates refreshed by ANALYZE TABLE)"]
        if ts is None:
            notes.append("InnoDB tablespace sizes need the PROCESS privilege")
        return StorageInfo(
            total_bytes=sum(d.size_bytes or 0 for d in dbs if not d.is_system),
            databases=[{"name": d.name, "size_bytes": d.size_bytes, "tables": d.extra.get("tables")} for d in dbs],
            tablespaces=ts or [],
            top_tables=[jsonable(dict(r)) for r in await self._query(Q.TOP_TABLES)],
            temp=jsonable(
                {
                    "created_tmp_tables": st.get("created_tmp_tables"),
                    "created_tmp_disk_tables": st.get("created_tmp_disk_tables"),
                    "created_tmp_files": st.get("created_tmp_files"),
                }
            ),
            notes=notes,
        )


def _category(name: Any) -> str:
    n = (text(name) or "").lower()
    for prefix, cat in (
        ("innodb_", "InnoDB"),
        ("ssl_", "Security / TLS"),
        ("tls_", "Security / TLS"),
        ("validate_password", "Security"),
        ("binlog", "Replication / binary log"),
        ("log_bin", "Replication / binary log"),
        ("gtid", "Replication / binary log"),
        ("replica_", "Replication / binary log"),
        ("slave_", "Replication / binary log"),
        ("wsrep_", "Galera"),
        ("performance_schema", "performance_schema"),
        ("query_cache", "Query cache"),
        ("max_", "Limits"),
        ("table_", "Tables / caches"),
        ("thread_", "Threads"),
        ("character_set", "Character sets"),
        ("collation", "Character sets"),
        ("log_", "Logging"),
        ("slow_query", "Logging"),
        ("general_log", "Logging"),
    ):
        if n.startswith(prefix):
            return cat
    return "General"
