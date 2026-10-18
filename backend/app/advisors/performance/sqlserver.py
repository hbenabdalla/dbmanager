"""Performance rules — SQL Server."""

from __future__ import annotations

from app.advisors.engine import EvidenceContext, RuleFinding, rule


@rule(
    "MSSQL-PERF-001",
    engines=("mssql",),
    kind="performance",
    category="configuration",
    severity="medium",
    title="max server memory not configured",
)
def mssql_max_memory(ctx: EvidenceContext) -> list[RuleFinding]:
    if ctx.tuning.get("max_server_memory_mb") != 2147483647:
        return []
    phys = ctx.tuning.get("physical_memory_mb")
    return [
        RuleFinding(
            title="SQL Server may consume all host memory",
            finding="'max server memory (MB)' is at its default (unlimited).",
            evidence={"max_server_memory_mb": 2147483647, "physical_memory_mb": phys},
            recommendation="Set max server memory to leave headroom for the OS (e.g. physical memory minus 10–20%).",
            expected_impact="Prevents OS memory pressure and paging.",
            risk="Takes effect immediately; too low a value hurts caching.",
            confidence=0.85,
            guidance="EXEC sp_configure 'max server memory (MB)', <value>; RECONFIGURE;",
        )
    ]


@rule(
    "MSSQL-PERF-002",
    engines=("mssql",),
    kind="performance",
    category="waits",
    severity="high",
    title="Page I/O latch waits dominate",
)
def mssql_pageiolatch(ctx: EvidenceContext) -> list[RuleFinding]:
    waits = {w["wait_type"]: w["pct"] for w in ctx.tuning.get("top_waits") or []}
    if waits.get("PAGEIOLATCH_SH", 0) < 30:
        return []
    return [
        RuleFinding(
            title="Buffer pool pressure (PAGEIOLATCH)",
            finding=f"PAGEIOLATCH_SH represents {waits['PAGEIOLATCH_SH']:.0f}% of waits; page life expectancy is "
            f"{ctx.tuning.get('page_life_expectancy')}s.",
            evidence={"waits_pct": waits, "page_life_expectancy": ctx.tuning.get("page_life_expectancy")},
            recommendation="Review missing-index DMVs and top queries by logical reads; then consider memory.",
            expected_impact="Fewer physical reads and better throughput.",
            risk="Investigation only.",
            confidence=0.75,
        )
    ]


@rule(
    "MSSQL-PERF-003",
    engines=("mssql",),
    kind="performance",
    category="indexes",
    severity="medium",
    title="High-impact missing index",
)
def mssql_missing_index(ctx: EvidenceContext) -> list[RuleFinding]:
    return [
        RuleFinding(
            title=f"Missing index on {m['table']}",
            finding=f"The optimizer reports an estimated {m['avg_user_impact']:.0f}% improvement for {m['user_seeks']:,} seeks.",
            evidence=m,
            recommendation=f"Evaluate an index on {m['table']} ({m['equality_columns']}). Missing-index DMVs ignore "
            "existing similar indexes — consolidate where possible.",
            expected_impact="Lower logical reads for matching queries.",
            risk="Write overhead and storage.",
            confidence=0.6,
            affected_object=m["table"],
        )
        for m in ctx.tuning.get("missing_indexes") or []
        if m["avg_user_impact"] > 70
    ]


@rule(
    "MSSQL-PERF-004",
    engines=("mssql",),
    kind="performance",
    category="tempdb",
    severity="low",
    title="Single tempdb data file",
)
def mssql_tempdb(ctx: EvidenceContext) -> list[RuleFinding]:
    files, cpus = ctx.tuning.get("tempdb_data_files"), ctx.tuning.get("cpu_count")
    if not files or not cpus or files >= min(cpus, 8):
        return []
    return [
        RuleFinding(
            title="tempdb has fewer data files than recommended",
            finding=f"tempdb has {files} data file(s) on a {cpus}-CPU server.",
            evidence={"tempdb_data_files": files, "cpu_count": cpus},
            recommendation="Use one equally sized tempdb data file per core up to 8 to reduce allocation contention.",
            expected_impact="Less PAGELATCH contention on allocation pages.",
            risk="Requires planning file placement.",
            confidence=0.7,
        )
    ]


@rule(
    "MSSQL-PERF-005",
    engines=("mssql",),
    kind="performance",
    category="observability",
    severity="info",
    title="Query Store disabled",
)
def mssql_query_store(ctx: EvidenceContext) -> list[RuleFinding]:
    if ctx.tuning.get("query_store_enabled") is not False:
        return []
    return [
        RuleFinding(
            title="Query Store is not enabled",
            finding="Query Store is off for user databases, so plan history is not kept.",
            evidence={"query_store_enabled": False},
            recommendation="ALTER DATABASE [db] SET QUERY_STORE = ON;",
            expected_impact="Plan regression detection and history.",
            risk="Small overhead and storage in each database.",
            confidence=1.0,
        )
    ]


@rule(
    "MSSQL-PERF-006",
    engines=("mssql",),
    kind="performance",
    category="configuration",
    severity="medium",
    title="AUTO_SHRINK / AUTO_CLOSE enabled",
)
def mssql_auto_shrink(ctx: EvidenceContext) -> list[RuleFinding]:
    out = []
    for opt, key in (("AUTO_SHRINK", "auto_shrink_dbs"), ("AUTO_CLOSE", "auto_close_dbs")):
        for db in ctx.tuning.get(key) or []:
            out.append(
                RuleFinding(
                    title=f"{opt} is on for {db}",
                    finding=f"{opt} = ON"
                    + (
                        " repeatedly shrinks and regrows files, fragmenting indexes."
                        if opt == "AUTO_SHRINK"
                        else " closes the database after the last connection, flushing its caches."
                    ),
                    evidence={"database": db, "option": opt},
                    recommendation=f"ALTER DATABASE [{db}] SET {opt} OFF;",
                    expected_impact="Stable file sizes and warm caches.",
                    risk="None for typical workloads.",
                    confidence=0.95,
                    database=db,
                )
            )
    return out


@rule(
    "MSSQL-PERF-007",
    engines=("mssql",),
    kind="performance",
    category="indexes",
    severity="low",
    title="Unused nonclustered indexes",
)
def mssql_unused_indexes(ctx: EvidenceContext) -> list[RuleFinding]:
    uptime = ctx.tuning.get("uptime_seconds") or 0
    idx = [i for i in ctx.tuning.get("unused_indexes") or [] if (i.get("user_updates") or 0) > 0]
    if uptime < 7 * 86400 or not idx:
        return []
    return [
        RuleFinding(
            title=f"{len(idx)} index(es) maintained but never read in {ctx.tuning.get('database')}",
            finding=f"No seeks, scans or lookups since the last restart ({uptime / 86400:.0f} days), but writes keep "
            "maintaining them.",
            evidence={"indexes": idx[:20], "uptime_days": round(uptime / 86400, 1)},
            recommendation="Confirm with month-end/batch workloads, then disable (ALTER INDEX … DISABLE) before dropping.",
            expected_impact="Faster writes and less storage and log.",
            risk="Usage stats reset on restart and on index rebuild in some versions; verify before dropping.",
            confidence=0.6,
            database=ctx.tuning.get("database"),
        )
    ]


@rule(
    "MSSQL-PERF-008",
    engines=("mssql",),
    kind="performance",
    category="statistics",
    severity="low",
    title="Stale statistics",
)
def mssql_stale_stats(ctx: EvidenceContext) -> list[RuleFinding]:
    stale = ctx.tuning.get("stale_statistics") or []
    if not stale:
        return []
    return [
        RuleFinding(
            title=f"{len(stale)} statistics object(s) with many modifications in {ctx.tuning.get('database')}",
            finding="More than 20% of rows changed since the statistics were last updated.",
            evidence={"statistics": stale[:20]},
            recommendation="UPDATE STATISTICS [schema].[table] (or schedule a statistics maintenance job).",
            expected_impact="More accurate cardinality estimates and plans.",
            risk="Sampling scans the table; run off-peak for large tables.",
            confidence=0.7,
            database=ctx.tuning.get("database"),
        )
    ]


@rule(
    "MSSQL-PERF-009",
    engines=("mssql",),
    kind="performance",
    category="storage",
    severity="high",
    title="Transaction log waiting for a log backup",
)
def mssql_log_backup(ctx: EvidenceContext) -> list[RuleFinding]:
    return [
        RuleFinding(
            title=f"The log of {db} cannot be truncated until a log backup runs",
            finding="log_reuse_wait_desc = LOG_BACKUP: FULL recovery model without recent log backups.",
            evidence={"database": db, "log_reuse_wait": "LOG_BACKUP"},
            recommendation="Schedule regular log backups, or switch to SIMPLE recovery if point-in-time restore is not needed.",
            expected_impact="Bounded log growth.",
            risk="Switching recovery model breaks the log backup chain.",
            confidence=0.8,
            database=db,
        )
        for db in ctx.tuning.get("log_backup_waits") or []
    ]


@rule(
    "MSSQL-PERF-010",
    engines=("mssql",),
    kind="performance",
    category="configuration",
    severity="low",
    title="Parallelism settings at defaults",
)
def mssql_parallelism(ctx: EvidenceContext) -> list[RuleFinding]:
    t = ctx.tuning
    maxdop, ctfp, cpus = t.get("maxdop"), t.get("cost_threshold_for_parallelism"), t.get("cpu_count") or 0
    issues = []
    if maxdop == 0 and cpus > 8:
        issues.append(f"MAXDOP = 0 (all {cpus} CPUs per query)")
    if ctfp == 5:
        issues.append("cost threshold for parallelism = 5 (default)")
    if not issues:
        return []
    return [
        RuleFinding(
            title="Parallelism uses default settings",
            finding="; ".join(issues) + ".",
            evidence={"maxdop": maxdop, "cost_threshold_for_parallelism": ctfp, "cpu_count": cpus},
            recommendation="Set MAXDOP per Microsoft guidance (≤ 8, cores per NUMA node) and raise the cost threshold "
            "(commonly 25–50) after checking CXPACKET/CXCONSUMER waits.",
            expected_impact="Fewer small queries going parallel; less CPU contention.",
            risk="Workload dependent: validate on a test system first.",
            confidence=0.6,
            guidance="EXEC sp_configure 'cost threshold for parallelism', 50; RECONFIGURE;",
        )
    ]
