"""Normalized metric catalog and counter → rate derivation."""

from __future__ import annotations

from typing import Any

# name: (label, unit, category, description)
CATALOG: dict[str, tuple[str, str, str, str]] = {
    "availability.up": ("Availability", "bool", "availability", "1 when the last collection connected successfully"),
    "uptime.seconds": ("Uptime", "s", "availability", "Time since the database server started"),
    "connections.total": ("Connections", "count", "connections", "Client connections"),
    "connections.active": ("Active sessions", "count", "connections", "Sessions currently executing"),
    "connections.idle": ("Idle sessions", "count", "connections", "Connected but idle sessions"),
    "connections.idle_in_transaction": (
        "Idle in transaction",
        "count",
        "connections",
        "Idle sessions holding an open transaction",
    ),
    "connections.max": ("Max connections", "count", "connections", "Configured connection limit"),
    "connections.utilization": ("Connection utilization", "%", "connections", "Connections as a share of the limit"),
    "sessions.blocked": ("Blocked sessions", "count", "locks", "Sessions waiting on another session's lock"),
    "sessions.waiting": ("Lock-waiting sessions", "count", "locks", "Sessions in a lock wait"),
    "locks.total": ("Locks held", "count", "locks", "Lock entries"),
    "locks.waiting": ("Locks waiting", "count", "locks", "Ungranted lock requests"),
    "deadlocks.count": ("Deadlocks", "count", "locks", "Deadlocks since the previous collection"),
    "transactions.per_sec": ("Transactions/s", "1/s", "throughput", "Commits + rollbacks per second"),
    "commits.per_sec": ("Commits/s", "1/s", "throughput", "Commits per second"),
    "rollbacks.per_sec": ("Rollbacks/s", "1/s", "throughput", "Rollbacks per second"),
    "transactions.longest_seconds": ("Longest transaction", "s", "throughput", "Age of the oldest open transaction"),
    "query.mean_latency_ms": (
        "Mean query latency",
        "ms",
        "queries",
        "Average statement execution time in the interval",
    ),
    "query.calls_per_sec": ("Statements/s", "1/s", "queries", "Statement executions per second"),
    "query.longest_running_seconds": (
        "Longest running query",
        "s",
        "queries",
        "Runtime of the longest active statement",
    ),
    "cache.hit_ratio": ("Cache hit ratio", "%", "io", "Share of block requests served from the buffer cache"),
    "io.blocks_read_per_sec": ("Physical reads/s", "1/s", "io", "Blocks read from storage per second"),
    "temp.bytes_per_sec": ("Temp written/s", "B/s", "io", "Temporary file bytes written per second"),
    "rows.returned.per_sec": ("Rows read/s", "1/s", "throughput", "Rows returned by scans per second"),
    "rows.modified.per_sec": ("Rows modified/s", "1/s", "throughput", "Rows inserted/updated/deleted per second"),
    "storage.database_bytes": ("Database size", "B", "storage", "Total size of all databases"),
    "storage.capacity_bytes": ("Storage capacity", "B", "storage", "Allocated capacity of the data volume / storage"),
    "storage.utilization": ("Storage used", "%", "storage", "Used share of the data volume / allocated storage"),
    "replication.lag_seconds": (
        "Replication lag",
        "s",
        "replication",
        "Replay delay of the replica (or worst replica)",
    ),
    "replication.replica_count": ("Replicas", "count", "replication", "Connected replicas"),
    "replication.is_replica": ("Is replica", "bool", "replication", "1 when the server is a replica"),
    "cpu.percent": ("CPU", "%", "host", "Host / instance CPU utilization (engine-reported)"),
    "memory.used_bytes": ("Memory used", "B", "host", "Memory used (engine-reported)"),
    "memory.total_bytes": ("Memory total", "B", "host", "Memory available to the instance"),
}

# counter -> derived gauge
RATE_DERIVATIONS: dict[str, str] = {
    "transactions.commits": "commits.per_sec",
    "transactions.rollbacks": "rollbacks.per_sec",
    "io.blocks_read": "io.blocks_read_per_sec",
    "temp.bytes": "temp.bytes_per_sec",
    "query.calls": "query.calls_per_sec",
}
DELTA_DERIVATIONS: dict[str, str] = {"deadlocks": "deadlocks.count"}
#: cumulative host CPU time pairs (Oracle V$OSSTAT): cpu.percent is derived from their deltas
CPU_TIME_COUNTERS = ("cpu.busy_time_cs", "cpu.idle_time_cs")
SILENT_COUNTERS = {"io.blocks_hit", "query.total_ms", *CPU_TIME_COUNTERS}

DASHBOARD_METRICS = [
    "connections.total",
    "connections.active",
    "connections.utilization",
    "transactions.per_sec",
    "cache.hit_ratio",
    "query.mean_latency_ms",
    "sessions.blocked",
    "replication.lag_seconds",
    "storage.database_bytes",
    "cpu.percent",
]


def derive(
    counters: dict[str, float], prev: dict[str, Any] | None, now_ts: float
) -> tuple[dict[str, float], dict[str, Any]]:
    """Turn cumulative counters into rates using the previous collection state."""
    out: dict[str, float] = {}
    state = {"ts": now_ts, "counters": counters}
    if not prev or "counters" not in prev:
        return out, state
    dt = now_ts - float(prev.get("ts", 0))
    if dt <= 0 or dt > 3600:
        return out, state
    pc: dict[str, float] = prev["counters"]

    def delta(k: str) -> float | None:
        if k not in counters or k not in pc:
            return None
        d = counters[k] - pc[k]
        return d if d >= 0 else None  # counter reset

    for k in counters:
        d = delta(k)
        if d is None:
            continue
        if k in RATE_DERIVATIONS:
            out[RATE_DERIVATIONS[k]] = round(d / dt, 4)
        elif k in DELTA_DERIVATIONS:
            out[DELTA_DERIVATIONS[k]] = d
        elif k not in SILENT_COUNTERS:
            out[f"{k}.per_sec"] = round(d / dt, 4)
    if "commits.per_sec" in out or "rollbacks.per_sec" in out:
        out["transactions.per_sec"] = round(out.get("commits.per_sec", 0) + out.get("rollbacks.per_sec", 0), 4)
    hit, read = delta("io.blocks_hit"), delta("io.blocks_read")
    if hit is not None and read is not None and hit + read > 0:
        out["cache.hit_ratio"] = round(100 * hit / (hit + read), 3)
    busy, idle = delta(CPU_TIME_COUNTERS[0]), delta(CPU_TIME_COUNTERS[1])
    if busy is not None and idle is not None and busy + idle > 0:
        out["cpu.percent"] = round(100 * busy / (busy + idle), 2)
    calls, total = delta("query.calls"), delta("query.total_ms")
    if calls and total is not None and calls > 0:
        out["query.mean_latency_ms"] = round(total / calls, 4)
    return out, state


def compute_health(latest: dict[str, float]) -> tuple[str, list[str]]:
    reasons_c: list[str] = []
    reasons_w: list[str] = []
    if latest.get("availability.up", 1) < 1:
        return "unavailable", ["server unreachable"]
    util = latest.get("connections.utilization")
    if util is not None:
        if util >= 95:
            reasons_c.append(f"connections at {util:.0f}% of limit")
        elif util >= 80:
            reasons_w.append(f"connections at {util:.0f}% of limit")
    lag = latest.get("replication.lag_seconds")
    if lag is not None:
        if lag >= 300:
            reasons_c.append(f"replication lag {lag:.0f}s")
        elif lag >= 60:
            reasons_w.append(f"replication lag {lag:.0f}s")
    blocked = latest.get("sessions.blocked")
    if blocked is not None and blocked >= 5:
        reasons_c.append(f"{blocked:.0f} blocked sessions")
    elif blocked:
        reasons_w.append(f"{blocked:.0f} blocked sessions")
    hit = latest.get("cache.hit_ratio")
    if hit is not None and hit < 90:
        reasons_w.append(f"cache hit ratio {hit:.1f}%")
    storage = latest.get("storage.utilization")
    if storage is not None and storage >= 95:
        reasons_c.append(f"storage {storage:.0f}% full")
    elif storage is not None and storage >= 90:
        reasons_w.append(f"storage {storage:.0f}% full")
    cpu = latest.get("cpu.percent")
    if cpu is not None and cpu >= 95:
        reasons_c.append(f"CPU {cpu:.0f}%")
    elif cpu is not None and cpu >= 85:
        reasons_w.append(f"CPU {cpu:.0f}%")
    if reasons_c:
        return "critical", reasons_c + reasons_w
    if reasons_w:
        return "warning", reasons_w
    return "healthy", []


def describe(metric: str) -> dict[str, str]:
    label, unit, category, desc = CATALOG.get(metric, (metric, "", metric.split(".")[0], ""))
    return {"name": metric, "label": label, "unit": unit, "category": category, "description": desc}
