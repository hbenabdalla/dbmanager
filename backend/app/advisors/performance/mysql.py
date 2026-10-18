"""Performance rules — MySQL / MariaDB."""

from __future__ import annotations

from app.advisors.engine import EvidenceContext, RuleFinding, fmt_bytes, rule


@rule(
    "MYSQL-PERF-001",
    engines=("mysql", "mariadb"),
    kind="performance",
    category="memory",
    severity="high",
    title="InnoDB buffer pool undersized",
)
def mysql_bp(ctx: EvidenceContext) -> list[RuleFinding]:
    size, ram = ctx.tuning.get("innodb_buffer_pool_size"), ctx.tuning.get("ram_bytes")
    hit = ctx.tuning.get("innodb_buffer_pool_hit_ratio")
    if not size or not ram or size >= ram * 0.3:
        return []
    return [
        RuleFinding(
            title="InnoDB buffer pool is small relative to RAM",
            finding=f"innodb_buffer_pool_size is {fmt_bytes(size)} on a host with {fmt_bytes(ram)} RAM (hit ratio {hit}%).",
            evidence={"innodb_buffer_pool_size": fmt_bytes(size), "ram": fmt_bytes(ram), "hit_ratio_pct": hit},
            recommendation="On dedicated servers set innodb_buffer_pool_size to ~50–75% of RAM (resizable online in 5.7+).",
            expected_impact="More of the working set cached in memory.",
            risk="Leave room for per-connection buffers.",
            confidence=0.8 if hit and hit < 98 else 0.6,
            guidance="SET GLOBAL innodb_buffer_pool_size = <bytes>; then persist in my.cnf (or SET PERSIST in 8.0).",
        )
    ]


@rule(
    "MYSQL-PERF-002",
    engines=("mysql", "mariadb"),
    kind="performance",
    category="memory",
    severity="medium",
    title="Temporary tables created on disk",
)
def mysql_tmp(ctx: EvidenceContext) -> list[RuleFinding]:
    pct = ctx.tuning.get("tmp_disk_tables_pct", 0)
    if pct < 25:
        return []
    return [
        RuleFinding(
            title="Many internal temporary tables go to disk",
            finding=f"{pct:.0f}% of internal temporary tables were created on disk.",
            evidence={"tmp_disk_tables_pct": pct},
            recommendation="Find queries using filesort/temporary (EXPLAIN); "
            "add indexes for GROUP BY/ORDER BY; review tmp_table_size and max_heap_table_size together.",
            expected_impact="Faster GROUP BY / ORDER BY / UNION queries.",
            risk="Higher memory per session.",
            confidence=0.7,
        )
    ]


@rule(
    "MYSQL-PERF-003",
    engines=("mysql", "mariadb"),
    kind="performance",
    category="observability",
    severity="info",
    title="Slow query log disabled",
)
def mysql_slow_log(ctx: EvidenceContext) -> list[RuleFinding]:
    if ctx.tuning.get("slow_query_log", "ON") != "OFF":
        return []
    return [
        RuleFinding(
            title="Slow query log is disabled",
            finding="slow_query_log = OFF.",
            evidence={"slow_query_log": "OFF"},
            recommendation="Enable the slow log with a sensible long_query_time (e.g. 1s) or rely on performance_schema digests.",
            expected_impact="Historical visibility into slow statements.",
            risk="Log volume; rotate the log.",
            confidence=1.0,
        )
    ]


@rule(
    "MYSQL-PERF-004",
    engines=("mysql", "mariadb"),
    kind="performance",
    category="schema",
    severity="low",
    title="Tables without primary key",
)
def mysql_no_pk(ctx: EvidenceContext) -> list[RuleFinding]:
    return [
        RuleFinding(
            title=f"{t} has no primary key",
            finding=f"InnoDB table {t} has no primary key.",
            evidence={"table": t},
            recommendation="Add a primary key; InnoDB otherwise uses a hidden clustered key and replication is slower.",
            expected_impact="Better replication apply performance and clustering.",
            risk="Table rebuild.",
            confidence=0.9,
            affected_object=t,
        )
        for t in ctx.tuning.get("tables_without_pk") or []
    ]


@rule(
    "MYSQL-PERF-005",
    engines=("mysql", "mariadb"),
    kind="performance",
    category="indexes",
    severity="low",
    title="Unused secondary indexes",
)
def mysql_unused_indexes(ctx: EvidenceContext) -> list[RuleFinding]:
    """Indexes without a single read since statistics started (performance_schema index
    usage, or MariaDB userstat). Only evaluated with at least 7 days of evidence."""
    rows = ctx.tuning.get("unused_indexes") or []
    if not rows:
        return []
    days = ctx.tuning.get("unused_index_evidence_days")
    by_table: dict[str, list[str]] = {}
    for r in rows:
        by_table.setdefault(f"{r['db']}.{r['table_name']}", []).append(r["index_name"])
    return [
        RuleFinding(
            title=f"{len(idx)} unused index(es) on {table}",
            finding=f"{', '.join(idx[:5])} on {table} had no reads in the last {days} days of server uptime.",
            evidence={
                "table": table,
                "indexes": idx,
                "evidence_days": days,
                "source": ctx.tuning.get("unused_index_source", "performance_schema"),
            },
            recommendation="Confirm the indexes are not needed for constraints, rare reports or replicas, then drop them "
            "(MySQL 8: make them INVISIBLE first to test safely).",
            expected_impact="Less write amplification and buffer pool pressure.",
            risk="Low with ALTER TABLE ... ALTER INDEX ... INVISIBLE first; usage on replicas is not visible here.",
            confidence=0.6 if (days or 0) < 30 else 0.75,
            guidance=f"ALTER TABLE {table} ALTER INDEX `{idx[0]}` INVISIBLE;",
            affected_object=table,
        )
        for table, idx in list(by_table.items())[:10]
    ]


@rule(
    "MYSQL-PERF-006",
    engines=("mysql",),
    kind="performance",
    category="indexes",
    severity="low",
    title="Redundant indexes",
)
def mysql_redundant_indexes(ctx: EvidenceContext) -> list[RuleFinding]:
    return [
        RuleFinding(
            title=f"Redundant index {r['redundant_index']} on {r['db']}.{r['table_name']}",
            finding=f"{r['redundant_index']} ({r['redundant_columns']}) is covered by {r['dominant_index']} ({r['dominant_columns']}).",
            evidence=r,
            recommendation="Drop the redundant index after checking it is not referenced by index hints.",
            expected_impact="Less write overhead and storage.",
            risk="Queries with FORCE INDEX hints on the redundant index will fail.",
            confidence=0.85,
            affected_object=f"{r['db']}.{r['table_name']}",
        )
        for r in (ctx.tuning.get("redundant_indexes") or [])[:10]
    ]


@rule(
    "MYSQL-PERF-007",
    engines=("mysql", "mariadb"),
    kind="performance",
    category="queries",
    severity="medium",
    title="Statements reading without an index",
)
def mysql_no_index(ctx: EvidenceContext) -> list[RuleFinding]:
    out = []
    for r in ctx.tuning.get("full_scan_statements") or []:
        examined, sent = r.get("rows_examined") or 0, max(r.get("rows_sent") or 0, 1)
        if examined < 100_000 or examined / sent < 100:
            continue
        out.append(
            RuleFinding(
                title=f"Full scans in {r['db']}: {r['digest_text'][:60]}",
                finding=f"Executed {r['calls']:,} times without an index, examining {examined:,} rows to return {sent:,}.",
                evidence=r,
                recommendation="EXPLAIN the statement and add an index on the filtered/joined columns.",
                expected_impact="Fewer rows examined per execution.",
                risk="Index maintenance overhead.",
                confidence=0.7,
                database=r.get("db"),
                affected_object=str(r.get("digest") or "")[:32],
            )
        )
    return out[:5]


@rule(
    "MYSQL-PERF-008",
    engines=("mysql", "mariadb"),
    kind="performance",
    category="connections",
    severity="medium",
    title="Connection limit nearly reached historically",
)
def mysql_connection_peak(ctx: EvidenceContext) -> list[RuleFinding]:
    peak, limit = ctx.tuning.get("max_used_connections"), ctx.tuning.get("max_connections")
    if not peak or not limit or peak < 0.9 * limit:
        return []
    return [
        RuleFinding(
            title="Max_used_connections reached 90% of max_connections",
            finding=f"Since start-up, {peak:.0f} of {limit:.0f} allowed connections were in use at the same time.",
            evidence={"max_used_connections": peak, "max_connections": limit},
            recommendation="Use connection pooling in the applications (or ProxySQL/MaxScale) before raising max_connections.",
            expected_impact="Avoids 'Too many connections' errors.",
            risk="Raising max_connections increases per-connection memory.",
            confidence=0.8,
        )
    ]


@rule(
    "MYSQL-PERF-009",
    engines=("mysql", "mariadb"),
    kind="performance",
    category="configuration",
    severity="low",
    title="Query cache enabled",
)
def mysql_query_cache(ctx: EvidenceContext) -> list[RuleFinding]:
    if not ctx.tuning.get("query_cache_enabled"):
        return []
    return [
        RuleFinding(
            title="The query cache is enabled",
            finding="query_cache_type is ON with a non-zero query_cache_size.",
            evidence={k: (ctx.tuning.get("settings") or {}).get(k) for k in ("query_cache_type", "query_cache_size")},
            recommendation="Disable the query cache (it serializes on a global mutex); MySQL 8 removed it entirely.",
            expected_impact="Better concurrency for write-heavy workloads.",
            risk="Read-mostly workloads with identical queries may lose a small benefit.",
            confidence=0.7,
        )
    ]
