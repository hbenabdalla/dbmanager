"""Performance intelligence (milestone 0.13).

* robust trend estimation and capacity forecasting with confidence bands (Theil–Sen),
* statistical baselines for alert rules (z-score),
* per-fingerprint query latency regression and plan-change detection,
* period-vs-period query comparison and plan-vs-plan comparison,
* before/after validation of resolved recommendations.

Everything here reads the monitoring history (time-series database) and never touches a
managed database. Nothing is extrapolated from thin data: each result says why it is absent.
"""

from __future__ import annotations

import asyncio
import math
import statistics
import time
import uuid
from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from app.timeseries.catalog import QUERY_BY_FIELD, name_for
from app.timeseries.model import Rollup, Selector, SeriesValue
from app.timeseries.repository import TimeSeriesRepository, get_repository

DAY_MS = 86_400_000
#: two-sided 90 % normal quantile used for Sen's slope confidence interval
Z90 = 1.6449
#: minimum samples before a baseline (mean / stddev) is trusted
MIN_BASELINE_SAMPLES = 60
#: a before/after change smaller than this fraction counts as "unchanged"
VALIDATION_TOLERANCE = 0.10

Scope = Literal["server", "database", "tablespace"]


# ------------------------------------------------------------------------------------------
# robust trend (Theil–Sen) and forecasting
# ------------------------------------------------------------------------------------------
def theil_sen(points: list[tuple[float, float]]) -> dict[str, float] | None:
    """Median of pairwise slopes (robust to outliers such as a one-off bulk load or purge),
    Sen's 90 % confidence interval of the slope and the 90th percentile of the absolute
    residuals. ``None`` with fewer than three distinct x values."""
    pts = sorted(points)
    n = len(pts)
    slopes = sorted(
        (pts[j][1] - pts[i][1]) / (pts[j][0] - pts[i][0])
        for i in range(n)
        for j in range(i + 1, n)
        if pts[j][0] != pts[i][0]
    )
    if len(slopes) < 3:
        return None
    slope = statistics.median(slopes)
    intercept = statistics.median(y - slope * x for x, y in pts)
    # Sen (1968): rank bounds of the slope CI from the variance of Kendall's S
    big_n = len(slopes)
    c = Z90 * math.sqrt(n * (n - 1) * (2 * n + 5) / 18)
    lo_i = max(int(math.floor((big_n - c) / 2)), 0)
    hi_i = min(int(math.ceil((big_n + c) / 2)), big_n - 1)
    residuals = sorted(abs(y - (intercept + slope * x)) for x, y in pts)
    r90 = residuals[min(int(math.ceil(0.9 * len(residuals))) - 1, len(residuals) - 1)]
    return {
        "slope": slope,
        "intercept": intercept,
        "slope_low": slopes[lo_i],
        "slope_high": slopes[hi_i],
        "residual_p90": r90,
        "n": n,
    }


def _days(remaining: float, slope: float) -> float | None:
    if slope <= 0:
        return None
    return round(max(remaining, 0.0) / slope, 1)


def forecast(
    points: list[tuple[float, float]],
    capacity: float | None,
    *,
    horizon_days: int = 90,
    min_points: int = 5,
    min_span_days: float = 7,
) -> dict[str, Any]:
    """Forecast of one size series. ``points`` are ``(day number, bytes)``.

    ``days_to_full`` has three readings: ``likely`` (median slope), ``earliest`` (upper slope
    bound plus the residual spread — the cautious planning figure) and ``latest`` (lower slope
    bound minus the spread; ``None`` when the lower bound does not grow)."""
    pts = sorted(points)
    out: dict[str, Any] = {
        "points": len(pts),
        "current_bytes": pts[-1][1] if pts else None,
        "capacity_bytes": capacity,
        "method": "theil-sen",
        "confidence": 0.9,
        "growth_bytes_per_day": None,
        "growth_low": None,
        "growth_high": None,
        "days_to_full": None,
        "band": [],
        "history": [{"t": datetime.fromtimestamp(x * 86400, UTC).isoformat(), "v": round(y, 1)} for x, y in pts],
        "reason": None,
    }
    span = pts[-1][0] - pts[0][0] if len(pts) > 1 else 0.0
    out["history_days"] = round(span, 1)
    fit = theil_sen(pts) if len(pts) >= min_points and span >= min_span_days else None
    if fit is None:
        out["reason"] = (
            f"insufficient history: {len(pts)} daily points over {span:.1f} days "
            f"(need {min_points} points over {min_span_days:g} days)"
        )
        return out
    slope, lo, hi, spread = fit["slope"], fit["slope_low"], fit["slope_high"], fit["residual_p90"]
    x_last = pts[-1][0]
    base = fit["intercept"] + slope * x_last
    out.update(
        {
            "growth_bytes_per_day": round(slope, 1),
            "growth_low": round(lo, 1),
            "growth_high": round(hi, 1),
            "fitted_bytes": round(base, 1),
            "residual_p90": round(spread, 1),
        }
    )
    step = max(horizon_days // 30, 1)
    out["band"] = [
        {
            "day": d,
            "t": datetime.fromtimestamp((x_last + d) * 86400, UTC).isoformat(),
            "likely": round(base + slope * d, 1),
            "low": round(max(base + lo * d - spread, 0.0), 1),
            "high": round(base + hi * d + spread, 1),
        }
        for d in range(0, horizon_days + 1, step)
    ]
    if hi <= 0 and slope <= 0:
        out["reason"] = "not growing"
        return out
    if not capacity:
        out["reason"] = "no capacity limit is reported for this object"
        return out
    remaining = capacity - base
    out["days_to_full"] = {
        "likely": _days(remaining, slope),
        "earliest": _days(remaining - spread, hi),
        "latest": _days(remaining + spread, lo),
    }
    out["full_within_horizon"] = bool(
        out["days_to_full"]["earliest"] is not None and out["days_to_full"]["earliest"] <= horizon_days
    )
    return out


def _ids(server_ids: Iterable[uuid.UUID | str]) -> list[str]:
    return sorted({str(s) for s in server_ids})


async def capacity_forecast(
    server_ids: Iterable[uuid.UUID | str],
    scope: Scope = "server",
    *,
    days: int = 30,
    horizon_days: int = 90,
    min_span_days: float = 7,
    repo: TimeSeriesRepository | None = None,
) -> list[dict[str, Any]]:
    """Growth and days-to-full per server, database or tablespace from daily last values.

    Capacity basis: the server's reported capacity (``server``); for a database, the space it
    could grow into (its size plus the server's free capacity — databases share the volume);
    for a tablespace, its maximum size where the engine reports one."""
    repo = repo or get_repository()
    ids = _ids(server_ids)
    if not ids:
        return []
    end = datetime.now(UTC)
    start = end - timedelta(days=days)
    srv_size, srv_cap = name_for("storage.database_bytes"), name_for("storage.capacity_bytes")
    jobs: list[Any] = [
        repo.get_latest(Selector.of(srv_cap, ids), lookback_s=86400),
        repo.get_latest(Selector.of(srv_size, ids), lookback_s=86400),
    ]
    if scope == "server":
        size_name, label = srv_size, None
    elif scope == "database":
        size_name, label = name_for("storage.db_size_bytes"), "database"
    else:
        size_name, label = name_for("storage.tablespace_used_bytes"), "tablespace"
        jobs.append(repo.get_latest(Selector.of(name_for("storage.tablespace_max_bytes"), ids), lookback_s=3 * 86400))
    hist_job = repo.query_range(Selector.of(size_name, ids), start, end, 86400, rollup="last")
    hist, *rest = await asyncio.gather(hist_job, *jobs)
    rest = [list(r) for r in rest]
    caps = {v.labels.get("server_id"): v.value for v in rest[0]}
    used_now = {v.labels.get("server_id"): v.value for v in rest[1]}
    ts_max = (
        {(v.labels.get("server_id"), v.labels.get("tablespace")): v.value for v in rest[2]}
        if scope == "tablespace"
        else {}
    )
    out = []
    for s in hist:
        sid = s.labels.get("server_id", "")
        obj = s.labels.get(label) if label else None
        if label and not obj:
            continue
        pts = [(t / DAY_MS, v) for t, v in s.points if v is not None and math.isfinite(v)]
        if scope == "server":
            capacity, basis = caps.get(sid), "server capacity"
        elif scope == "database":
            free = (caps[sid] - used_now.get(sid, 0.0)) if caps.get(sid) else None
            capacity = (pts[-1][1] + max(free, 0.0)) if (pts and free is not None) else None
            basis = "database size + server free capacity"
        else:
            capacity, basis = ts_max.get((sid, obj)), "tablespace maximum size"
        row = forecast(pts, capacity, horizon_days=horizon_days, min_span_days=min_span_days)
        row.update({"server_id": sid, "scope": scope, "object": obj, "capacity_basis": basis})
        out.append(row)
    out.sort(key=_urgency)
    return out


def _urgency(r: dict[str, Any]) -> tuple[float, float]:
    d = (r.get("days_to_full") or {}).get("earliest")
    return (d if d is not None else math.inf, -(r.get("growth_bytes_per_day") or 0.0))


# ------------------------------------------------------------------------------------------
# baselines (z-score) for alert rules
# ------------------------------------------------------------------------------------------
_BASELINE_CACHE: dict[tuple[str, tuple[str, ...], int, int], tuple[float, dict[str, dict[str, float]]]] = {}
BASELINE_CACHE_SECONDS = 600


def _plain(v: SeriesValue, extra_ok: tuple[str, ...] = ()) -> bool:
    return not any(k in v.labels for k in ("wait_type", "database", "tablespace", "fingerprint") if k not in extra_ok)


async def baselines(
    metric: str,
    server_ids: list[str],
    *,
    days: int,
    exclude_recent_s: int,
    now: datetime,
    repo: TimeSeriesRepository | None = None,
) -> dict[str, dict[str, float]]:
    """mean / stddev / sample count of a metric per server over ``days`` (the most recent
    ``exclude_recent_s`` excluded so the condition being tested does not dilute its own
    baseline). Cached for a few minutes: baselines move slowly."""
    key = (metric, tuple(sorted(server_ids)), days, exclude_recent_s)
    hit = _BASELINE_CACHE.get(key)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    repo = repo or get_repository()
    end = now - timedelta(seconds=exclude_recent_s)
    start = now - timedelta(days=days)
    sel = Selector.of([name_for(metric)], server_ids=server_ids)
    fns: tuple[Rollup, ...] = ("avg", "stddev", "count")
    res = await asyncio.gather(*(repo.aggregate(sel, start, end, fn) for fn in fns))
    out: dict[str, dict[str, float]] = {}
    for fn, values in zip(fns, res, strict=True):
        for v in values:
            sid = v.labels.get("server_id")
            if sid and _plain(v) and math.isfinite(v.value):
                out.setdefault(sid, {})[fn] = v.value
    _BASELINE_CACHE[key] = (time.monotonic() + BASELINE_CACHE_SECONDS, out)
    if len(_BASELINE_CACHE) > 2000:
        _BASELINE_CACHE.clear()
    return out


def zscore(current: float | None, base: dict[str, float] | None) -> tuple[float | None, str | None]:
    """z-score of ``current`` against a baseline, or the reason it cannot be computed."""
    if current is None:
        return None, "no current value"
    if not base or base.get("count", 0) < MIN_BASELINE_SAMPLES:
        have = int((base or {}).get("count", 0))
        return None, f"baseline needs {MIN_BASELINE_SAMPLES} samples (have {have})"
    sd = base.get("stddev") or 0.0
    if sd <= 0:
        return None, "baseline has no variation (stddev 0)"
    return (current - base["avg"]) / sd, None


# ------------------------------------------------------------------------------------------
# query latency regressions and plan changes
# ------------------------------------------------------------------------------------------
async def _per_statement(
    repo: TimeSeriesRepository,
    server_id: str,
    start: datetime,
    end: datetime,
    fields: dict[str, Rollup] | dict[str, tuple[str, Rollup]],
    database: str | None = None,
    fingerprints: list[str] | None = None,
) -> dict[tuple[str, str], dict[str, float]]:
    """``fields``: result name → rollup of the query metric of that name, or result name →
    ``(query metric, rollup)`` when one metric is summarized several ways."""
    match: dict[str, Any] = {}
    if database is not None:
        match["database"] = database
    if fingerprints is not None:
        match["fingerprint"] = fingerprints
    names = list(fields)
    specs = [v if isinstance(v, tuple) else (k, v) for k, v in fields.items()]
    res = await asyncio.gather(
        *(repo.aggregate(Selector.of(QUERY_BY_FIELD[f].name, [server_id], **match), start, end, fn) for f, fn in specs)
    )
    out: dict[tuple[str, str], dict[str, float]] = {}
    for f, values in zip(names, res, strict=True):
        for v in values:
            if not math.isfinite(v.value):
                continue
            k = (v.labels.get("database", ""), v.labels.get("fingerprint", ""))
            out.setdefault(k, {})[f] = v.value
    return out


def _mean(d: dict[str, float] | None) -> float | None:
    if not d or not d.get("calls"):
        return None
    return d.get("total_ms", 0.0) / d["calls"]


async def query_regressions(
    server_id: uuid.UUID | str,
    *,
    recent_hours: int = 1,
    baseline_days: int = 7,
    threshold_pct: float = 50,
    min_calls: int = 20,
    min_delta_ms: float = 5,
    now: datetime | None = None,
    include_plan_only: bool = True,
    repo: TimeSeriesRepository | None = None,
) -> list[dict[str, Any]]:
    """Statements whose mean latency in the recent window exceeds their baseline mean.

    A regression needs: enough executions in both windows (``min_calls``), a relative increase
    of at least ``threshold_pct`` and an absolute increase of at least ``min_delta_ms`` (so a
    0.1 ms → 0.2 ms statement is not reported). Plan changes are reported where the engine
    exposes plan identity (Oracle plan hash, SQL Server cached plan count)."""
    repo = repo or get_repository()
    now = now or datetime.now(UTC)
    sid = str(server_id)
    split = now - timedelta(hours=recent_hours)
    start = now - timedelta(days=baseline_days)
    fields: dict[str, Rollup] = {"calls": "increase", "total_ms": "increase"}
    # plan identity: the plan ids seen in the baseline (min/max of the dominant plan hash) against
    # the current one; cached plan counts: the typical (median) baseline count against the recent peak
    recent, base, recent_p, base_p = await asyncio.gather(
        _per_statement(repo, sid, split, now, fields),
        _per_statement(repo, sid, start, split, fields),
        _per_statement(repo, sid, split, now, {"plans": "max", "plan_id": "last"}),
        _per_statement(
            repo, sid, start, split, {"plans": "p50", "plan_min": ("plan_id", "min"), "plan_max": ("plan_id", "max")}
        ),
    )
    out: list[dict[str, Any]] = []
    for key, r in recent.items():
        b = base.get(key)
        rm, bm = _mean(r), _mean(b)
        rp, bp = recent_p.get(key, {}), dict(base_p.get(key, {}))
        bp["plan_id"], plan_changed = _baseline_plan(bp.get("plan_min"), bp.get("plan_max"), rp.get("plan_id"))
        if rp.get("plans") and bp.get("plans") and rp["plans"] > bp["plans"]:
            plan_changed = True
        if rm is None or bm is None:
            continue
        enough = r.get("calls", 0) >= min_calls and (b or {}).get("calls", 0) >= min_calls
        change = (rm - bm) / bm * 100 if bm > 0 else math.inf
        regressed = enough and change >= threshold_pct and (rm - bm) >= min_delta_ms
        if not regressed and not (include_plan_only and plan_changed and enough):
            continue
        out.append(
            {
                "server_id": sid,
                "database": key[0] or None,
                "fingerprint": key[1],
                "kind": "latency+plan" if regressed and plan_changed else "latency" if regressed else "plan_change",
                "regressed": regressed,
                "plan_changed": plan_changed,
                "baseline_mean_ms": round(bm, 3),
                "recent_mean_ms": round(rm, 3),
                "change_pct": round(change, 1) if math.isfinite(change) else None,
                "delta_ms": round(rm - bm, 3),
                "baseline_calls": round((b or {}).get("calls", 0)),
                "recent_calls": round(r.get("calls", 0)),
                "baseline_plan_id": _plan_label(bp.get("plan_id")),
                "recent_plan_id": _plan_label(rp.get("plan_id")),
                "baseline_plans": int(bp["plans"]) if bp.get("plans") else None,
                "recent_plans": int(rp["plans"]) if rp.get("plans") else None,
                "recent_window_hours": recent_hours,
                "baseline_days": baseline_days,
                # time lost in the recent window compared with the baseline speed
                "excess_ms": round((rm - bm) * r.get("calls", 0), 1),
            }
        )
    out.sort(key=lambda x: (not x["regressed"], -(x["excess_ms"] or 0)))
    return out


def _baseline_plan(lo: float | None, hi: float | None, recent: float | None) -> tuple[float | None, bool]:
    """(the baseline's plan id, whether the current plan differs from it). When the baseline
    already saw two plans, the baseline plan is the one that is not the current one."""
    if lo is None or hi is None or recent is None:
        return (lo if lo == hi else None), False
    if lo == hi:
        return lo, recent != lo
    return (lo if recent == hi else hi), True


def _plan_label(v: float | None) -> str | None:
    return str(int(v)) if v else None


# ------------------------------------------------------------------------------------------
# period-vs-period query comparison
# ------------------------------------------------------------------------------------------
COMPARE_FIELDS: dict[str, Rollup] = {
    "calls": "increase",
    "total_ms": "increase",
    "rows": "increase",
    "cpu_ms": "increase",
    "logical_reads": "increase",
    "physical_reads": "increase",
}


def _pct(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or a == 0:
        return None
    return round((b - a) / a * 100, 1)


async def compare_periods(
    server_id: uuid.UUID | str,
    a: tuple[datetime, datetime],
    b: tuple[datetime, datetime],
    *,
    database: str | None = None,
    limit: int = 50,
    change_pct: float = 20,
    repo: TimeSeriesRepository | None = None,
) -> dict[str, Any]:
    """Per-statement activity in period A (reference) and period B, with deltas. Statements
    are ranked by the absolute change of total execution time; ``status`` is ``new`` / ``gone``
    / ``slower`` / ``faster`` / ``similar`` (mean latency change against ``change_pct``), or
    ``unclassified`` when one of the periods has no statistics at all."""
    repo = repo or get_repository()
    sid = str(server_id)
    pa, pb = await asyncio.gather(
        _per_statement(repo, sid, a[0], a[1], COMPARE_FIELDS, database),
        _per_statement(repo, sid, b[0], b[1], COMPARE_FIELDS, database),
    )
    rows: list[dict[str, Any]] = []
    totals = {"a": dict.fromkeys(("calls", "total_ms"), 0.0), "b": dict.fromkeys(("calls", "total_ms"), 0.0)}
    for key in set(pa) | set(pb):
        ra, rb = pa.get(key, {}), pb.get(key, {})
        ca, cb = ra.get("calls", 0.0), rb.get("calls", 0.0)
        for p, r in (("a", ra), ("b", rb)):
            totals[p]["calls"] += r.get("calls", 0.0)
            totals[p]["total_ms"] += r.get("total_ms", 0.0)
        if ca <= 0 and cb <= 0:
            continue
        ma, mb = _mean(ra), _mean(rb)
        if ca <= 0:
            status = "new"
        elif cb <= 0:
            status = "gone"
        else:
            ch = _pct(ma, mb)
            status = "similar" if ch is None or abs(ch) < change_pct else "slower" if ch > 0 else "faster"
        rows.append(
            {
                "database": key[0] or None,
                "fingerprint": key[1],
                "status": status,
                "a": _period_row(ra),
                "b": _period_row(rb),
                "mean_change_pct": _pct(ma, mb),
                "calls_change_pct": _pct(ca or None, cb or None),
                "total_ms_delta": round(rb.get("total_ms", 0.0) - ra.get("total_ms", 0.0), 1),
            }
        )
    notes = []
    for p, label in (("a", "A"), ("b", "B")):
        if totals[p]["calls"] <= 0:
            notes.append(
                f"Period {label} has no query statistics (outside the collected history, or nothing was collected): "
                "statements are not classified."
            )
    if notes:
        for r in rows:
            r["status"] = "unclassified"
    rows.sort(key=lambda r: abs(r["total_ms_delta"]), reverse=True)
    summary = {
        p: {
            "calls": round(t["calls"]),
            "total_ms": round(t["total_ms"], 1),
            "mean_ms": round(t["total_ms"] / t["calls"], 3) if t["calls"] else None,
        }
        for p, t in totals.items()
    }
    counts = Counter(r["status"] for r in rows)
    return {
        "server_id": sid,
        "a": {"start": a[0].isoformat(), "end": a[1].isoformat()},
        "b": {"start": b[0].isoformat(), "end": b[1].isoformat()},
        "summary": summary,
        "status_counts": dict(counts),
        "items": rows[:limit],
        "total_statements": len(rows),
        "notes": notes,
    }


def _period_row(r: dict[str, float]) -> dict[str, Any]:
    m = _mean(r)
    return {
        "calls": round(r.get("calls", 0.0)),
        "total_ms": round(r.get("total_ms", 0.0), 1),
        "mean_ms": round(m, 3) if m is not None else None,
        "rows": round(r["rows"]) if r.get("rows") is not None else None,
        "cpu_ms": round(r["cpu_ms"], 1) if r.get("cpu_ms") is not None else None,
        "logical_reads": round(r["logical_reads"]) if r.get("logical_reads") is not None else None,
        "physical_reads": round(r["physical_reads"]) if r.get("physical_reads") is not None else None,
    }


# ------------------------------------------------------------------------------------------
# plan-vs-plan comparison
# ------------------------------------------------------------------------------------------
def _nodes(plan: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    stack: list[dict[str, Any]] = [plan["root"]] if plan.get("root") else []
    while stack:
        n = stack.pop()
        out.append(n)
        stack.extend(reversed(n.get("children") or []))
    return out


def _op(n: dict[str, Any]) -> str:
    parts = [n.get("operator") or "?"]
    if n.get("relation"):
        parts.append(f"on {n['relation']}")
    if n.get("index"):
        parts.append(f"using {n['index']}")
    return " ".join(parts)


def compare_plans(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Structural difference of two saved plans (``ExplainResult`` documents): total cost,
    operations added / removed, access path changes per table and plan issues that appeared
    or disappeared. Costs of different engines are not comparable and are not compared."""
    na, nb = _nodes(a), _nodes(b)
    ops_a, ops_b = Counter(_op(n) for n in na), Counter(_op(n) for n in nb)
    access_a: dict[str, set[str]] = {}
    access_b: dict[str, set[str]] = {}
    for nodes, acc in ((na, access_a), (nb, access_b)):
        for n in nodes:
            if n.get("relation"):
                acc.setdefault(n["relation"], set()).add(
                    n.get("operator", "?") + (f" ({n['index']})" if n.get("index") else "")
                )
    access = [
        {"relation": rel, "a": sorted(access_a.get(rel, ())), "b": sorted(access_b.get(rel, ()))}
        for rel in sorted(set(access_a) | set(access_b))
        if access_a.get(rel) != access_b.get(rel)
    ]
    issues_a = {(i.get("code"), i.get("title")) for i in a.get("issues") or []}
    issues_b = {(i.get("code"), i.get("title")) for i in b.get("issues") or []}
    same_engine = a.get("engine") == b.get("engine")
    ca, cb = a.get("total_cost"), b.get("total_cost")
    return {
        "same_engine": same_engine,
        "identical_shape": ops_a == ops_b,
        "a": {"nodes": len(na), "total_cost": ca, "mode": a.get("mode"), "execution_ms": a.get("execution_ms")},
        "b": {"nodes": len(nb), "total_cost": cb, "mode": b.get("mode"), "execution_ms": b.get("execution_ms")},
        "cost_change_pct": _pct(ca, cb) if same_engine else None,
        "execution_change_pct": _pct(a.get("execution_ms"), b.get("execution_ms")),
        "operations_added": sorted((ops_b - ops_a).elements()),
        "operations_removed": sorted((ops_a - ops_b).elements()),
        "access_path_changes": access,
        "issues_added": [{"code": c, "title": t} for c, t in sorted(issues_b - issues_a, key=str)],
        "issues_resolved": [{"code": c, "title": t} for c, t in sorted(issues_a - issues_b, key=str)],
        "notes": [] if same_engine else ["plans come from different engines: costs are not comparable"],
    }


# ------------------------------------------------------------------------------------------
# recommendation validation (before / after)
# ------------------------------------------------------------------------------------------
#: rule → (metric key, which direction is better) for metric-based findings
VALIDATION_METRICS: dict[str, tuple[str, Literal["lower", "higher"]]] = {
    "GEN-PERF-001": ("connections.utilization", "lower"),
    "GEN-PERF-002": ("cache.hit_ratio", "higher"),
    "GEN-PERF-003": ("sessions.blocked", "lower"),
    "GEN-PERF-004": ("deadlocks.count", "lower"),
    "GEN-PERF-005": ("replication.lag_seconds", "lower"),
    "GEN-PERF-006": ("transactions.longest_seconds", "lower"),
    "GEN-PERF-008": ("temp.bytes_per_sec", "lower"),
    "GEN-PERF-009": ("cpu.percent", "lower"),
    "GEN-PERF-010": ("storage.utilization", "lower"),
}


def validation_method(rule_id: str, affected_object: str | None) -> dict[str, Any] | None:
    """How a finding's improvement is measured, or ``None`` when it is not measurable from
    the monitoring history (configuration and security style findings)."""
    if affected_object and affected_object.startswith("query:"):
        return {"kind": "query", "metric": "mean_ms", "fingerprint": affected_object[6:], "better": "lower"}
    if rule_id in VALIDATION_METRICS:
        metric, better = VALIDATION_METRICS[rule_id]
        return {"kind": "metric", "metric": metric, "better": better}
    return None


async def measure(
    server_id: str,
    method: dict[str, Any],
    start: datetime,
    end: datetime,
    database: str | None = None,
    repo: TimeSeriesRepository | None = None,
) -> dict[str, Any]:
    """The measured value of a validation method over one window."""
    repo = repo or get_repository()
    if method["kind"] == "query":
        rows = await _per_statement(
            repo,
            server_id,
            start,
            end,
            {"calls": "increase", "total_ms": "increase"},
            database,
            [method["fingerprint"]],
        )
        calls = sum(r.get("calls", 0.0) for r in rows.values())
        total = sum(r.get("total_ms", 0.0) for r in rows.values())
        return {"value": round(total / calls, 3) if calls > 0 else None, "samples": round(calls)}
    sel = Selector.of(name_for(method["metric"]), [server_id])
    avg, cnt = await asyncio.gather(repo.aggregate(sel, start, end, "avg"), repo.aggregate(sel, start, end, "count"))
    v = next((x.value for x in avg if _plain(x) and math.isfinite(x.value)), None)
    n = next((x.value for x in cnt if _plain(x)), 0.0)
    return {"value": round(v, 4) if v is not None else None, "samples": int(n)}


def verdict(before: float | None, after: float | None, better: str) -> tuple[str, float | None]:
    if before is None or after is None:
        return "insufficient_data", None
    if before == 0:
        change = 0.0 if after == 0 else math.inf
    else:
        change = (after - before) / abs(before)
    pct = round(change * 100, 1) if math.isfinite(change) else None
    if abs(change) < VALIDATION_TOLERANCE:
        return "unchanged", pct
    improved = change < 0 if better == "lower" else change > 0
    return ("improved" if improved else "regressed"), pct


async def start_validation(
    rec: Any, resolved_at: datetime, window_hours: int, repo: TimeSeriesRepository | None = None
) -> dict[str, Any]:
    """Validation record for a recommendation that was just resolved: the "before" window
    is measured now; the "after" window is measured once it has elapsed."""
    method = validation_method(rec.rule_id, rec.affected_object)
    base: dict[str, Any] = {
        "resolved_at": resolved_at.isoformat(),
        "window_hours": window_hours,
        "tolerance_pct": VALIDATION_TOLERANCE * 100,
    }
    if method is None:
        return {**base, "status": "not_measurable", "reason": "this finding has no metric to measure before and after"}
    try:
        before = await measure(
            str(rec.server_id), method, resolved_at - timedelta(hours=window_hours), resolved_at, rec.database, repo
        )
    except Exception as exc:  # noqa: BLE001 - storage outage: record and retry later
        return {**base, "method": method, "status": "pending", "reason": f"monitoring history unavailable: {exc}"[:300]}
    return {
        **base,
        "method": method,
        "status": "measuring",
        "before": before,
        "after": None,
        "due_at": (resolved_at + timedelta(hours=window_hours)).isoformat(),
    }


async def complete_validation(
    rec: Any, now: datetime, repo: TimeSeriesRepository | None = None
) -> dict[str, Any] | None:
    """Finish a ``measuring`` validation whose after-window has elapsed (``None`` = not due)."""
    v = dict(rec.validation or {})
    if v.get("status") != "measuring":
        return None
    due = datetime.fromisoformat(v["due_at"])
    if now < due:
        return None
    resolved_at = datetime.fromisoformat(v["resolved_at"])
    after = await measure(str(rec.server_id), v["method"], resolved_at, due, rec.database, repo)
    status, pct = verdict((v.get("before") or {}).get("value"), after.get("value"), v["method"]["better"])
    v.update({"after": after, "status": status, "change_pct": pct, "measured_at": now.isoformat()})
    return v
