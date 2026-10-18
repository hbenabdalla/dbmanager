"""Performance rules — Oracle."""

from __future__ import annotations

from app.advisors.engine import EvidenceContext, RuleFinding, fmt_bytes, rule


@rule(
    "ORA-PERF-001",
    engines=("oracle",),
    kind="performance",
    category="storage",
    severity="high",
    title="Tablespace nearly full",
)
def ora_tablespace(ctx: EvidenceContext) -> list[RuleFinding]:
    out = []
    for t in ctx.tuning.get("tablespaces") or []:
        if t["used_pct"] < 85:
            continue
        out.append(
            RuleFinding(
                title=f"Tablespace {t['name']} is {t['used_pct']:.0f}% full",
                finding=f"{t['name']} usage is {t['used_pct']:.1f}% and autoextend is {'on' if t.get('autoextend') else 'off'}.",
                evidence=t,
                recommendation="Add a datafile or enable AUTOEXTEND with a MAXSIZE; check segment growth.",
                expected_impact="Avoids ORA-01653 allocation failures.",
                risk="Adding files consumes disk.",
                confidence=0.95,
                affected_object=t["name"],
                severity="critical" if t["used_pct"] > 95 else "high",
            )
        )
    return out


@rule(
    "ORA-PERF-002",
    engines=("oracle",),
    kind="performance",
    category="waits",
    severity="medium",
    title="User I/O dominates waits",
)
def ora_user_io(ctx: EvidenceContext) -> list[RuleFinding]:
    waits = {w["wait_class"]: w["pct"] for w in ctx.tuning.get("top_waits") or []}
    if waits.get("User I/O", 0) < 40:
        return []
    return [
        RuleFinding(
            title="User I/O is the dominant wait class",
            finding=f"User I/O accounts for {waits['User I/O']:.0f}% of non-idle wait time.",
            evidence={
                "wait_classes_pct": waits,
                "buffer_cache_hit_ratio": ctx.tuning.get("buffer_cache_hit_ratio"),
                "diagnostics_pack_licensed": ctx.tuning.get("diagnostics_pack_licensed"),
            },
            recommendation="Review top SQL by physical reads in V$SQLSTATS; consider the buffer cache advisory "
            "(V$DB_CACHE_ADVICE) before resizing SGA.",
            expected_impact="Lower response time for I/O-bound statements.",
            risk="ASH/AWR based analysis requires the Diagnostics Pack license.",
            confidence=0.7,
        )
    ]


@rule(
    "ORA-PERF-003",
    engines=("oracle",),
    kind="performance",
    category="memory",
    severity="medium",
    title="PGA over-allocation",
)
def ora_pga(ctx: EvidenceContext) -> list[RuleFinding]:
    n = ctx.tuning.get("pga_over_allocation_count") or 0
    if n <= 0:
        return []
    return [
        RuleFinding(
            title="PGA target is too small for the workload",
            finding=f"V$PGASTAT reports {n} over-allocation events.",
            evidence={
                "over_allocation_count": n,
                "pga_aggregate_target": fmt_bytes(ctx.tuning.get("pga_aggregate_target_bytes")),
            },
            recommendation="Consult V$PGA_TARGET_ADVICE and raise PGA_AGGREGATE_TARGET accordingly.",
            expected_impact="Fewer one-pass/multi-pass sorts and hash joins.",
            risk="Consumes more host memory.",
            confidence=0.8,
        )
    ]


@rule(
    "ORA-PERF-004",
    engines=("oracle",),
    kind="performance",
    category="statistics",
    severity="low",
    title="Stale optimizer statistics",
)
def ora_stale(ctx: EvidenceContext) -> list[RuleFinding]:
    return [
        RuleFinding(
            title=f"Stale statistics on {t['owner']}.{t['table']}",
            finding=f"Statistics were last gathered {t['last_analyzed_days']} days ago.",
            evidence=t,
            recommendation=f"EXEC DBMS_STATS.GATHER_TABLE_STATS('{t['owner']}', '{t['table']}');",
            expected_impact="More accurate cardinality estimates.",
            risk="Plans may change after gathering.",
            confidence=0.7,
            affected_object=f"{t['owner']}.{t['table']}",
        )
        for t in ctx.tuning.get("stale_stats_tables") or []
        if t["last_analyzed_days"] > 30
    ]


@rule(
    "ORA-PERF-005",
    engines=("oracle",),
    kind="performance",
    category="parsing",
    severity="medium",
    title="High hard-parse ratio",
)
def ora_hard_parse(ctx: EvidenceContext) -> list[RuleFinding]:
    ratio, hard = ctx.tuning.get("hard_parse_ratio"), ctx.tuning.get("hard_parses") or 0
    if ratio is None or ratio < 20 or hard < 10_000:
        return []
    return [
        RuleFinding(
            title=f"{ratio:.0f}% of parses are hard parses",
            finding=f"{int(hard):,} hard parses since startup (V$SYSSTAT); typically literals instead of bind variables.",
            evidence={
                "hard_parse_ratio": ratio,
                "hard_parses": hard,
                "cursor_sharing": ctx.tuning.get("cursor_sharing"),
            },
            recommendation="Use bind variables in the application; review top statements in V$SQLSTATS by parse calls. "
            "CURSOR_SHARING=FORCE is a last resort.",
            expected_impact="Less CPU and shared pool / library cache contention.",
            risk="Changing CURSOR_SHARING can change plans.",
            confidence=0.7,
        )
    ]


@rule(
    "ORA-PERF-006",
    engines=("oracle",),
    kind="performance",
    category="redo",
    severity="medium",
    title="Frequent redo log switches",
)
def ora_log_switches(ctx: EvidenceContext) -> list[RuleFinding]:
    n = ctx.tuning.get("log_switches_24h") or 0
    if n < 24 * 4:  # more than ~4 per hour on average
        return []
    return [
        RuleFinding(
            title=f"{n} redo log switches in the last 24 hours",
            finding=f"About {n / 24:.0f} switches per hour; smallest online redo log is "
            f"{fmt_bytes(ctx.tuning.get('redo_log_bytes_min'))}.",
            evidence={"log_switches_24h": n, "redo_log_bytes_min": ctx.tuning.get("redo_log_bytes_min")},
            recommendation="Size online redo logs so switches happen every 15–20 minutes at peak.",
            expected_impact="Fewer checkpoints and 'log file switch' waits.",
            risk="Adding redo log groups is online; dropping old ones requires them to be inactive.",
            confidence=0.7,
        )
    ]


@rule(
    "ORA-PERF-007",
    engines=("oracle",),
    kind="performance",
    category="storage",
    severity="high",
    title="Fast recovery area nearly full",
)
def ora_fra(ctx: EvidenceContext) -> list[RuleFinding]:
    pct = ctx.tuning.get("fra_used_percent")
    if pct is None or pct < 85:
        return []
    return [
        RuleFinding(
            title=f"Fast recovery area is {pct:.0f}% used (net of reclaimable space)",
            finding="When the FRA fills, archiving stops and the database hangs (ORA-00257).",
            evidence={"fra_used_percent": pct},
            recommendation="Back up and delete archived logs (RMAN), or raise DB_RECOVERY_FILE_DEST_SIZE.",
            expected_impact="Avoids an archiver stall.",
            risk="Deleting archived logs before backup breaks recoverability.",
            confidence=0.9,
            severity="critical" if pct >= 95 else "high",
        )
    ]


@rule(
    "ORA-PERF-008",
    engines=("oracle",),
    kind="performance",
    category="objects",
    severity="low",
    title="Invalid objects / unusable indexes",
)
def ora_invalid(ctx: EvidenceContext) -> list[RuleFinding]:
    out = []
    inv = ctx.tuning.get("invalid_objects") or []
    if inv:
        total = sum(int(r.get("n") or 0) for r in inv)
        out.append(
            RuleFinding(
                title=f"{total} invalid object(s) in application schemas",
                finding="Invalid PL/SQL units and views are recompiled on first use (or fail).",
                evidence={"by_owner_type": inv},
                recommendation="Recompile with UTL_RECOMP.RECOMP_SERIAL('<schema>') and fix compilation errors.",
                expected_impact="No runtime recompilation or ORA-04063 errors.",
                risk="Low.",
                confidence=0.8,
            )
        )
    unusable = ctx.tuning.get("unusable_indexes") or []
    if unusable:
        out.append(
            RuleFinding(
                title=f"{len(unusable)} unusable index(es)",
                finding="Unusable indexes are ignored by the optimizer and can make DML fail (unique indexes).",
                evidence={"indexes": unusable},
                recommendation="ALTER INDEX <owner>.<name> REBUILD ONLINE;",
                expected_impact="Restores index access paths.",
                risk="Rebuild consumes I/O and temp space.",
                confidence=0.9,
                severity="medium",
            )
        )
    return out


@rule(
    "ORA-PERF-009",
    engines=("oracle",),
    kind="performance",
    category="availability",
    severity="medium",
    title="Database in NOARCHIVELOG mode",
)
def ora_noarchivelog(ctx: EvidenceContext) -> list[RuleFinding]:
    if ctx.tuning.get("log_mode") != "NOARCHIVELOG" or ctx.environment_tier not in ("production", "staging"):
        return []
    return [
        RuleFinding(
            title="NOARCHIVELOG mode on a production database",
            finding="Point-in-time recovery and online backups are impossible; Data Guard cannot be used.",
            evidence={"log_mode": "NOARCHIVELOG", "environment": ctx.environment_tier},
            recommendation="Enable ARCHIVELOG mode during a maintenance window (SHUTDOWN, STARTUP MOUNT, ALTER DATABASE ARCHIVELOG).",
            expected_impact="Recoverability to any point in time.",
            risk="Requires a restart and FRA/archive destination capacity.",
            confidence=0.95,
        )
    ]
