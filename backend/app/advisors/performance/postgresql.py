"""Performance rules — PostgreSQL."""

from __future__ import annotations

from app.advisors.engine import EvidenceContext, RuleFinding, fmt_bytes, rule


def _setting(ctx: EvidenceContext, name: str) -> dict | None:
    return (ctx.tuning.get("settings") or {}).get(name)


@rule(
    "PG-PERF-001",
    engines=("postgresql",),
    kind="performance",
    category="configuration",
    severity="low",
    title="shared_buffers at default size",
)
def pg_shared_buffers(ctx: EvidenceContext) -> list[RuleFinding]:
    sb = _setting(ctx, "shared_buffers")
    if not sb or not sb.get("bytes") or sb["bytes"] > 128 * 1024**2:
        return []
    hit = ctx.m("cache.hit_ratio")
    return [
        RuleFinding(
            title="shared_buffers is at the 128 MB default",
            finding="shared_buffers is configured at 128 MB, the compiled-in default intended for minimal installations.",
            evidence={
                "shared_buffers": fmt_bytes(sb["bytes"]),
                "avg_cache_hit_ratio_pct": round(hit.avg, 2) if hit else None,
            },
            recommendation="For dedicated servers a common starting point is ~25% of RAM; validate against the cache hit "
            "ratio and OS page cache behaviour after the change.",
            expected_impact="Reduces reads served from the OS cache or disk for hot data.",
            risk="Requires a restart; too large a value reduces OS cache and can hurt performance.",
            confidence=0.6 if not hit or hit.avg > 98 else 0.8,
            guidance="ALTER SYSTEM SET shared_buffers = '<size>'; then schedule a restart. Not applied automatically.",
        )
    ]


@rule(
    "PG-PERF-002",
    engines=("postgresql",),
    kind="performance",
    category="configuration",
    severity="medium",
    title="Frequent requested checkpoints",
)
def pg_checkpoints(ctx: EvidenceContext) -> list[RuleFinding]:
    req = ctx.m("pg.checkpoints_requested.per_sec")
    timed = ctx.m("pg.checkpoints_timed.per_sec")
    if not req or not timed or req.count < 5:
        return []
    r, t = req.avg, timed.avg
    if r + t <= 0 or r / (r + t) < 0.3:
        return []
    mws = _setting(ctx, "max_wal_size")
    return [
        RuleFinding(
            title="Checkpoints are triggered by WAL volume",
            finding=f"{r / (r + t) * 100:.0f}% of checkpoints were requested (WAL-size driven) rather than timed.",
            evidence={"requested_share_pct": round(r / (r + t) * 100, 1), "max_wal_size": mws and mws.get("value")},
            recommendation="Increase max_wal_size so checkpoints are spread by checkpoint_timeout, and keep "
            "checkpoint_completion_target at 0.9.",
            expected_impact="Smoother I/O and fewer full-page writes after each checkpoint.",
            risk="Longer crash recovery and more WAL disk usage.",
            confidence=0.75,
            guidance="ALTER SYSTEM SET max_wal_size = '4GB'; SELECT pg_reload_conf(); (reload only, no restart).",
        )
    ]


@rule(
    "PG-PERF-003",
    engines=("postgresql",),
    kind="performance",
    category="maintenance",
    severity="medium",
    title="Table bloat from dead tuples",
)
def pg_dead_tuples(ctx: EvidenceContext) -> list[RuleFinding]:
    out = []
    for t in ctx.tuning.get("dead_tuple_tables") or []:
        live, dead = t.get("n_live_tup") or 0, t.get("n_dead_tup") or 0
        if dead < 10_000 or dead / max(live + dead, 1) < 0.2:
            continue
        name = f"{t['schemaname']}.{t['relname']}"
        out.append(
            RuleFinding(
                title=f"Dead tuples accumulating in {name}",
                finding=f"{name} has {dead:,} dead vs {live:,} live tuples ({dead / (live + dead) * 100:.0f}%).",
                evidence={
                    "table": name,
                    "n_dead_tup": dead,
                    "n_live_tup": live,
                    "last_autovacuum": t.get("last_autovacuum"),
                    "total_size": fmt_bytes(t.get("total_bytes")),
                },
                recommendation="Check whether long transactions prevent cleanup; consider lowering "
                "autovacuum_vacuum_scale_factor for this table.",
                expected_impact="Smaller tables and indexes, faster scans.",
                risk="Manual VACUUM adds I/O load; VACUUM FULL takes an exclusive lock (avoid in business hours).",
                confidence=0.8,
                affected_object=name,
                database=ctx.tuning.get("database"),
                guidance=f"ALTER TABLE {name} SET (autovacuum_vacuum_scale_factor = 0.02);",
            )
        )
    return out[:5]


@rule(
    "PG-PERF-004",
    engines=("postgresql",),
    kind="performance",
    category="indexes",
    severity="medium",
    title="Large table read by sequential scans",
)
def pg_seq_scans(ctx: EvidenceContext) -> list[RuleFinding]:
    out = []
    for t in ctx.tuning.get("seq_scan_tables") or []:
        if (t.get("total_bytes") or 0) < 100 * 1024**2 or t["seq_scan"] < 100:
            continue
        if t["idx_scan"] and t["seq_scan"] < t["idx_scan"]:
            continue
        name = f"{t['schemaname']}.{t['relname']}"
        out.append(
            RuleFinding(
                title=f"Index opportunity on {name}",
                finding=f"{name} ({fmt_bytes(t.get('total_bytes'))}) had {t['seq_scan']:,} sequential scans reading "
                f"{t['seq_tup_read']:,} rows vs {t['idx_scan']:,} index scans.",
                evidence={
                    "table": name,
                    "seq_scan": t["seq_scan"],
                    "seq_tup_read": t["seq_tup_read"],
                    "idx_scan": t["idx_scan"],
                    "size": fmt_bytes(t.get("total_bytes")),
                    "stats_since": ctx.tuning.get("stats_reset"),
                },
                recommendation="Find the queries scanning this table (Queries tab, filter by table name) and EXPLAIN them "
                "to choose index columns.",
                expected_impact="Large reductions in I/O and latency for selective queries.",
                risk="New indexes slow writes slightly and consume storage; build CONCURRENTLY.",
                confidence=0.65,
                affected_object=name,
                database=ctx.tuning.get("database"),
            )
        )
    return out[:5]


@rule(
    "PG-PERF-005",
    engines=("postgresql",),
    kind="performance",
    category="indexes",
    severity="low",
    title="Unused indexes",
)
def pg_unused_indexes(ctx: EvidenceContext) -> list[RuleFinding]:
    out = []
    for i in ctx.tuning.get("unused_indexes") or []:
        if (i.get("size_bytes") or 0) < 50 * 1024**2:
            continue
        name = f"{i['schemaname']}.{i['index_name']}"
        out.append(
            RuleFinding(
                title=f"Index {name} is never used",
                finding=f"{name} on {i['table_name']} ({fmt_bytes(i['size_bytes'])}) has 0 scans since statistics reset.",
                evidence={
                    "index": name,
                    "table": i["table_name"],
                    "idx_scan": 0,
                    "size": fmt_bytes(i["size_bytes"]),
                    "stats_since": ctx.tuning.get("stats_reset"),
                },
                recommendation="Confirm on replicas and across month-end workloads before dropping; statistics are per node.",
                expected_impact="Faster writes, less storage and vacuum work.",
                risk="Dropping an index needed by an infrequent job can cause severe regressions. DESTRUCTIVE action.",
                confidence=0.55,
                affected_object=name,
                database=ctx.tuning.get("database"),
                guidance=f"DROP INDEX CONCURRENTLY {name};  -- only after confirmation, via the SQL workspace",
            )
        )
    return out[:5]


@rule(
    "PG-PERF-006",
    engines=("postgresql",),
    kind="performance",
    category="indexes",
    severity="low",
    title="Duplicate indexes",
)
def pg_duplicate_indexes(ctx: EvidenceContext) -> list[RuleFinding]:
    out = []
    for d in ctx.tuning.get("duplicate_indexes") or []:
        name = f"{d['schema']}.{d['table_name']}"
        out.append(
            RuleFinding(
                title=f"Duplicate indexes on {name}",
                finding=f"Indexes {', '.join(d['indexes'])} have identical definitions.",
                evidence={"table": name, "indexes": d["indexes"], "combined_size": fmt_bytes(d.get("total_bytes"))},
                recommendation="Keep one of the identical indexes (prefer the one backing a constraint).",
                expected_impact="Less write amplification and storage.",
                risk="Low; verify no hints reference the index name.",
                confidence=0.9,
                affected_object=name,
                database=ctx.tuning.get("database"),
            )
        )
    return out


@rule(
    "PG-PERF-007",
    engines=("postgresql",),
    kind="performance",
    category="sessions",
    severity="medium",
    title="Idle-in-transaction sessions",
)
def pg_idle_in_txn(ctx: EvidenceContext) -> list[RuleFinding]:
    m = ctx.m("pg.idle_in_transaction_longest_seconds")
    if not m or m.max < 300:
        return []
    return [
        RuleFinding(
            title="Sessions sit idle inside open transactions",
            finding=f"A session stayed 'idle in transaction' for up to {m.max / 60:.0f} minutes.",
            evidence={
                "longest_idle_in_txn_minutes": round(m.max / 60, 1),
                "idle_in_txn_sessions_now": ctx.latest.get("connections.idle_in_transaction"),
            },
            recommendation="Fix application transaction handling and set idle_in_transaction_session_timeout for "
            "application roles.",
            expected_impact="Releases locks and allows vacuum to clean up.",
            risk="Timeouts abort affected transactions.",
            confidence=0.85,
            guidance="ALTER ROLE app_user SET idle_in_transaction_session_timeout = '5min';",
        )
    ]


@rule(
    "PG-PERF-008",
    engines=("postgresql",),
    kind="performance",
    category="observability",
    severity="info",
    title="pg_stat_statements not available",
)
def pg_no_pgss(ctx: EvidenceContext) -> list[RuleFinding]:
    if ctx.tuning.get("pg_stat_statements", True):
        return []
    return [
        RuleFinding(
            title="Query statistics are not being collected",
            finding="The pg_stat_statements extension is not installed or not preloaded, so query-level analysis is unavailable.",
            evidence={"pg_stat_statements": False},
            recommendation="Add pg_stat_statements to shared_preload_libraries, restart, then CREATE EXTENSION pg_stat_statements;",
            expected_impact="Enables top-query analysis, latency trends and query recommendations.",
            risk="Requires a restart; overhead is typically 1–2%.",
            confidence=1.0,
        )
    ]


@rule(
    "PG-PERF-009",
    engines=("postgresql",),
    kind="performance",
    category="maintenance",
    severity="low",
    title="Stale planner statistics",
)
def pg_stale_stats(ctx: EvidenceContext) -> list[RuleFinding]:
    out = []
    for t in ctx.tuning.get("dead_tuple_tables") or []:
        mods, live = t.get("n_mod_since_analyze") or 0, t.get("n_live_tup") or 0
        if mods < 50_000 or mods < live * 0.5:
            continue
        name = f"{t['schemaname']}.{t['relname']}"
        out.append(
            RuleFinding(
                title=f"Statistics are stale for {name}",
                finding=f"{mods:,} rows modified since the last ANALYZE of {name} ({live:,} live rows).",
                evidence={
                    "table": name,
                    "n_mod_since_analyze": mods,
                    "n_live_tup": live,
                    "last_analyze": t.get("last_analyze") or t.get("last_autoanalyze"),
                },
                recommendation=f"Run ANALYZE {name}; and review autovacuum_analyze_scale_factor for this table.",
                expected_impact="More accurate row estimates and better plans.",
                risk="LOW_RISK_CHANGE: ANALYZE reads a sample.",
                confidence=0.8,
                affected_object=name,
                database=ctx.tuning.get("database"),
            )
        )
    return out[:5]
