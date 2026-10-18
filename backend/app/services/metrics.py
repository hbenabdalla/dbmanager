"""Metrics service: the read side of monitoring telemetry.

Frontend → Metrics API → **MetricsService** → TimeSeriesRepository → time-series database.

Callers pass the server ids the principal is allowed to see (authorization happens in the API
layer against PostgreSQL *before* this module builds a time-series query) and platform metric
keys (``connections.total``). This module translates keys to storage names, chooses the step
(query-time downsampling, never more than ~``TARGET_POINTS`` points per series), applies the
right semantics per metric type and shapes the result for the UI.

Metric-type semantics:

* gauge / distribution / rate → the requested aggregation per step (default ``avg``)
* counter → per-second ``rate`` per step (``increase`` on request); raw cumulative values are
  never charted because they are meaningless across resets.
"""

from __future__ import annotations

import asyncio
import math
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from app.core.errors import ValidationFailed
from app.timeseries.catalog import BY_KEY, QUERY_BY_FIELD, definition, name_for
from app.timeseries.model import MetricType, Rollup, Selector, from_ms
from app.timeseries.repository import TimeSeriesRepository, get_repository

RANGES: dict[str, int] = {
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "6h": 360,
    "12h": 720,
    "24h": 1440,
    "7d": 10080,
    "30d": 43200,
    "90d": 129600,
}
MAX_CUSTOM_RANGE = timedelta(days=400)
TARGET_POINTS = 240
STEPS = (30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400)
AGGREGATIONS: tuple[str, ...] = ("avg", "min", "max", "sum", "count", "last", "p50", "p95", "p99")
COUNTER_ROLLUPS: tuple[str, ...] = ("rate", "increase")
LABEL_KEYS = ("wait_type", "database", "tablespace")
GroupMode = Literal["sum", "avg", "max", "min"]


# ------------------------------------------------------------------------------------------
# ranges and steps
# ------------------------------------------------------------------------------------------
def _utc(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def resolve_range(range_: str | None, start: datetime | None, end: datetime | None) -> tuple[datetime, datetime]:
    """Predefined range (``15m`` … ``90d``) or a custom ``start``/``end``. Always UTC."""
    now = datetime.now(UTC)
    if start or end:
        s, e = _utc(start or (now - timedelta(hours=1))), _utc(end or now)
        if s >= e:
            raise ValidationFailed("start must be before end")
        if e - s > MAX_CUSTOM_RANGE:
            raise ValidationFailed("custom ranges are limited to 400 days")
        return s, min(e, now + timedelta(minutes=5))
    minutes = RANGES.get(range_ or "1h")
    if minutes is None:
        raise ValidationFailed("invalid range", details={"allowed": list(RANGES)})
    return now - timedelta(minutes=minutes), now


def step_for(start: datetime, end: datetime, max_points: int = TARGET_POINTS) -> int:
    """Smallest 'nice' step that keeps a series at or below ``max_points`` points."""
    raw = (end - start).total_seconds() / max(max_points, 1)
    for step in STEPS:
        if raw <= step:
            return step
    return int(math.ceil(raw / 86400) * 86400)


# kept for callers that still import the legacy name
bucket_seconds = step_for


def effective_rollup(key: str, requested: str | None) -> Rollup:
    d = definition(key)
    if d.type == MetricType.COUNTER:
        return "increase" if requested == "increase" else "rate"
    if requested in (None, "", "rate", "increase"):
        return "avg"
    if requested not in AGGREGATIONS:
        raise ValidationFailed("invalid aggregation", details={"allowed": list(AGGREGATIONS)})
    return requested  # type: ignore[return-value]


def _iso(ms: int) -> str:
    return from_ms(ms).isoformat()


def _label_of(labels: dict[str, str]) -> str | None:
    for k in LABEL_KEYS:
        if labels.get(k):
            return labels[k]
    return None


def _ids(server_ids: Iterable[uuid.UUID | str]) -> list[str]:
    return [str(i) for i in server_ids]


# ------------------------------------------------------------------------------------------
# range queries
# ------------------------------------------------------------------------------------------
async def series(
    server_ids: Iterable[uuid.UUID | str],
    metrics: list[str],
    start: datetime,
    end: datetime,
    *,
    aggregate_servers: GroupMode | None = None,
    labeled: bool = False,
    agg: str | None = None,
    step: int | None = None,
    repo: TimeSeriesRepository | None = None,
) -> dict[str, Any]:
    """Range query in the UI's shape: ``{start, end, step_seconds, series: [{server_id,
    metric, label, points: [{t, v, max}]}]}``.

    ``aggregate_servers`` combines all servers per metric (e.g. fleet connections = sum,
    fleet latency = avg). ``labeled`` returns the per-label series of labeled metrics
    (e.g. active sessions by wait class) instead of the unlabeled ones.
    """
    repo = repo or get_repository()
    ids = _ids(server_ids)
    step = step or step_for(start, end)
    out: dict[str, Any] = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "step_seconds": step,
        "aggregation": agg or "avg",
        "series": [],
    }
    if not ids or not metrics:
        return out
    by_rollup: dict[Rollup, dict[str, str]] = {}
    for key in dict.fromkeys(metrics):
        by_rollup.setdefault(effective_rollup(key, agg), {})[name_for(key)] = key
    group_by: tuple[str, ...] | None = None
    if aggregate_servers:
        group_by = ("wait_type",) if labeled else ()

    async def run(rollup: Rollup, names: dict[str, str]) -> list[tuple[str, Any]]:
        sel = Selector.of(list(names), ids)
        res = await repo.query_range(
            sel, start, end, step, rollup=rollup, group_by=group_by, group_agg=aggregate_servers or "sum"
        )
        return [(names.get(s.name, s.name), s) for s in res]

    results = await asyncio.gather(*(run(r, n) for r, n in by_rollup.items()))
    for key, s in (item for chunk in results for item in chunk):
        label = _label_of(s.labels)
        if bool(label) != labeled:
            continue
        out["series"].append(
            {
                "server_id": None if aggregate_servers else s.labels.get("server_id"),
                "metric": key,
                "label": label,
                "points": [{"t": _iso(t), "v": round(v, 4), "max": round(v, 4)} for t, v in s.points],
            }
        )
    out["series"].sort(key=lambda x: (x["metric"], x["server_id"] or "", x["label"] or ""))
    return out


# ------------------------------------------------------------------------------------------
# current values
# ------------------------------------------------------------------------------------------
def current_metric_keys() -> list[str]:
    return [d.key for d in BY_KEY.values() if d.type != MetricType.COUNTER]


async def latest(
    server_ids: Iterable[uuid.UUID | str],
    metrics: list[str] | None = None,
    *,
    lookback_s: int = 900,
    repo: TimeSeriesRepository | None = None,
) -> dict[str, dict[str, Any]]:
    """``{server_id: {metric_key: value, …, "_collected_at": iso}}`` — one batched query for
    any number of servers (no per-server round trips)."""
    repo = repo or get_repository()
    ids = _ids(server_ids)
    keys = metrics or current_metric_keys()
    names = {name_for(k): k for k in keys}
    if not ids:
        return {}
    values = await repo.get_latest(Selector.of(list(names), ids), lookback_s=lookback_s)
    out: dict[str, dict[str, Any]] = {}
    newest: dict[str, int] = {}
    for v in values:
        if _label_of(v.labels):
            continue
        sid = v.labels.get("server_id", "")
        out.setdefault(sid, {})[names.get(v.name, v.name)] = round(v.value, 4)
        newest[sid] = max(newest.get(sid, 0), v.ts_ms)
    for sid, ms in newest.items():
        out[sid]["_collected_at"] = _iso(ms)
    return out


# ------------------------------------------------------------------------------------------
# windows, sustained conditions, baselines
# ------------------------------------------------------------------------------------------
@dataclass
class Window:
    """Summary of one metric over a window (used by the recommendation engine)."""

    avg: float
    min: float
    max: float
    count: int
    p95: float | None = None


async def windows(
    server_id: uuid.UUID | str,
    metrics: list[str] | None = None,
    hours: float = 6,
    *,
    repo: TimeSeriesRepository | None = None,
) -> dict[str, Window]:
    repo = repo or get_repository()
    keys = metrics or current_metric_keys()
    names = {name_for(k): k for k in keys}
    end = datetime.now(UTC)
    start = end - timedelta(hours=hours)
    sel = Selector.of(list(names), [str(server_id)])
    fns: tuple[Rollup, ...] = ("avg", "min", "max", "count", "p95")
    res = await asyncio.gather(*(repo.aggregate(sel, start, end, fn) for fn in fns))
    tmp: dict[str, dict[str, float]] = {}
    for fn, values in zip(fns, res, strict=True):
        for v in values:
            if _label_of(v.labels):
                continue
            tmp.setdefault(names.get(v.name, v.name), {})[fn] = v.value
    return {
        k: Window(
            avg=d["avg"],
            min=d.get("min", d["avg"]),
            max=d.get("max", d["avg"]),
            count=int(d.get("count", 0)),
            p95=d.get("p95"),
        )
        for k, d in tmp.items()
        if "avg" in d
    }


async def sustained(
    server_id: uuid.UUID | str,
    metric: str,
    threshold: float,
    minutes: int = 15,
    *,
    above: bool = True,
    min_samples: int = 3,
    repo: TimeSeriesRepository | None = None,
) -> dict[str, Any]:
    """Was ``metric`` continuously above (or below) ``threshold`` for the last ``minutes``?

    Every sample in the window must cross the threshold (min > threshold for "above"), and
    the window must contain at least ``min_samples`` samples so a single collection cannot
    trigger a recommendation."""
    repo = repo or get_repository()
    end = datetime.now(UTC)
    start = end - timedelta(minutes=minutes)
    sel = Selector.of(name_for(metric), [str(server_id)])
    bound: Rollup = "min" if above else "max"
    lo, cnt = await asyncio.gather(repo.aggregate(sel, start, end, bound), repo.aggregate(sel, start, end, "count"))
    lo_v = next((v.value for v in lo if not _label_of(v.labels)), None)
    count = int(next((v.value for v in cnt if not _label_of(v.labels)), 0))
    hit = lo_v is not None and count >= min_samples and (lo_v > threshold if above else lo_v < threshold)
    return {
        "metric": metric,
        "threshold": threshold,
        "minutes": minutes,
        "direction": "above" if above else "below",
        "samples": count,
        "bound": lo_v,
        "sustained": bool(hit),
    }


async def baseline(
    server_id: uuid.UUID | str,
    metric: str,
    days: int = 7,
    *,
    repo: TimeSeriesRepository | None = None,
) -> dict[str, Any]:
    """Statistical baseline of one metric (mean, stddev, percentiles over ``days``) and how
    the current value compares (z-score). No baseline is reported from too little data."""
    repo = repo or get_repository()
    end = datetime.now(UTC)
    start = end - timedelta(days=days)
    sel = Selector.of(name_for(metric), [str(server_id)])
    fns: tuple[Rollup, ...] = ("avg", "stddev", "min", "max", "p50", "p95", "p99", "count")
    res = await asyncio.gather(*(repo.aggregate(sel, start, end, fn) for fn in fns))
    stats: dict[str, float] = {}
    for fn, values in zip(fns, res, strict=True):
        v = next((x.value for x in values if not _label_of(x.labels)), None)
        if v is not None and math.isfinite(v):
            stats[fn] = v
    cur = await repo.get_latest(sel, lookback_s=900)
    current = next((x.value for x in cur if not _label_of(x.labels)), None)
    count = int(stats.get("count", 0))
    min_samples = 60
    out: dict[str, Any] = {
        "server_id": str(server_id),
        "metric": metric,
        "window_days": days,
        "samples": count,
        "current": current,
        "sufficient_data": count >= min_samples,
    }
    if count < min_samples:
        out["reason"] = f"at least {min_samples} samples are needed for a baseline (have {count})"
        return out
    sd = stats.get("stddev") or 0.0
    out.update(
        {
            "mean": round(stats["avg"], 4),
            "stddev": round(sd, 4),
            "min": stats.get("min"),
            "max": stats.get("max"),
            "p50": stats.get("p50"),
            "p95": stats.get("p95"),
            "p99": stats.get("p99"),
            "z_score": round((current - stats["avg"]) / sd, 3) if current is not None and sd > 0 else None,
            "band": {"low": round(stats["avg"] - 2 * sd, 4), "high": round(stats["avg"] + 2 * sd, 4)},
        }
    )
    out["anomalous"] = out["z_score"] is not None and abs(out["z_score"]) >= 3
    return out


# ------------------------------------------------------------------------------------------
# capacity
# ------------------------------------------------------------------------------------------
def linear_fit(points: list[tuple[float, float]]) -> tuple[float, float, float] | None:
    """Least squares ``y = a + b·x``; returns ``(a, b, r²)``."""
    n = len(points)
    if n < 2:
        return None
    mx = sum(p[0] for p in points) / n
    my = sum(p[1] for p in points) / n
    sxx = sum((p[0] - mx) ** 2 for p in points)
    if sxx == 0:
        return None
    sxy = sum((p[0] - mx) * (p[1] - my) for p in points)
    b = sxy / sxx
    a = my - b * mx
    ss_tot = sum((p[1] - my) ** 2 for p in points)
    ss_res = sum((p[1] - (a + b * p[0])) ** 2 for p in points)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 1.0
    return a, b, r2


async def capacity(
    server_ids: Iterable[uuid.UUID | str],
    days: int = 30,
    *,
    min_span_days: float = 7,
    repo: TimeSeriesRepository | None = None,
) -> list[dict[str, Any]]:
    """Storage growth per server from the stored history (daily last value + least squares).

    A forecast ("days until full") is only returned when the history spans at least
    ``min_span_days``, contains at least 5 daily points, the trend is growing and a capacity
    is known. Otherwise the reason is returned — nothing is extrapolated from thin data.
    """
    repo = repo or get_repository()
    ids = _ids(server_ids)
    if not ids:
        return []
    end = datetime.now(UTC)
    start = end - timedelta(days=days)
    size_name, cap_name = name_for("storage.database_bytes"), name_for("storage.capacity_bytes")
    hist, caps = await asyncio.gather(
        repo.query_range(Selector.of(size_name, ids), start, end, 86400, rollup="last"),
        repo.get_latest(Selector.of(cap_name, ids), lookback_s=86400),
    )
    cap_by = {v.labels.get("server_id"): v.value for v in caps}
    out = []
    for s in hist:
        sid = s.labels.get("server_id", "")
        pts = [(t / 86_400_000, v) for t, v in s.points]
        row: dict[str, Any] = {
            "server_id": sid,
            "points": len(pts),
            "current_bytes": pts[-1][1] if pts else None,
            "capacity_bytes": cap_by.get(sid),
            "growth_bytes_per_day": None,
            "days_until_full": None,
            "forecast": None,
        }
        span = pts[-1][0] - pts[0][0] if len(pts) > 1 else 0
        fit = linear_fit(pts) if len(pts) >= 5 and span >= min_span_days else None
        if fit is None:
            row["reason"] = f"insufficient history ({len(pts)} daily points over {span:.1f} days)"
        else:
            _a, slope, r2 = fit
            row["growth_bytes_per_day"] = round(slope, 1)
            row["fit_r2"] = round(r2, 3)
            capb = cap_by.get(sid)
            if slope <= 0:
                row["reason"] = "storage is not growing"
            elif not capb:
                row["reason"] = "storage capacity is not reported by this server"
            else:
                remaining = capb - pts[-1][1]
                row["days_until_full"] = round(max(remaining, 0) / slope, 1)
                row["forecast"] = "linear"
        out.append(row)
    return out


# ------------------------------------------------------------------------------------------
# query telemetry
# ------------------------------------------------------------------------------------------
QUERY_SORTS: dict[str, tuple[str, Rollup]] = {
    "total_ms": ("total_ms", "increase"),
    "calls": ("calls", "increase"),
    "rows": ("rows", "increase"),
    "cpu_ms": ("cpu_ms", "increase"),
    "logical_reads": ("logical_reads", "increase"),
    "physical_reads": ("physical_reads", "increase"),
    "temp_blocks": ("temp_blocks", "increase"),
    "lock_wait_ms": ("lock_wait_ms", "increase"),
    "mean_ms": ("mean_ms", "last"),
    "max_ms": ("max_ms", "max"),
}


async def top_queries(
    server_ids: Iterable[uuid.UUID | str],
    *,
    window_s: int = 3600,
    sort: str = "total_ms",
    limit: int = 50,
    database: str | None = None,
    fingerprints: list[str] | None = None,
    repo: TimeSeriesRepository | None = None,
) -> list[dict[str, Any]]:
    """Top statements by activity *within the window* (counter increases), across any number
    of servers in one ranked query, then the details of just those statements."""
    if sort not in QUERY_SORTS:
        raise ValidationFailed("invalid sort", details={"allowed": sorted(QUERY_SORTS)})
    repo = repo or get_repository()
    ids = _ids(server_ids)
    if not ids:
        return []
    field, fn = QUERY_SORTS[sort]
    extra: dict[str, Any] = {"database": database} if database is not None else {}
    if fingerprints is not None:
        if not fingerprints:
            return []
        extra["fingerprint"] = sorted(set(fingerprints))
    ranked = await repo.top(Selector.of(QUERY_BY_FIELD[field].name, ids, **extra), limit, window_s, fn=fn)
    if not ranked:
        return []
    fps = sorted({v.labels.get("fingerprint", "") for v in ranked})
    servers = sorted({v.labels.get("server_id", "") for v in ranked})
    end = datetime.now(UTC)
    start = end - timedelta(seconds=window_s)

    detail_match = {k: v for k, v in extra.items() if k != "fingerprint"}

    def sel(f: str) -> Selector:
        return Selector.of(QUERY_BY_FIELD[f].name, servers, fingerprint=fps, **detail_match)

    counters = ["calls", "total_ms", "rows", "cpu_ms", "logical_reads", "physical_reads", "temp_blocks", "lock_wait_ms"]
    jobs = [repo.aggregate(sel(f), start, end, "increase") for f in counters]
    jobs += [repo.aggregate(sel("max_ms"), start, end, "max"), repo.get_latest(sel("mean_ms"), lookback_s=window_s)]
    jobs.append(repo.get_latest(sel("calls"), lookback_s=window_s))
    res = await asyncio.gather(*jobs)
    data: dict[tuple[str, str, str], dict[str, Any]] = {}

    def k(labels: dict[str, str]) -> tuple[str, str, str]:
        return labels.get("server_id", ""), labels.get("database", ""), labels.get("fingerprint", "")

    for f, values in zip([*counters, "max_ms", "mean_ms_last", "calls_cum"], res, strict=True):
        for v in values:
            row = data.setdefault(k(v.labels), {})
            row[f] = v.value
            if f == "calls_cum":
                row["last_sample_ms"] = v.ts_ms
    wanted = {k(v.labels) for v in ranked}
    out = []
    for key in wanted:
        d = data.get(key, {})
        calls = d.get("calls") or 0.0
        total = d.get("total_ms") or 0.0
        mean = total / calls if calls > 0 else d.get("mean_ms_last")
        out.append(
            {
                "server_id": key[0],
                "database": key[1] or None,
                "fingerprint": key[2],
                "calls": round(calls, 2),
                "total_ms": round(total, 3),
                "mean_ms": round(mean, 3) if mean is not None else None,
                "max_ms": d.get("max_ms"),
                "rows": d.get("rows"),
                "cpu_ms": d.get("cpu_ms"),
                "logical_reads": d.get("logical_reads"),
                "physical_reads": d.get("physical_reads"),
                "temp_blocks": d.get("temp_blocks"),
                "lock_wait_ms": d.get("lock_wait_ms"),
                "calls_per_sec": round(calls / window_s, 4) if window_s else None,
                "window_seconds": window_s,
                "total_calls": d.get("calls_cum"),
                "last_seen": _iso(int(d["last_sample_ms"])) if d.get("last_sample_ms") else None,
            }
        )
    out.sort(key=lambda r: r.get(sort) or 0, reverse=True)
    return out[:limit]


async def query_history(
    server_id: uuid.UUID | str,
    fingerprint: str,
    start: datetime,
    end: datetime,
    *,
    repo: TimeSeriesRepository | None = None,
) -> list[dict[str, Any]]:
    """Per-step executions, executions/s, mean latency and physical reads of one statement."""
    repo = repo or get_repository()
    step = max(step_for(start, end, 120), 300)
    ids = [str(server_id)]

    def sel(f: str) -> Selector:
        return Selector.of(QUERY_BY_FIELD[f].name, ids, fingerprint=fingerprint)

    calls, total, reads = await asyncio.gather(
        repo.query_range(sel("calls"), start, end, step, rollup="increase", group_by=("fingerprint",)),
        repo.query_range(sel("total_ms"), start, end, step, rollup="increase", group_by=("fingerprint",)),
        repo.query_range(sel("physical_reads"), start, end, step, rollup="increase", group_by=("fingerprint",)),
    )

    def pts(res: list[Any]) -> dict[int, float]:
        return {t: v for s in res for t, v in s.points}

    c, t_, r = pts(calls), pts(total), pts(reads)
    out = []
    for ts in sorted(c):
        n = c[ts]
        out.append(
            {
                "t": _iso(ts),
                "calls": round(n, 2),
                "calls_per_sec": round(n / step, 4),
                "mean_ms": round(t_.get(ts, 0.0) / n, 3) if n > 0 else None,
                "physical_reads": r.get(ts),
            }
        )
    return out
