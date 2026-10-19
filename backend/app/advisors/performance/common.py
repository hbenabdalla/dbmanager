"""Performance rules — Engine-neutral rules (normalized metrics)."""

from __future__ import annotations

from app.advisors.engine import EvidenceContext, RuleFinding, fmt_bytes, rule


@rule(
    "GEN-PERF-001",
    engines=("*",),
    kind="performance",
    category="connections",
    severity="high",
    title="Connection pool near capacity",
)
def connection_utilization(ctx: EvidenceContext) -> list[RuleFinding]:
    """Connection utilization above 85% of the configured maximum *sustained for 15 minutes*
    (every sample in the window), or repeated peaks at 95%+ within the analysis window."""
    m = ctx.m("connections.utilization")
    sus = ctx.sustained.get("connections.utilization") or {}
    if not m or m.count < 3:
        return []
    sustained = bool(sus.get("sustained"))
    if not sustained and m.max < 95:
        return []
    if sustained:
        floor = sus.get("bound") or m.min
        sev = "critical" if floor >= 95 else "high"
        title = "Connections are close to the configured maximum"
        finding = (
            f"Connection utilization stayed above {sus.get('threshold', 85):.0f}% for the last "
            f"{sus.get('minutes', 15)} minutes (lowest {floor:.0f}%, peak {m.max:.0f}% over {ctx.window_hours:.0f}h)."
        )
    else:
        sev = "medium"
        title = "Connection usage peaks near the configured maximum"
        finding = (
            f"Connection utilization peaked at {m.max:.0f}% (average {m.avg:.0f}%) over the last "
            f"{ctx.window_hours:.0f}h without staying high."
        )
    return [
        RuleFinding(
            title=title,
            finding=finding,
            evidence={
                "peak_utilization_pct": round(m.max, 1),
                "avg_utilization_pct": round(m.avg, 1),
                "p95_utilization_pct": round(m.p95, 1) if m.p95 is not None else None,
                "sustained_minutes": sus.get("minutes") if sustained else 0,
                "sustained_threshold_pct": sus.get("threshold"),
                "max_connections": ctx.latest.get("connections.max"),
                "samples": m.count,
            },
            recommendation="Introduce or tune a connection pooler (PgBouncer, ProxySQL, application pools) and review idle "
            "sessions before raising the server connection limit.",
            expected_impact="Prevents connection refusals during peaks and reduces per-connection memory overhead.",
            risk="Raising the connection limit increases memory use and may require a restart.",
            confidence=(0.9 if m.count > 20 else 0.75) if sustained else 0.6,
            severity=sev,
            guidance="Check which applications hold most connections on the Sessions tab; size pools to the number of CPU "
            "cores × 2–4 rather than to peak client count.",
        )
    ]


@rule(
    "GEN-PERF-002",
    engines=("*",),
    kind="performance",
    category="memory",
    severity="high",
    title="Low buffer cache efficiency",
)
def cache_hit_ratio(ctx: EvidenceContext) -> list[RuleFinding]:
    """Buffer/cache hit ratio below 95% on average."""
    m = ctx.m("cache.hit_ratio")
    reads = ctx.m("io.blocks_read_per_sec")
    if not m or m.count < 3 or m.avg >= 95:
        return []
    if reads and reads.avg < 50:  # tiny workloads produce noisy ratios
        return []
    return [
        RuleFinding(
            title="Low buffer cache hit ratio",
            finding=f"Average cache hit ratio is {m.avg:.1f}% (minimum {m.min:.1f}%) over {ctx.window_hours:.0f}h.",
            evidence={
                "avg_hit_ratio_pct": round(m.avg, 2),
                "min_hit_ratio_pct": round(m.min, 2),
                "avg_physical_reads_per_sec": round(reads.avg, 1) if reads else None,
                "samples": m.count,
            },
            recommendation="Investigate the queries with the highest physical reads first (Queries tab). If the working set "
            "genuinely exceeds memory, review the buffer cache / pool size for this engine.",
            expected_impact="Fewer physical reads, lower I/O latency and more stable query response times.",
            risk="Increasing cache memory without host headroom can cause swapping; requires restart on some engines.",
            confidence=0.75,
            severity="high" if m.avg < 90 else "medium",
        )
    ]


@rule(
    "GEN-PERF-003",
    engines=("*",),
    kind="performance",
    category="locking",
    severity="high",
    title="Sessions blocked by locks",
)
def blocking(ctx: EvidenceContext) -> list[RuleFinding]:
    m = ctx.m("sessions.blocked")
    if not m or m.max < 1 or m.avg < 0.5:
        return []
    return [
        RuleFinding(
            title="Recurring lock blocking",
            finding=f"On average {m.avg:.1f} sessions were blocked (peak {m.max:.0f}) over {ctx.window_hours:.0f}h.",
            evidence={
                "avg_blocked_sessions": round(m.avg, 2),
                "peak_blocked_sessions": m.max,
                "longest_transaction_seconds": ctx.latest.get("transactions.longest_seconds"),
            },
            recommendation="Identify blocking chains on the Locks tab, shorten long transactions and make sure "
            "applications commit promptly and access rows in a consistent order.",
            expected_impact="Lower tail latency and fewer timeouts for contending workloads.",
            risk="Terminating blockers rolls back their work; fix at the application level where possible.",
            confidence=0.85,
        )
    ]


@rule(
    "GEN-PERF-004",
    engines=("*",),
    kind="performance",
    category="locking",
    severity="medium",
    title="Deadlocks detected",
)
def deadlocks(ctx: EvidenceContext) -> list[RuleFinding]:
    m = ctx.m("deadlocks.count")
    if not m or m.count == 0:
        return []
    total = m.avg * m.count
    if total < 1:
        return []
    return [
        RuleFinding(
            title="Deadlocks occurred",
            finding=f"About {total:.0f} deadlocks were recorded in the last {ctx.window_hours:.0f}h.",
            evidence={"deadlocks": round(total), "window_hours": ctx.window_hours},
            recommendation="Review the engine's deadlock log/graph to find the conflicting statements and enforce a "
            "consistent lock acquisition order in the application.",
            expected_impact="Eliminates failed transactions and retries.",
            risk="None — investigation only.",
            confidence=0.95,
        )
    ]


@rule(
    "GEN-PERF-005", engines=("*",), kind="performance", category="replication", severity="high", title="Replication lag"
)
def replication_lag(ctx: EvidenceContext) -> list[RuleFinding]:
    m = ctx.m("replication.lag_seconds")
    if not m or m.max < 60:
        return []
    return [
        RuleFinding(
            title="Replica is falling behind",
            finding=f"Replication lag peaked at {m.max:.0f}s (average {m.avg:.0f}s).",
            evidence={"peak_lag_seconds": round(m.max, 1), "avg_lag_seconds": round(m.avg, 1)},
            recommendation="Check replica I/O and apply throughput, long-running queries on the replica that delay replay, "
            "and network bandwidth between sites.",
            expected_impact="Keeps RPO within target and keeps read replicas fresh.",
            risk="Failing over to a lagging replica loses recent transactions.",
            confidence=0.9,
            severity="critical" if m.max > 600 else "high",
        )
    ]


@rule(
    "GEN-PERF-006",
    engines=("*",),
    kind="performance",
    category="transactions",
    severity="medium",
    title="Long-running transactions",
)
def long_transactions(ctx: EvidenceContext) -> list[RuleFinding]:
    m = ctx.m("transactions.longest_seconds")
    if not m or m.max < 3600:
        return []
    return [
        RuleFinding(
            title="Transactions open for more than an hour",
            finding=f"The longest open transaction reached {m.max / 60:.0f} minutes.",
            evidence={"longest_transaction_minutes": round(m.max / 60, 1)},
            recommendation="Find the session on the Sessions tab (sorted by transaction age) and confirm with the owner "
            "whether it can be committed or ended.",
            expected_impact="Long transactions hold locks and prevent cleanup of old row versions (bloat / undo growth).",
            risk="Ending a session rolls back its uncommitted work.",
            confidence=0.8,
        )
    ]


@rule(
    "GEN-PERF-007", engines=("*",), kind="performance", category="queries", severity="medium", title="Expensive queries"
)
def expensive_queries(ctx: EvidenceContext) -> list[RuleFinding]:
    """Queries that dominate total execution time or have a high mean latency."""
    if not ctx.query_stats:
        return []
    total = sum(q["total_ms"] for q in ctx.query_stats) or 1
    out = []
    for q in ctx.query_stats[:20]:
        share = q["total_ms"] / total
        if (share >= 0.2 and q["calls"] >= 10) or (q["mean_ms"] >= 1000 and q["calls"] >= 5):
            out.append(
                RuleFinding(
                    title="Query dominates database time" if share >= 0.2 else "Slow query",
                    finding=f"Query {q['query_id']} averages {q['mean_ms']:.1f} ms over {q['calls']:,} calls "
                    f"({share * 100:.0f}% of total measured execution time).",
                    evidence={
                        "query_id": q["query_id"],
                        "mean_ms": round(q["mean_ms"], 2),
                        "calls": q["calls"],
                        "total_ms": round(q["total_ms"]),
                        "share_pct": round(share * 100, 1),
                        "physical_reads": q.get("physical_reads"),
                        "temp_blocks": q.get("temp_blocks"),
                        "query_preview": q["query_text"][:240],
                    },
                    recommendation="Open the query in Execution Plans to check for full scans, missing indexes or spills; "
                    "consider caching or rewriting if it is called very frequently.",
                    expected_impact="Reducing this query's cost directly reduces overall database load.",
                    risk="Index changes affect write throughput and plans of other queries; test first.",
                    confidence=0.7,
                    affected_object=f"query:{q['query_id']}",
                    database=q.get("database"),
                    severity="high" if share >= 0.4 or q["mean_ms"] > 5000 else "medium",
                )
            )
    return out[:5]


@rule(
    "GEN-PERF-008",
    engines=("*",),
    kind="performance",
    category="memory",
    severity="medium",
    title="Temporary spills to disk",
)
def temp_spills(ctx: EvidenceContext) -> list[RuleFinding]:
    m = ctx.m("temp.bytes_per_sec")
    if not m or m.avg < 5 * 1024**2:
        return []
    return [
        RuleFinding(
            title="Sorts and hashes are spilling to disk",
            finding=f"Temporary file writes average {fmt_bytes(m.avg)}/s (peak {fmt_bytes(m.max)}/s).",
            evidence={"avg_temp_bytes_per_sec": round(m.avg), "peak_temp_bytes_per_sec": round(m.max)},
            recommendation="Identify queries with temp usage on the Queries tab; add supporting indexes or raise the "
            "per-operation sort/hash memory for those sessions or roles rather than globally.",
            expected_impact="Faster sorts, hash joins and aggregates; less I/O pressure.",
            risk="Raising per-operation memory globally can exhaust RAM under concurrency.",
            confidence=0.75,
        )
    ]


@rule(
    "GEN-PERF-009",
    engines=("*",),
    kind="performance",
    category="capacity",
    severity="medium",
    title="Sustained high CPU",
)
def cpu(ctx: EvidenceContext) -> list[RuleFinding]:
    m = ctx.m("cpu.percent")
    if not m or m.count < 5 or m.avg < 75:
        return []
    return [
        RuleFinding(
            title="CPU is consistently saturated",
            finding=f"Average CPU {m.avg:.0f}% (peak {m.max:.0f}%).",
            evidence={"avg_cpu_pct": round(m.avg, 1), "peak_cpu_pct": round(m.max, 1)},
            recommendation="Review top queries by CPU time and plan regressions before adding cores.",
            expected_impact="Headroom for peaks and lower queueing latency.",
            risk="None — investigation only.",
            confidence=0.7,
            severity="high" if m.avg > 90 else "medium",
        )
    ]


@rule(
    "GEN-PERF-010",
    engines=("*",),
    kind="performance",
    category="capacity",
    severity="high",
    title="Storage approaching capacity",
)
def storage_capacity(ctx: EvidenceContext) -> list[RuleFinding]:
    """Data volume / allocated storage above 85% (only where the engine or host reports capacity)."""
    m = ctx.m("storage.utilization")
    if not m or m.count < 3 or m.last < 85:
        return []
    cap = ctx.latest.get("storage.capacity_bytes")
    used = ctx.latest.get("storage.database_bytes")
    sev = "critical" if m.last >= 95 else "high" if m.last >= 90 else "medium"
    return [
        RuleFinding(
            title="Storage is approaching capacity",
            finding=f"Storage is {m.last:.1f}% used (peak {m.max:.1f}% over {ctx.window_hours:.0f}h)"
            + (f": {fmt_bytes(used)} of {fmt_bytes(cap)}." if cap and used else "."),
            evidence={
                "used_pct": round(m.last, 1),
                "peak_used_pct": round(m.max, 1),
                "capacity_bytes": cap,
                "database_bytes": used,
            },
            recommendation="Plan a capacity increase or archive/purge data before the volume fills; review the growth "
            "trend on the Storage tab and the largest tables.",
            expected_impact="Avoids write failures and emergency outages when the volume is full.",
            risk="Extending storage is usually online; purging data requires application owner approval.",
            confidence=0.85,
            severity=sev,
        )
    ]


@rule(
    "GEN-PERF-011",
    engines=("*",),
    kind="performance",
    category="queries",
    severity="medium",
    title="Query latency regression",
)
def query_regression(ctx: EvidenceContext) -> list[RuleFinding]:
    """A statement's mean latency in the recent window is well above its own baseline
    (thresholds: platform settings ``performance.regression_*``), optionally after an
    execution plan change (Oracle plan hash, SQL Server cached plan count)."""
    out = []
    for r in ctx.query_regressions:
        if not r.get("regressed"):
            continue
        plan = r.get("plan_changed")
        change = r.get("change_pct")
        sev = "high" if (change or 0) >= 200 or r["recent_mean_ms"] >= 1000 else "medium"
        plan_txt = ""
        if plan and r.get("baseline_plan_id") and r.get("recent_plan_id"):
            plan_txt = f" The dominant plan changed from {r['baseline_plan_id']} to {r['recent_plan_id']}."
        elif plan:
            plan_txt = f" The number of cached plans grew from {r.get('baseline_plans')} to {r.get('recent_plans')}."
        out.append(
            RuleFinding(
                title="Query is slower than its baseline" + (" after a plan change" if plan else ""),
                finding=f"Query {r['fingerprint']} averaged {r['recent_mean_ms']:.1f} ms over the last "
                f"{r['recent_window_hours']}h ({r['recent_calls']:,} calls) against {r['baseline_mean_ms']:.1f} ms over "
                f"the previous {r['baseline_days']} days ({r['baseline_calls']:,} calls)"
                + (f", +{change:.0f}%." if change is not None else ".")
                + plan_txt,
                evidence={
                    k: r.get(k)
                    for k in (
                        "fingerprint",
                        "baseline_mean_ms",
                        "recent_mean_ms",
                        "change_pct",
                        "delta_ms",
                        "baseline_calls",
                        "recent_calls",
                        "plan_changed",
                        "baseline_plan_id",
                        "recent_plan_id",
                        "baseline_plans",
                        "recent_plans",
                        "recent_window_hours",
                        "baseline_days",
                        "excess_ms",
                    )
                },
                recommendation="Compare the statement's recent and baseline periods (Queries → Compare) and its current "
                "plan with a saved earlier plan; look for changed statistics, data volume, parameters or a plan change. "
                "On Oracle, a known good plan can be fixed with a SQL plan baseline; on SQL Server, Query Store plan "
                "forcing does the same.",
                expected_impact="Returns the statement to its baseline latency and removes the extra database time "
                f"(about {r.get('excess_ms', 0) / 1000:,.0f} s in the recent window).",
                risk="Plan forcing and statistics changes affect other statements; validate on a non-production copy.",
                confidence=0.8 if (r.get("recent_calls") or 0) >= 100 else 0.65,
                affected_object=f"query:{r['fingerprint']}",
                database=r.get("database"),
                severity=sev,
            )
        )
    return out[:10]


@rule(
    "GEN-PERF-012",
    engines=("*",),
    kind="performance",
    category="capacity",
    severity="high",
    title="Storage forecast to fill",
)
def storage_forecast(ctx: EvidenceContext) -> list[RuleFinding]:
    """A robust (Theil–Sen) growth trend reaches the capacity of the server volume or of a
    tablespace within ``performance.forecast_warning_days`` (earliest reading of the 90 %
    band). Needs at least 7 days of history and a reported capacity."""
    horizon = int(ctx.settings.get("performance.forecast_warning_days", 30))
    out = []
    for f in ctx.capacity_forecasts:
        days = (f.get("days_to_full") or {}).get("earliest")
        if days is None or days > horizon:
            continue
        likely = f["days_to_full"].get("likely")
        what = {"server": "Server storage", "tablespace": f"Tablespace {f.get('object')}"}.get(
            f["scope"], f"Database {f.get('object')}"
        )
        out.append(
            RuleFinding(
                title=f"{what} may fill within {max(days, 0):.0f} days",
                finding=f"{what} grows by about {fmt_bytes(f['growth_bytes_per_day'])}/day "
                f"(90% band {fmt_bytes(f['growth_low'])}–{fmt_bytes(f['growth_high'])}/day over "
                f"{f['history_days']:.0f} days). At this rate it reaches {fmt_bytes(f['capacity_bytes'])} "
                f"({f['capacity_basis']}) in {days:.0f} days at the earliest"
                + (f", {likely:.0f} days most likely." if likely is not None else "."),
                evidence={
                    "scope": f["scope"],
                    "object": f.get("object"),
                    "current_bytes": f.get("current_bytes"),
                    "capacity_bytes": f.get("capacity_bytes"),
                    "capacity_basis": f.get("capacity_basis"),
                    "growth_bytes_per_day": f.get("growth_bytes_per_day"),
                    "growth_low": f.get("growth_low"),
                    "growth_high": f.get("growth_high"),
                    "days_to_full": f.get("days_to_full"),
                    "history_days": f.get("history_days"),
                    "method": "Theil-Sen median slope, Sen 90% interval",
                },
                recommendation="Extend the volume or the tablespace (or its autoextend limit), or archive/purge data, "
                "before the earliest date; review the largest tables and the growth chart on the Storage tab.",
                expected_impact="Avoids write failures when the storage fills.",
                risk="Extending storage is usually online; purging data needs application owner approval.",
                confidence=0.8 if (f.get("history_days") or 0) >= 21 else 0.65,
                affected_object=f"{f['scope']}:{f.get('object') or 'server'}",
                severity="critical" if days <= 7 else "high" if days <= 14 else "medium",
            )
        )
    return out[:10]
