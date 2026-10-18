"""Db2 LUW monitoring through MON_GET_* table functions and SYSIBMADM views."""

from __future__ import annotations

from datetime import UTC, datetime

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
from app.adapters.db2 import sql as Q
from app.adapters.db2.base import Db2Base
from app.adapters.dbapi import AdapterObjectMissing, jsonable, num, text, utc

LOCK_MODES = {
    "IN": "intent none",
    "IS": "intent share",
    "IX": "intent exclusive",
    "S": "share",
    "SIX": "share with intent exclusive",
    "U": "update",
    "X": "exclusive",
    "Z": "super exclusive",
    "NS": "next-key share",
    "NW": "next-key weak exclusive",
}
NO_MON = "grant EXECUTE on the MON_GET_* routines (or the SQLADM authority) to the monitoring user"


class Db2MonitoringMixin(Db2Base):
    # ---- sessions & locks -------------------------------------------------------------------------
    async def get_sessions(self) -> list[SessionInfo]:
        try:
            conns = await self._query(Q.CONNECTIONS)
        except AdapterPermissionError as exc:
            raise AdapterNotSupported(Capability.SESSIONS, NO_MON) from exc
        current = {int(r["application_handle"]): r for r in await self._try(Q.CURRENT_SQL) or []}
        uows = {int(r["application_handle"]): r for r in await self._try(Q.UOWS) or []}
        waits: dict[int, list[str]] = {}
        for w in await self._try(Q.LOCK_WAITS) or []:
            waits.setdefault(int(w["req_application_handle"]), []).append(str(int(w["hld_application_handle"])))
        db = text((await self._server()).get("db_name"))
        out = []
        for r in conns:
            h = int(r["application_handle"])
            cur, uow = current.get(h), uows.get(h)
            wstate = (text(uow["workload_occurrence_state"]) or "") if uow else ""
            state = (
                "active"
                if cur
                else ("idle in transaction" if wstate == "UOWWAIT" and uow and uow["uow_start_time"] else "idle")
            )
            out.append(
                SessionInfo(
                    session_id=str(h),
                    username=text(r["session_auth_id"]) or text(r["system_auth_id"]),
                    database=db,
                    application=text(r["application_name"]),
                    client_address=text(r["client_ipaddr"]) or text(r["client_hostname"]),
                    state=state,
                    query=text(cur["stmt_text"]) if cur else None,
                    duration_seconds=float(cur["elapsed_time_sec"] or 0) if cur else None,
                    backend_start=utc(r["connection_start_time"]),
                    xact_start=utc(uow["uow_start_time"]) if uow and wstate != "IDLE" else None,
                    wait_event_type="Lock" if h in waits else None,
                    wait_event="lock wait" if h in waits else (text(cur["activity_state"]) if cur else None),
                    blocked_by=waits.get(h, []),
                    backend_type="client",
                )
            )
        return out

    async def get_locks(self) -> list[LockInfo]:
        rows = await self._try(Q.LOCKS)
        if rows is None:
            raise AdapterNotSupported(Capability.LOCKS, NO_MON)
        blockers: dict[int, list[str]] = {}
        for w in await self._try(Q.LOCK_WAITS) or []:
            blockers.setdefault(int(w["req_application_handle"]), []).append(str(int(w["hld_application_handle"])))
        return [
            LockInfo(
                session_id=str(int(r["application_handle"])),
                lock_type=(text(r["lock_object_type"]) or "").lower(),
                mode=LOCK_MODES.get(text(r["lock_mode"]) or "", text(r["lock_mode"]) or ""),
                granted=text(r["lock_status"]) == "G",
                relation=f"{text(r['tabschema'])}.{text(r['tabname'])}" if r["tabname"] else text(r["lock_name"]),
                blocked_by=blockers.get(int(r["application_handle"]), []) if text(r["lock_status"]) != "G" else [],
            )
            for r in rows
        ]

    async def cancel_session(self, session_id: str, terminate: bool = False) -> bool:
        if not session_id.isdigit():
            raise AdapterNotSupported(Capability.SESSION_CANCEL, "Db2 application handles are numeric")
        handle = int(session_id)
        if terminate:
            await self._query(f"CALL SYSPROC.ADMIN_CMD('FORCE APPLICATION ({handle})')")
            return True
        if await self._version() < 11:
            raise AdapterNotSupported(Capability.SESSION_CANCEL, "MON_GET_ACTIVITY needs Db2 11.1 or later")
        acts = await self._query(Q.ACTIVITIES_OF, [handle])
        if not acts:
            raise AdapterNotSupported(Capability.SESSION_CANCEL, "the application has no running activity to cancel")
        for a in acts:
            await self._query(
                "CALL SYSPROC.WLM_CANCEL_ACTIVITY(?, ?, ?)", [handle, int(a["uow_id"]), int(a["activity_id"])]
            )
        return True

    # ---- metrics ---------------------------------------------------------------------------------
    async def get_performance_metrics(self, slow: bool = True) -> MetricSnapshot:
        snap = MetricSnapshot(collected_at=datetime.now(UTC))
        g, c = snap.gauges, snap.counters
        g["availability.up"] = 1.0
        n = await self._try_one(Q.CONNECTION_COUNT)
        if n is None:
            snap.unavailable["connections.total"] = NO_MON
            return snap
        total = float(n["n"] or 0)
        g["connections.total"] = total
        act = await self._try_one(Q.ACTIVE_COUNT)
        if act:
            g["connections.active"] = float(act["n"] or 0)
            g["connections.idle"] = max(total - float(act["n"] or 0), 0.0)
            g["query.longest_running_seconds"] = float(act["longest"] or 0)
        lw = await self._try_one(Q.LOCK_WAIT_COUNT)
        if lw:
            g["sessions.blocked"] = g["locks.waiting"] = g["sessions.waiting"] = float(lw["n"] or 0)
        cfg = {
            text(r["name"]): text(r["value"]) for r in await self._try(Q.DBCFG_VALUES.format(names="'maxappls'")) or []
        }
        maxappls = (cfg.get("maxappls") or "").strip()
        if maxappls.isdigit() and int(maxappls) > 0:
            g["connections.max"] = float(maxappls)
            g["connections.utilization"] = round(100.0 * total / int(maxappls), 2)
        else:
            snap.unavailable["connections.utilization"] = "MAXAPPLS is AUTOMATIC (no fixed connection limit)"
        w = await self._try_one(Q.WORKLOAD_TOTALS)
        if w:
            mapping = {
                "commits": "transactions.commits",
                "rollbacks": "transactions.rollbacks",
                "rows_read": "rows.returned",  # catalog meaning: rows read by scans
                "rows_returned": "db2.rows_returned_to_application",
                "rows_modified": "rows.modified",
                "p_reads": "io.blocks_read",
                "deadlocks": "deadlocks",
                "lock_timeouts": "db2.lock_timeouts",
                "lock_escals": "db2.lock_escalations_total",
                "lock_waits": "db2.lock_waits",
                "lock_wait_time": "db2.lock_wait_ms",
                "total_sorts": "db2.sorts",
                "sort_overflows": "db2.sort_overflows",
                "pkg_cache_inserts": "db2.pkg_cache_inserts",
                "pkg_cache_lookups": "db2.pkg_cache_lookups",
                "acts": "query.calls",
                "act_time_ms": "query.total_ms",
                "cpu_us": "db2.cpu_time_us",
                "temp_p_reads": "db2.temp_physical_reads",
            }
            for col, key in mapping.items():
                if w.get(col) is not None:
                    c[key] = float(num(w[col], 0) or 0)
            lr, pr = num(w.get("l_reads")), num(w.get("p_reads"))
            if lr is not None and pr is not None:
                c["io.blocks_hit"] = max(lr - pr, 0.0)
        bps = await self._try(Q.BUFFERPOOLS) or []
        lr = sum(num(b["l_reads"], 0) or 0 for b in bps)
        pr = sum(num(b["p_reads"], 0) or 0 for b in bps)
        if lr > 0:
            g["db2.bufferpool_hit_ratio"] = round(100.0 * (1 - pr / lr), 3)
        mem = await self._try_one(Q.MEMORY_SETS)
        if mem and mem["used_kb"] is not None:
            g["memory.used_bytes"] = float(mem["used_kb"]) * 1024
        cpu = await self._try_one(Q.SYSTEM_RESOURCES)
        if cpu and cpu["cpu_usage_total"] is not None:
            g["cpu.percent"] = float(cpu["cpu_usage_total"])
        else:
            snap.unavailable["cpu.percent"] = "SYSPROC.ENV_GET_SYSTEM_RESOURCES is not executable by this user"
        log = await self._try_one(Q.TRANSACTION_LOG)
        if log and log["used"] is not None:
            used, avail = float(log["used"] or 0), float(log["available"] or 0)
            g["db2.log_used_bytes"] = used
            if avail >= 0 and used + avail > 0:
                g["db2.log_used_percent"] = round(100.0 * used / (used + avail), 2)
        hadr = await self._try(Q.HADR) or []
        if hadr:
            role = text(hadr[0]["hadr_role"]) or ""
            g["replication.is_replica"] = 1.0 if role == "STANDBY" else 0.0
            g["replication.replica_count"] = float(len(hadr)) if role == "PRIMARY" else 0.0
            lags = [self._hadr_lag(h) for h in hadr]
            lags = [lag for lag in lags if lag is not None]
            if lags:
                g["replication.lag_seconds"] = max(lags)
        if slow:
            ts = await self._try(Q.TABLESPACES) or []
            if ts:
                g["storage.database_bytes"] = float(
                    sum(int(num(t["used_pages"], 0) or 0) * int(num(t["tbsp_page_size"], 0) or 0) for t in ts)
                )
        snap.unavailable.setdefault("storage.utilization", "file system capacity is not visible through SQL")
        return snap

    @staticmethod
    def _hadr_lag(h: dict) -> float | None:  # type: ignore[type-arg]
        p, s = utc(h.get("primary_log_time")), utc(h.get("standby_replay_log_time"))
        if p and s:
            return max((p - s).total_seconds(), 0.0)
        return None

    # ---- query statistics -------------------------------------------------------------------------
    async def get_query_statistics(self, limit: int = 100) -> list[QueryStat]:
        try:
            rows = await self._query(Q.PKG_CACHE.format(limit=max(1, min(int(limit), 1000))))
        except (AdapterPermissionError, AdapterObjectMissing) as exc:
            raise AdapterNotSupported(Capability.QUERY_STATISTICS, NO_MON) from exc
        db = text((await self._server()).get("db_name"))
        out = []
        for r in rows:
            calls = int(num(r["num_executions"], 0) or 0)
            total = num(r["stmt_exec_time"], 0.0) or 0.0
            out.append(
                QueryStat(
                    query_id=text(r["executable_id"]) or "",
                    query_text=text(r["stmt_text"]) or "",
                    database=db,
                    calls=calls,
                    total_ms=total,
                    mean_ms=total / calls if calls else 0.0,
                    rows=int(num(r["rows_returned"], 0) or 0),
                    cpu_ms=(num(r["total_cpu_time"], 0) or 0) / 1000.0,
                    logical_reads=int(num(r["logical_reads"], 0) or 0),
                    physical_reads=int(num(r["physical_reads"], 0) or 0),
                    lock_wait_ms=num(r["lock_wait_time"]),
                    extra=jsonable(
                        {
                            "section_type": "dynamic" if text(r["section_type"]) == "D" else "static",
                            "package": f"{text(r['package_schema'])}.{text(r['package_name'])}"
                            if r["package_name"]
                            else None,
                            "rows_read": r["rows_read"],
                            "rows_modified": r["rows_modified"],
                            "sorts": r["total_sorts"],
                            "sort_overflows": r["sort_overflows"],
                            "inserted": r["insert_timestamp"],
                            "last_metrics_update": r["last_metrics_update"],
                            "source": "MON_GET_PKG_CACHE_STMT (cumulative while the section stays in the package cache)",
                            "reads_unit": "pages",
                        }
                    ),
                )
            )
        return out

    # ---- replication / configuration / storage ------------------------------------------------------
    async def get_replication_status(self) -> ReplicationInfo:
        rows = await self._try(Q.HADR)
        if rows is None:
            return ReplicationInfo(role="unknown", notes=[f"MON_GET_HADR is not executable: {NO_MON}"])
        if not rows:
            return ReplicationInfo(role="standalone", notes=["HADR is not configured for this database"])
        role = text(rows[0]["hadr_role"]) or ""
        lags = [x for x in (self._hadr_lag(h) for h in rows) if x is not None]
        return ReplicationInfo(
            role="primary" if role == "PRIMARY" else ("replica" if role == "STANDBY" else "unknown"),
            replicas=[jsonable(dict(h)) for h in rows] if role == "PRIMARY" else [],
            upstream=jsonable(dict(rows[0])) if role == "STANDBY" else None,
            lag_seconds=max(lags) if lags else None,
            notes=[f"HADR {text(rows[0]['hadr_syncmode'])}, state {text(rows[0]['hadr_state'])}"],
        )

    async def get_configuration(self) -> list[ConfigParameter]:
        out = []
        for scope, sql in (("database", Q.DBCFG), ("instance", Q.DBMCFG)):
            rows = await self._try(sql)
            if rows is None:
                continue
            for r in rows:
                value, deferred = text(r["value"]), text(r["deferred_value"])
                out.append(
                    ConfigParameter(
                        name=f"{text(r['name'])}",
                        value=value,
                        category=scope,
                        source=(text(r["value_flags"]) or "").lower() or None,
                        pending_restart=deferred is not None and deferred != value,
                        description=f"{scope} configuration ({'DB CFG' if scope == 'database' else 'DBM CFG'}); type {text(r['datatype'])}",
                    )
                )
        if not out:
            raise AdapterNotSupported(Capability.CONFIGURATION, "SYSIBMADM.DBCFG/DBMCFG are not readable by this user")
        return out

    async def get_storage_metrics(self) -> StorageInfo:
        ts = await self._try(Q.TABLESPACES)
        notes = [] if ts is not None else [f"MON_GET_TABLESPACE: {NO_MON}"]
        log = await self._try_one(Q.TRANSACTION_LOG)
        tablespaces = []
        for t in ts or []:
            page = int(num(t["tbsp_page_size"], 0) or 0)
            used, usable = int(num(t["used_pages"], 0) or 0), int(num(t["usable_pages"], 0) or 0)
            tablespaces.append(
                {
                    "name": text(t["tbsp_name"]),
                    "type": text(t["tbsp_type"]),
                    "contents": text(t["tbsp_content_type"]),
                    "state": text(t["tbsp_state"]),
                    "used_bytes": used * page,
                    "allocated_bytes": int(num(t["total_pages"], 0) or 0) * page,
                    "used_pct": round(100.0 * used / usable, 2) if usable > 0 else None,
                    "autoextend": bool(t["auto_resize"]) or bool(t["auto_storage"]),
                }
            )
        total = sum(t["used_bytes"] for t in tablespaces)
        return StorageInfo(
            total_bytes=total or None,
            databases=[{"name": text((await self._server()).get("db_name")), "size_bytes": total or None}],
            tablespaces=tablespaces,
            temp={
                "log_used_bytes": jsonable(log["used"]) if log else None,
                "log_available_bytes": jsonable(log["available"]) if log else None,
            },
            notes=notes
            + [
                "SMS and automatic-storage tablespaces grow until the file system is full (used_pct is of allocated space)"
            ],
        )
