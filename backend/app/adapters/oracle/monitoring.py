"""Oracle monitoring with views that need no management pack: sessions, blocking, locks,
V$SYSSTAT counters, host CPU from V$OSSTAT, wait classes, memory, V$SQLSTATS, Data Guard,
parameters and tablespaces."""

from __future__ import annotations

import re
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
from app.adapters.dbapi import jsonable, num, text, utc
from app.adapters.oracle import sql as Q
from app.adapters.oracle.base import OracleBase

LOCK_MODES = {
    0: "none",
    1: "null",
    2: "row share (RS)",
    3: "row exclusive (RX)",
    4: "share (S)",
    5: "share row exclusive (SRX)",
    6: "exclusive (X)",
}
#: cumulative V$SYSSTAT statistics → platform counter keys
SYSSTAT = {
    "user commits": "transactions.commits",
    "user rollbacks": "transactions.rollbacks",
    "execute count": "query.calls",
    "physical reads": "io.blocks_read",
    "enqueue deadlocks": "deadlocks",
    "parse count (total)": "oracle.parse_count",
    "parse count (hard)": "oracle.hard_parses",
    "redo size": "oracle.redo_bytes",
    "user calls": "oracle.user_calls",
    "physical writes": "oracle.physical_writes",
    "sorts (disk)": "oracle.sorts_disk",
    "sorts (memory)": "oracle.sorts_memory",
    "table scans (long tables)": "oracle.long_table_scans",
    "logons cumulative": "oracle.logons",
    "physical read total bytes": "oracle.read_bytes",
    "physical write total bytes": "oracle.write_bytes",
}
_INTERVAL = re.compile(r"^\+?(\d+)\s+(\d+):(\d+):(\d+)(?:\.\d+)?$")


def interval_seconds(v: str | None) -> float | None:
    """V$DATAGUARD_STATS values are INTERVAL DAY TO SECOND strings: ``+00 00:00:05``."""
    m = _INTERVAL.match((v or "").strip())
    if not m:
        return None
    d, h, mi, s = (int(x) for x in m.groups())
    return float(d * 86400 + h * 3600 + mi * 60 + s)


def session_key(sid: Any, serial: Any) -> str:
    return f"{int(sid)},{int(serial)}"


class OracleMonitoringMixin(OracleBase):
    async def _container_names(self) -> dict[int, str]:
        async def load() -> dict[int, str]:
            return {int(r["con_id"]): text(r["name"]) or "" for r in await self._try(Q.CONTAINERS) or []}

        return await self._memoized("container_names", load)

    # ---- sessions & locks -----------------------------------------------------------------------
    async def get_sessions(self) -> list[SessionInfo]:
        try:
            rows = await self._query(Q.SESSIONS)
        except AdapterPermissionError as exc:
            raise AdapterNotSupported(
                Capability.SESSIONS, "grant SELECT_CATALOG_ROLE (V$SESSION) to the monitoring user"
            ) from exc
        names = await self._container_names()
        serial_of = {int(r["sid"]): r["serial#"] for r in rows}
        out = []
        for r in rows:
            user_proc = text(r["type"]) == "USER"
            active = text(r["status"]) == "ACTIVE"
            in_txn = r["xact_start"] is not None
            state = (
                "background"
                if not user_proc
                else ("active" if active else ("idle in transaction" if in_txn else "idle"))
            )
            blocker = r["final_blocking_session"] or r["blocking_session"]
            waiting = text(r["state"]) == "WAITING" and text(r["wait_class"]) != "Idle"
            out.append(
                SessionInfo(
                    session_id=session_key(r["sid"], r["serial#"]),
                    username=text(r["username"]),
                    database=names.get(int(r["con_id"] or 0)) if r["con_id"] else None,
                    application=text(r["program"]) or text(r["module"]),
                    client_address=text(r["machine"]),
                    state=state,
                    query=text(r["sql_text"]) if active and user_proc else None,
                    query_start=utc(r["sql_exec_start"]) if active else None,
                    backend_start=utc(r["logon_time"]),
                    xact_start=utc(r["xact_start"]),
                    duration_seconds=float(r["last_call_et"] or 0) if active else None,
                    wait_event_type=text(r["wait_class"]) if waiting else None,
                    wait_event=text(r["event"]) if waiting else None,
                    blocked_by=[session_key(blocker, serial_of[int(blocker)])]
                    if blocker and int(blocker) in serial_of
                    else ([str(int(blocker))] if blocker else []),
                    backend_type="client" if user_proc else text(r["program"]),
                    ssl=None,
                )
            )
        return out

    async def get_locks(self) -> list[LockInfo]:
        try:
            rows = await self._query(Q.LOCKS)
        except AdapterPermissionError as exc:
            raise AdapterNotSupported(
                Capability.LOCKS, "grant SELECT_CATALOG_ROLE (V$LOCK) to the monitoring user"
            ) from exc
        serial_of = {int(r["sid"]): r["serial#"] for r in rows}
        out = []
        for r in rows:
            granted = int(r["lmode"] or 0) > 0 and int(r["request"] or 0) == 0
            mode = int(r["lmode"] or 0) if granted else int(r["request"] or 0)
            blocker = r["blocking_session"]
            relation = f"{text(r['owner'])}.{text(r['object_name'])}" if r["object_name"] else None
            out.append(
                LockInfo(
                    session_id=session_key(r["sid"], r["serial#"]),
                    lock_type=text(r["type"]) or "",
                    mode=LOCK_MODES.get(mode, str(mode)),
                    granted=granted,
                    relation=relation or (f"{text(r['type'])} {r['id1']},{r['id2']}"),
                    username=text(r["username"]),
                    waiting_seconds=float(r["ctime"] or 0) if not granted else None,
                    blocked_by=[session_key(blocker, serial_of[int(blocker)])]
                    if blocker and int(blocker) in serial_of and not granted
                    else [],
                )
            )
        return out

    async def cancel_session(self, session_id: str, terminate: bool = False) -> bool:
        if not re.fullmatch(r"\d+,\d+", session_id):
            raise AdapterNotSupported(Capability.SESSION_CANCEL, "Oracle session ids have the form 'sid,serial#'")
        if terminate:
            await self._query(f"ALTER SYSTEM KILL SESSION '{session_id}' IMMEDIATE")
            return True
        if await self._version() < 18:
            raise AdapterNotSupported(
                Capability.SESSION_CANCEL, "ALTER SYSTEM CANCEL SQL needs Oracle 18c or later; terminate instead"
            )
        await self._query(f"ALTER SYSTEM CANCEL SQL '{session_id}'")
        return True

    # ---- metrics -----------------------------------------------------------------------------
    async def get_performance_metrics(self, slow: bool = True) -> MetricSnapshot:
        snap = MetricSnapshot(collected_at=datetime.now(UTC))
        g, c = snap.gauges, snap.counters
        info = await self._server()
        g["availability.up"] = 1.0
        if not info["v_views"]:
            snap.unavailable["connections.total"] = "grant SELECT_CATALOG_ROLE (V$ views) to the monitoring user"
            return snap
        sc = await self._one(Q.SESSION_COUNTS)
        if sc:
            total = float(sc["total"] or 0)
            g.update(
                {
                    "connections.total": total,
                    "connections.active": float(sc["active"] or 0),
                    "connections.idle": total - float(sc["active"] or 0),
                    "connections.idle_in_transaction": float(sc["idle_in_txn"] or 0),
                    "sessions.blocked": float(sc["blocked"] or 0),
                    "sessions.waiting": float(sc["blocked"] or 0),
                    "query.longest_running_seconds": float(sc["longest_call_s"] or 0),
                }
            )
        lt = await self._try_one(Q.LONGEST_TXN)
        if lt and lt["seconds"] is not None:
            g["transactions.longest_seconds"] = float(lt["seconds"])
        for r in await self._try(Q.RESOURCE_LIMIT) or []:
            name, limit = text(r["resource_name"]), (text(r["limit_value"]) or "").strip()
            if name == "sessions" and limit.isdigit() and int(limit) > 0:
                g["connections.max"] = float(limit)
                g["connections.utilization"] = round(100.0 * float(r["current_utilization"] or 0) / int(limit), 2)
            elif name == "processes" and limit.isdigit() and int(limit) > 0:
                g["oracle.processes_utilization"] = round(100.0 * float(r["current_utilization"] or 0) / int(limit), 2)
        stats = {text(r["name"]): num(r["value"], 0) or 0.0 for r in await self._query(Q.SYSSTAT)}
        for name, key in SYSSTAT.items():
            if name in stats:
                c[key] = stats[name]
        if "DB time" in stats:
            c["query.total_ms"] = stats["DB time"] * 10.0  # centiseconds
        logical = stats.get("db block gets", 0.0) + stats.get("consistent gets", 0.0)
        if logical:
            c["io.blocks_hit"] = max(logical - stats.get("physical reads", 0.0), 0.0)
        os = {text(r["stat_name"]): num(r["value"]) for r in await self._try(Q.CPU_COUNT) or []}
        if os.get("BUSY_TIME") is not None and os.get("IDLE_TIME") is not None:
            # cumulative centiseconds; the collector derives cpu.percent from consecutive samples
            c["cpu.busy_time_cs"], c["cpu.idle_time_cs"] = float(os["BUSY_TIME"]), float(os["IDLE_TIME"])
        sga = await self._try_one(Q.SGA)
        pga = {text(r["name"]): num(r["value"]) for r in await self._try(Q.PGASTAT) or []}
        if sga and sga["bytes"] is not None:
            g["oracle.sga_used_bytes"] = float(sga["bytes"])
        if pga.get("total PGA allocated") is not None:
            g["oracle.pga_used_bytes"] = float(pga["total PGA allocated"] or 0)
        if "oracle.sga_used_bytes" in g:
            g["memory.used_bytes"] = g["oracle.sga_used_bytes"] + g.get("oracle.pga_used_bytes", 0.0)
        if os.get("PHYSICAL_MEMORY_BYTES"):
            g["memory.total_bytes"] = float(os["PHYSICAL_MEMORY_BYTES"] or 0)
        for r in await self._try(Q.WAITING_BY_CLASS) or []:
            snap.labeled.append(("wait.active_sessions", {"type": text(r["wait_class"]) or "?"}, float(r["n"])))
        started = utc(info.get("startup_time"))
        if started:
            g["uptime.seconds"] = (datetime.now(UTC) - started).total_seconds()
        role = text(info.get("database_role")) or ""
        g["replication.is_replica"] = 0.0 if role in ("", "PRIMARY") else 1.0
        if role and role != "PRIMARY":
            lag = await self._replica_lag()
            if lag is not None:
                g["replication.lag_seconds"] = lag
        elif role == "PRIMARY":
            dests = await self._try(Q.ARCHIVE_DESTS)
            if dests is not None:
                g["replication.replica_count"] = float(len(dests))
        if slow:
            size = await self._try_one(Q.DATAFILE_BYTES)
            if size and size["bytes"] is not None:
                g["storage.database_bytes"] = float(size["bytes"])
            fra = await self._try_one(Q.FRA)
            if fra and num(fra["space_limit"]):
                used = (num(fra["space_used"], 0) or 0) - (num(fra["space_reclaimable"], 0) or 0)
                g["oracle.fra_used_percent"] = round(100.0 * used / (num(fra["space_limit"]) or 1), 2)
            ts = await self._try(Q.TABLESPACES)
            if ts:
                perm = [num(t["used_percent"], 0) or 0 for t in ts if text(t["contents"]) == "PERMANENT"]
                if perm:
                    g["oracle.tablespace_used_percent_max"] = round(max(perm), 2)
        snap.unavailable.setdefault("storage.utilization", "file system capacity is not visible through SQL")
        return snap

    async def _replica_lag(self) -> float | None:
        rows = await self._try(Q.DATAGUARD_STATS) or []
        vals = {text(r["name"]): interval_seconds(text(r["value"])) for r in rows}
        lags = [v for k, v in vals.items() if k in ("apply lag", "transport lag") and v is not None]
        return max(lags) if lags else None

    # ---- query statistics ----------------------------------------------------------------------
    async def get_query_statistics(self, limit: int = 100) -> list[QueryStat]:
        try:
            rows = await self._query(Q.QUERY_STATS, {"lim": int(limit)})
        except AdapterPermissionError as exc:
            raise AdapterNotSupported(
                Capability.QUERY_STATISTICS, "grant SELECT_CATALOG_ROLE (V$SQLSTATS) to the monitoring user"
            ) from exc
        names = await self._container_names()
        out = []
        for r in rows:
            calls = int(num(r["executions"], 0) or 0)
            total = num(r["total_ms"], 0.0) or 0.0
            out.append(
                QueryStat(
                    query_id=text(r["sql_id"]) or "",
                    query_text=text(r["sql_text"]) or "",
                    database=names.get(int(r["con_id"] or 0)) if r["con_id"] else None,
                    calls=calls,
                    total_ms=total,
                    mean_ms=total / calls if calls else 0.0,
                    rows=int(num(r["rows_processed"], 0) or 0),
                    cpu_ms=num(r["cpu_ms"]),
                    logical_reads=int(num(r["buffer_gets"], 0) or 0),
                    physical_reads=int(num(r["disk_reads"], 0) or 0),
                    lock_wait_ms=num(r["app_wait_ms"]),
                    plans=int(num(r["plans"], 0) or 0) or None,
                    plan_id=int(num(r["top_plan"], 0) or 0) or None,
                    extra=jsonable(
                        {
                            "plans": r["plans"],
                            "concurrency_wait_ms": r["concurrency_wait_ms"],
                            "user_io_wait_ms": r["io_wait_ms"],
                            "direct_writes": r["direct_writes"],
                            "last_active": r["last_active_time"],
                            "source": "V$SQLSTATS (cumulative while the cursor stays in the shared pool)",
                            "reads_unit": "blocks",
                            "lock_wait_source": "application wait class (includes row-lock enqueue waits)",
                        }
                    ),
                )
            )
        return out

    # ---- replication / configuration / storage -------------------------------------------------
    async def get_replication_status(self) -> ReplicationInfo:
        info = await self._server()
        role = text(info.get("database_role"))
        if role is None:
            return ReplicationInfo(role="unknown", notes=["V$DATABASE is not readable by the monitoring user"])
        notes = [f"database role: {role}", f"protection mode: {text(info.get('protection_mode'))}"]
        if role == "PRIMARY":
            dests = await self._try(Q.ARCHIVE_DESTS)
            if dests is None:
                return ReplicationInfo(role="unknown", notes=[*notes, "V$ARCHIVE_DEST_STATUS is not readable"])
            if not dests:
                return ReplicationInfo(
                    role="standalone",
                    notes=[*notes, "no remote redo transport destination (Data Guard not configured)"],
                )
            return ReplicationInfo(role="primary", replicas=[jsonable(dict(d)) for d in dests], notes=notes)
        stats = await self._try(Q.DATAGUARD_STATS) or []
        return ReplicationInfo(
            role="replica",
            lag_seconds=await self._replica_lag(),
            slots=[jsonable(dict(r)) for r in stats],
            notes=[*notes, "Active Data Guard (read-only open with apply) requires its own license"],
        )

    async def get_configuration(self) -> list[ConfigParameter]:
        pending = {text(r["name"]) for r in await self._try(Q.SPFILE_PENDING) or []}
        return [
            ConfigParameter(
                name=text(r["name"]) or "",
                value=text(r["display_value"]) if r["display_value"] is not None else text(r["value"]),
                category=None,
                description=text(r["description"]),
                source="default"
                if text(r["isdefault"]) == "TRUE"
                else ("modified" if text(r["ismodified"]) != "FALSE" else "spfile/pfile"),
                requires_restart=text(r["issys_modifiable"]) == "FALSE",
                pending_restart=text(r["name"]) in pending,
                is_default=text(r["isdefault"]) == "TRUE",
            )
            for r in await self._query(Q.PARAMETERS)
        ]

    async def get_storage_metrics(self) -> StorageInfo:
        ts = await self._try(Q.TABLESPACES)
        notes: list[str] = []
        if ts is None:
            notes.append("tablespace usage needs SELECT on DBA_TABLESPACE_USAGE_METRICS (SELECT_CATALOG_ROLE)")
        dbs = await self.list_databases()  # type: ignore[attr-defined]
        temp = await self._try_one(Q.TEMP_USAGE)
        redo = await self._try(Q.REDO_LOGS) or []
        fra = await self._try_one(Q.FRA)
        info = await self._server()
        if info["in_root"]:
            notes.append("tablespaces are those of CDB$ROOT; connect through a PDB service for its tablespaces")
        return StorageInfo(
            total_bytes=sum(d.size_bytes or 0 for d in dbs if d.name != "PDB$SEED") or None,
            databases=[
                {"name": d.name, "size_bytes": d.size_bytes, **{k: d.extra.get(k) for k in ("type", "open_mode")}}
                for d in dbs
            ],
            tablespaces=[
                {
                    "name": text(t["name"]),
                    "contents": text(t["contents"]),
                    "status": text(t["status"]),
                    "bigfile": text(t["bigfile"]) == "YES",
                    "encrypted": text(t["encrypted"]) == "YES",
                    "used_bytes": jsonable(t["used_bytes"]),
                    "max_bytes": jsonable(t["max_bytes"]),
                    "allocated_bytes": jsonable(t["allocated_bytes"]),
                    "used_pct": round(num(t["used_percent"], 0) or 0, 2),
                    "autoextend": bool(t["autoextend"]),
                }
                for t in ts or []
            ],
            top_tables=[
                jsonable({"schema": r["schema_name"], **{k: v for k, v in dict(r).items() if k != "schema_name"}})
                for r in await self._try(Q.TOP_TABLES) or []
            ],
            temp={
                "in_use_bytes": jsonable(temp["bytes"]) if temp else None,
                "redo_log_bytes": sum(int(num(r["bytes"], 0) or 0) * int(r["members"] or 1) for r in redo) or None,
                "fra": jsonable(dict(fra)) if fra else None,
            },
            notes=notes + ["used_pct is relative to the tablespace maximum size (autoextend limit included)"],
        )


__all__ = ["OracleMonitoringMixin", "interval_seconds"]
