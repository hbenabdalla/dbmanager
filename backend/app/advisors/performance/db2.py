"""Performance rules — Db2 LUW."""

from __future__ import annotations

from app.advisors.engine import EvidenceContext, RuleFinding, rule


@rule(
    "DB2-PERF-001",
    engines=("db2",),
    kind="performance",
    category="memory",
    severity="high",
    title="Low buffer pool hit ratio",
)
def db2_bufferpool(ctx: EvidenceContext) -> list[RuleFinding]:
    return [
        RuleFinding(
            title=f"Buffer pool {b['name']} hit ratio {b['hit_ratio']:.1f}%",
            finding=f"Buffer pool {b['name']} serves only {b['hit_ratio']:.1f}% of data page requests from memory.",
            evidence=b,
            recommendation="Review MON_GET_BUFFERPOOL and tablespace-to-bufferpool assignment; consider STMM or resizing.",
            expected_impact="Fewer physical reads.",
            risk="Memory reallocation may affect other pools.",
            confidence=0.75,
            affected_object=b["name"],
        )
        for b in ctx.tuning.get("bufferpools") or []
        if b["hit_ratio"] < 95
    ]


@rule(
    "DB2-PERF-002",
    engines=("db2",),
    kind="performance",
    category="maintenance",
    severity="medium",
    title="Tables need REORG",
)
def db2_reorg(ctx: EvidenceContext) -> list[RuleFinding]:
    return [
        RuleFinding(
            title=f"{t['table']} needs REORG",
            finding=f"REORGCHK formulas F1={t['f1']} F2={t['f2']} F3={t['f3']} exceed thresholds.",
            evidence=t,
            recommendation=f"Schedule REORG TABLE {t['table']} INPLACE followed by RUNSTATS.",
            expected_impact="Better clustering and fewer pages scanned.",
            risk="REORG consumes I/O and log space.",
            confidence=0.7,
            affected_object=t["table"],
        )
        for t in ctx.tuning.get("tables_needing_reorg") or []
    ]


@rule(
    "DB2-PERF-003",
    engines=("db2",),
    kind="performance",
    category="statistics",
    severity="low",
    title="RUNSTATS overdue",
)
def db2_runstats(ctx: EvidenceContext) -> list[RuleFinding]:
    return [
        RuleFinding(
            title=f"RUNSTATS overdue on {t['table']}",
            finding=f"Statistics are {t['stats_time_days']} days old.",
            evidence=t,
            recommendation=f"RUNSTATS ON TABLE {t['table']} WITH DISTRIBUTION AND DETAILED INDEXES ALL",
            expected_impact="Better access plans.",
            risk="Packages may need rebinding to pick up new plans.",
            confidence=0.7,
            affected_object=t["table"],
        )
        for t in ctx.tuning.get("tables_needing_runstats") or []
        if t["stats_time_days"] > 30
    ]


@rule(
    "DB2-PERF-004",
    engines=("db2",),
    kind="performance",
    category="memory",
    severity="medium",
    title="Sort and package cache overflows",
)
def db2_overflows(ctx: EvidenceContext) -> list[RuleFinding]:
    sort, pkg = ctx.tuning.get("sort_overflow_pct") or 0, ctx.tuning.get("package_cache_overflows") or 0
    if sort < 3 and pkg == 0:
        return []
    return [
        RuleFinding(
            title="Sort heap / package cache overflows",
            finding=f"{sort:.1f}% of sorts overflowed and the package cache overflowed {pkg} times.",
            evidence={"sort_overflow_pct": sort, "package_cache_overflows": pkg},
            recommendation="Review SORTHEAP/SHEAPTHRES_SHR and PCKCACHESZ (or let STMM manage them).",
            expected_impact="Fewer spills and recompilations.",
            risk="Higher memory consumption.",
            confidence=0.7,
        )
    ]


@rule(
    "DB2-PERF-005", engines=("db2",), kind="performance", category="locking", severity="high", title="Lock escalations"
)
def db2_escalations(ctx: EvidenceContext) -> list[RuleFinding]:
    n = ctx.tuning.get("lock_escalations") or 0
    if n <= 0:
        return []
    return [
        RuleFinding(
            title="Lock escalations occurring",
            finding=f"{n} lock escalations since activation.",
            evidence={"lock_escalations": n},
            recommendation="Increase LOCKLIST/MAXLOCKS or reduce transaction size.",
            expected_impact="Less blocking from table-level locks.",
            risk="More memory for lock list.",
            confidence=0.8,
        )
    ]


@rule(
    "DB2-PERF-006",
    engines=("db2",),
    kind="performance",
    category="logging",
    severity="high",
    title="Active transaction log nearly full",
)
def db2_log_full(ctx: EvidenceContext) -> list[RuleFinding]:
    pct = ctx.tuning.get("log_used_percent")
    if pct is None or pct < 75:
        return []
    return [
        RuleFinding(
            title=f"Active log space is {pct:.0f}% used",
            finding="When the active log fills, transactions fail with SQL0964C (log full).",
            evidence={"log_used_percent": pct, "logarchmeth1": ctx.tuning.get("logarchmeth1")},
            recommendation="Find the application holding the oldest transaction (MON_GET_TRANSACTION_LOG "
            "APPLID_HOLDING_OLDEST_XACT); review LOGPRIMARY/LOGSECOND/LOGFILSIZ.",
            expected_impact="Avoids log-full failures.",
            risk="Forcing the oldest application rolls back its work.",
            confidence=0.85,
            severity="critical" if pct >= 90 else "high",
        )
    ]


@rule(
    "DB2-PERF-007",
    engines=("db2",),
    kind="performance",
    category="maintenance",
    severity="high",
    title="Tables in REORG-pending state",
)
def db2_reorg_pending(ctx: EvidenceContext) -> list[RuleFinding]:
    tables = ctx.tuning.get("reorg_pending_tables") or []
    if not tables:
        return []
    return [
        RuleFinding(
            title=f"{len(tables)} table(s) are REORG pending",
            finding="A REORG-pending table rejects most access until it is reorganized (after certain ALTER TABLE operations).",
            evidence={"tables": tables},
            recommendation="Plan REORG TABLE for each table in a maintenance window (DB Manager does not run it).",
            expected_impact="Restores full access to the tables.",
            risk="Classic REORG needs exclusive access; INPLACE is not allowed for every reorg-pending case.",
            confidence=0.95,
        )
    ]


@rule(
    "DB2-PERF-008",
    engines=("db2",),
    kind="performance",
    category="memory",
    severity="low",
    title="Low package cache hit ratio",
)
def db2_pkg_cache(ctx: EvidenceContext) -> list[RuleFinding]:
    hit = ctx.tuning.get("package_cache_hit_ratio")
    if hit is None or hit >= 80:
        return []
    return [
        RuleFinding(
            title=f"Package cache hit ratio {hit:.0f}%",
            finding="Many statements are compiled instead of reused from the package cache.",
            evidence={"package_cache_hit_ratio": hit},
            recommendation="Use parameter markers in dynamic SQL; consider PCKCACHESZ AUTOMATIC (STMM).",
            expected_impact="Less compilation CPU.",
            risk="Larger cache uses database memory.",
            confidence=0.65,
        )
    ]


@rule(
    "DB2-PERF-009",
    engines=("db2",),
    kind="performance",
    category="locking",
    severity="medium",
    title="Deadlocks and lock timeouts",
)
def db2_deadlocks(ctx: EvidenceContext) -> list[RuleFinding]:
    d, t = ctx.tuning.get("deadlocks") or 0, ctx.tuning.get("lock_timeouts") or 0
    if d < 10 and t < 50:
        return []
    return [
        RuleFinding(
            title=f"{d} deadlocks and {t} lock timeouts since activation",
            finding="MON_GET_WORKLOAD request metrics report recurring lock conflicts.",
            evidence={"deadlocks": d, "lock_timeouts": t},
            recommendation="Create a locking event monitor (CREATE EVENT MONITOR ... FOR LOCKING) to capture participants, "
            "then fix access order or isolation level.",
            expected_impact="Fewer rolled-back transactions.",
            risk="Event monitors write to tables; size them.",
            confidence=0.75,
        )
    ]
