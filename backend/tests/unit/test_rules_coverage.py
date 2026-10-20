"""Fire / no-fire coverage for every advisor rule, plus a guard that every rule in
``RULES`` is named by at least one unit test.

Each fire case calls the rule directly (so an exception is a test failure rather than
being swallowed by :func:`evaluate`) and also checks that :func:`evaluate` reports it for
the engine, which verifies registration, kind and engine scoping.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from app.advisors.engine import RULES, EvidenceContext, MetricWindow, Rule, catalog, evaluate

MiB = 1024**2
GiB = 1024**3

catalog()  # imports the rule modules so RULES is populated at collection time
RULES_BY_ID: dict[str, Rule] = {r.rule_id: r for r in RULES}


def ctx(engine: str = "postgresql", **kw: Any) -> EvidenceContext:
    base: dict[str, Any] = dict(
        server_id="s1",
        server_name="srv",
        engine=engine,
        environment_tier="production",
        tls_mode="verify-full",
        is_demo=False,
        window_hours=6,
    )
    base.update(kw)
    return EvidenceContext(**base)


def mw(avg: float, mn: float, mx: float, last: float, count: int = 60) -> MetricWindow:
    return MetricWindow(avg=avg, min=mn, max=mx, last=last, count=count)


def run(rule_id: str, c: EvidenceContext) -> list[tuple[str, dict[str, Any]]]:
    """Evaluate one rule directly; returns (effective severity, evidence) per finding."""
    r = RULES_BY_ID[rule_id]
    assert r.applies_to(c.engine), f"{rule_id} does not apply to {c.engine}"
    return [(f.severity or r.severity, f.evidence) for f in r.evaluate(c) or []]


def _contains(evidence: dict[str, Any], expected: dict[str, Any]) -> bool:
    return all(k in evidence and evidence[k] == v for k, v in expected.items())


# (case id, rule id, engine, kind, context kwargs, expected severity, evidence subset)
FIRE: list[tuple[str, str, str, str, dict[str, Any], str, dict[str, Any]]] = [
    # ---- engine-neutral performance -----------------------------------------------
    (
        "GEN-PERF-003",
        "GEN-PERF-003",
        "postgresql",
        "performance",
        {"metrics": {"sessions.blocked": mw(2.5, 0, 7, 3)}, "latest": {"transactions.longest_seconds": 900.0}},
        "high",
        {"avg_blocked_sessions": 2.5, "peak_blocked_sessions": 7, "longest_transaction_seconds": 900.0},
    ),
    (
        "GEN-PERF-004",
        "GEN-PERF-004",
        "mysql",
        "performance",
        {"metrics": {"deadlocks.count": mw(0.25, 0, 2, 0, 60)}},
        "medium",
        {"deadlocks": 15, "window_hours": 6},
    ),
    (
        "GEN-PERF-005-critical",
        "GEN-PERF-005",
        "postgresql",
        "performance",
        {"metrics": {"replication.lag_seconds": mw(120, 10, 900, 30)}},
        "critical",
        {"peak_lag_seconds": 900, "avg_lag_seconds": 120},
    ),
    (
        "GEN-PERF-005-high",
        "GEN-PERF-005",
        "mssql",
        "performance",
        {"metrics": {"replication.lag_seconds": mw(80, 5, 300, 40)}},
        "high",
        {"peak_lag_seconds": 300, "avg_lag_seconds": 80},
    ),
    (
        "GEN-PERF-006",
        "GEN-PERF-006",
        "oracle",
        "performance",
        {"metrics": {"transactions.longest_seconds": mw(1200, 0, 7200, 7200)}},
        "medium",
        {"longest_transaction_minutes": 120.0},
    ),
    (
        "GEN-PERF-008",
        "GEN-PERF-008",
        "postgresql",
        "performance",
        {"metrics": {"temp.bytes_per_sec": mw(8 * MiB, 0, 40 * MiB, 2 * MiB)}},
        "medium",
        {"avg_temp_bytes_per_sec": 8 * MiB, "peak_temp_bytes_per_sec": 40 * MiB},
    ),
    (
        "GEN-PERF-009-high",
        "GEN-PERF-009",
        "postgresql",
        "performance",
        {"metrics": {"cpu.percent": mw(93, 80, 100, 95, 30)}},
        "high",
        {"avg_cpu_pct": 93, "peak_cpu_pct": 100},
    ),
    (
        "GEN-PERF-009-medium",
        "GEN-PERF-009",
        "db2",
        "performance",
        {"metrics": {"cpu.percent": mw(80, 60, 97, 78, 30)}},
        "medium",
        {"avg_cpu_pct": 80, "peak_cpu_pct": 97},
    ),
    # GEN-PERF-010 grades on the latest value (MetricWindow.last), not on the peak
    (
        "GEN-PERF-010-medium-latest-not-peak",
        "GEN-PERF-010",
        "postgresql",
        "performance",
        {
            "metrics": {"storage.utilization": mw(80, 70, 97, 88, 30)},
            "latest": {"storage.capacity_bytes": 100 * GiB, "storage.database_bytes": 88 * GiB},
        },
        "medium",
        {"used_pct": 88, "peak_used_pct": 97, "capacity_bytes": 100 * GiB, "database_bytes": 88 * GiB},
    ),
    (
        "GEN-PERF-010-high",
        "GEN-PERF-010",
        "postgresql",
        "performance",
        {"metrics": {"storage.utilization": mw(88, 86, 92, 91, 30)}},
        "high",
        {"used_pct": 91, "peak_used_pct": 92},
    ),
    (
        "GEN-PERF-010-critical",
        "GEN-PERF-010",
        "postgresql",
        "performance",
        {"metrics": {"storage.utilization": mw(93, 90, 96, 96, 30)}},
        "critical",
        {"used_pct": 96, "peak_used_pct": 96},
    ),
    (
        "GEN-PERF-011",
        "GEN-PERF-011",
        "oracle",
        "performance",
        {
            "query_regressions": [
                {
                    "database": "ORCL",
                    "fingerprint": "8f2kq1",
                    "regressed": True,
                    "plan_changed": True,
                    "baseline_mean_ms": 12.0,
                    "recent_mean_ms": 42.0,
                    "change_pct": 250.0,
                    "delta_ms": 30.0,
                    "baseline_calls": 5000,
                    "recent_calls": 900,
                    "baseline_plan_id": "1111",
                    "recent_plan_id": "2222",
                    "baseline_plans": None,
                    "recent_plans": None,
                    "recent_window_hours": 24,
                    "baseline_days": 7,
                    "excess_ms": 27000.0,
                }
            ]
        },
        "high",
        {"fingerprint": "8f2kq1", "change_pct": 250.0, "recent_mean_ms": 42.0, "plan_changed": True},
    ),
    (
        "GEN-PERF-012",
        "GEN-PERF-012",
        "postgresql",
        "performance",
        {
            "capacity_forecasts": [
                {
                    "scope": "tablespace",
                    "object": "pg_default",
                    "current_bytes": 90 * GiB,
                    "capacity_bytes": 100 * GiB,
                    "capacity_basis": "volume",
                    "growth_bytes_per_day": 2 * GiB,
                    "growth_low": 1.5 * GiB,
                    "growth_high": 2.5 * GiB,
                    "days_to_full": {"likely": 5.0, "earliest": 4.0, "latest": 7.0},
                    "history_days": 30.0,
                }
            ]
        },
        "critical",
        {
            "scope": "tablespace",
            "object": "pg_default",
            "days_to_full": {"likely": 5.0, "earliest": 4.0, "latest": 7.0},
        },
    ),
    (
        "GEN-PERF-012-custom-horizon",
        "GEN-PERF-012",
        "postgresql",
        "performance",
        {
            "settings": {"performance.forecast_warning_days": 200},
            "capacity_forecasts": [
                {
                    "scope": "server",
                    "object": None,
                    "current_bytes": 10 * GiB,
                    "capacity_bytes": 100 * GiB,
                    "capacity_basis": "volume",
                    "growth_bytes_per_day": 0.6 * GiB,
                    "growth_low": 0.5 * GiB,
                    "growth_high": 0.7 * GiB,
                    "days_to_full": {"likely": 150.0, "earliest": 120.0, "latest": 180.0},
                    "history_days": 14.0,
                }
            ],
        },
        "medium",
        {"scope": "server", "capacity_bytes": 100 * GiB},
    ),
    # ---- PostgreSQL performance ---------------------------------------------------
    (
        "PG-PERF-001",
        "PG-PERF-001",
        "postgresql",
        "performance",
        {
            "tuning": {"settings": {"shared_buffers": {"value": "16384", "unit": "8kB", "bytes": 128 * MiB}}},
            "metrics": {"cache.hit_ratio": mw(97.25, 95, 99, 97)},
        },
        "low",
        {"shared_buffers": "128.0 MiB", "avg_cache_hit_ratio_pct": 97.25},
    ),
    (
        "PG-PERF-002",
        "PG-PERF-002",
        "postgresql",
        "performance",
        {
            "metrics": {
                "pg.checkpoints_requested.per_sec": mw(0.004, 0, 0.01, 0.004, 30),
                "pg.checkpoints_timed.per_sec": mw(0.002, 0, 0.004, 0.002, 30),
            },
            "tuning": {"settings": {"max_wal_size": {"value": "1024", "unit": "MB", "bytes": GiB}}},
        },
        "medium",
        {"requested_share_pct": 66.7, "max_wal_size": "1024"},
    ),
    (
        "PG-PERF-004",
        "PG-PERF-004",
        "postgresql",
        "performance",
        {
            "tuning": {
                "database": "shop",
                "stats_reset": "2026-09-01T00:00:00+00:00",
                "seq_scan_tables": [
                    {
                        "schemaname": "public",
                        "relname": "orders",
                        "seq_scan": 5000,
                        "seq_tup_read": 900_000_000,
                        "idx_scan": 120,
                        "n_live_tup": 2_000_000,
                        "total_bytes": 2 * GiB,
                    }
                ],
            }
        },
        "medium",
        {"table": "public.orders", "seq_scan": 5000, "idx_scan": 120, "size": "2.0 GiB"},
    ),
    (
        "PG-PERF-005",
        "PG-PERF-005",
        "postgresql",
        "performance",
        {
            "tuning": {
                "unused_indexes": [
                    {
                        "schemaname": "public",
                        "table_name": "orders",
                        "index_name": "ix_orders_note",
                        "idx_scan": 0,
                        "size_bytes": 300 * MiB,
                    }
                ]
            }
        },
        "low",
        {"index": "public.ix_orders_note", "table": "orders", "idx_scan": 0, "size": "300.0 MiB"},
    ),
    (
        "PG-PERF-006",
        "PG-PERF-006",
        "postgresql",
        "performance",
        {
            "tuning": {
                "duplicate_indexes": [
                    {"schema": "public", "table_name": "orders", "indexes": ["ix_a", "ix_b"], "total_bytes": 64 * MiB}
                ]
            }
        },
        "low",
        {"table": "public.orders", "indexes": ["ix_a", "ix_b"], "combined_size": "64.0 MiB"},
    ),
    (
        "PG-PERF-007",
        "PG-PERF-007",
        "postgresql",
        "performance",
        {
            "metrics": {"pg.idle_in_transaction_longest_seconds": mw(400, 0, 1800, 600)},
            "latest": {"connections.idle_in_transaction": 3.0},
        },
        "medium",
        {"longest_idle_in_txn_minutes": 30.0, "idle_in_txn_sessions_now": 3.0},
    ),
    (
        "PG-PERF-008",
        "PG-PERF-008",
        "postgresql",
        "performance",
        {"tuning": {"pg_stat_statements": False}},
        "info",
        {"pg_stat_statements": False},
    ),
    (
        "PG-PERF-009",
        "PG-PERF-009",
        "postgresql",
        "performance",
        {
            "tuning": {
                "dead_tuple_tables": [
                    {
                        "schemaname": "public",
                        "relname": "events",
                        "n_live_tup": 100_000,
                        "n_dead_tup": 0,
                        "n_mod_since_analyze": 80_000,
                        "last_analyze": None,
                        "last_autoanalyze": "2026-01-01T00:00:00+00:00",
                    }
                ]
            }
        },
        "low",
        {"table": "public.events", "n_mod_since_analyze": 80_000, "last_analyze": "2026-01-01T00:00:00+00:00"},
    ),
    # ---- MySQL / MariaDB performance ----------------------------------------------
    (
        "MYSQL-PERF-001",
        "MYSQL-PERF-001",
        "mysql",
        "performance",
        {"tuning": {"innodb_buffer_pool_size": 128 * MiB, "ram_bytes": 64 * GiB, "innodb_buffer_pool_hit_ratio": 97.5}},
        "high",
        {"innodb_buffer_pool_size": "128.0 MiB", "ram": "64.0 GiB", "hit_ratio_pct": 97.5},
    ),
    (
        "MYSQL-PERF-002",
        "MYSQL-PERF-002",
        "mariadb",
        "performance",
        {"tuning": {"tmp_disk_tables_pct": 42.0}},
        "medium",
        {"tmp_disk_tables_pct": 42.0},
    ),
    (
        "MYSQL-PERF-003",
        "MYSQL-PERF-003",
        "mysql",
        "performance",
        {"tuning": {"slow_query_log": "OFF"}},
        "info",
        {"slow_query_log": "OFF"},
    ),
    (
        "MYSQL-PERF-004",
        "MYSQL-PERF-004",
        "mysql",
        "performance",
        {"tuning": {"tables_without_pk": ["shop.audit_log"]}},
        "low",
        {"table": "shop.audit_log"},
    ),
    (
        "MYSQL-PERF-005",
        "MYSQL-PERF-005",
        "mysql",
        "performance",
        {
            "tuning": {
                "unused_indexes": [
                    {"db": "shop", "table_name": "orders", "index_name": "ix_a"},
                    {"db": "shop", "table_name": "orders", "index_name": "ix_b"},
                ],
                "unused_index_evidence_days": 45.0,
            }
        },
        "low",
        {"table": "shop.orders", "indexes": ["ix_a", "ix_b"], "evidence_days": 45.0, "source": "performance_schema"},
    ),
    (
        "MYSQL-PERF-006",
        "MYSQL-PERF-006",
        "mysql",
        "performance",
        {
            "tuning": {
                "redundant_indexes": [
                    {
                        "db": "shop",
                        "table_name": "orders",
                        "redundant_index": "ix_customer",
                        "redundant_columns": "customer_id",
                        "dominant_index": "ix_customer_date",
                        "dominant_columns": "customer_id,created_at",
                    }
                ]
            }
        },
        "low",
        {"redundant_index": "ix_customer", "dominant_index": "ix_customer_date"},
    ),
    (
        "MYSQL-PERF-007",
        "MYSQL-PERF-007",
        "mysql",
        "performance",
        {
            "tuning": {
                "full_scan_statements": [
                    {
                        "db": "shop",
                        "digest": "abc123",
                        "digest_text": "SELECT * FROM `orders` WHERE `note` = ?",
                        "calls": 1200,
                        "rows_examined": 5_000_000,
                        "rows_sent": 1200,
                    }
                ]
            }
        },
        "medium",
        {"rows_examined": 5_000_000, "rows_sent": 1200, "calls": 1200, "db": "shop"},
    ),
    (
        "MYSQL-PERF-008",
        "MYSQL-PERF-008",
        "mysql",
        "performance",
        {"tuning": {"max_used_connections": 148.0, "max_connections": 151.0}},
        "medium",
        {"max_used_connections": 148.0, "max_connections": 151.0},
    ),
    (
        "MYSQL-PERF-009",
        "MYSQL-PERF-009",
        "mariadb",
        "performance",
        {
            "tuning": {
                "query_cache_enabled": True,
                "settings": {"query_cache_type": "ON", "query_cache_size": "67108864"},
            }
        },
        "low",
        {"query_cache_type": "ON", "query_cache_size": "67108864"},
    ),
    # ---- SQL Server performance ---------------------------------------------------
    (
        "MSSQL-PERF-001",
        "MSSQL-PERF-001",
        "mssql",
        "performance",
        {"tuning": {"max_server_memory_mb": 2147483647, "physical_memory_mb": 65536}},
        "medium",
        {"max_server_memory_mb": 2147483647, "physical_memory_mb": 65536},
    ),
    (
        "MSSQL-PERF-002",
        "MSSQL-PERF-002",
        "mssql",
        "performance",
        {
            "tuning": {
                "top_waits": [{"wait_type": "PAGEIOLATCH_SH", "pct": 46.5}, {"wait_type": "CXPACKET", "pct": 20.0}],
                "page_life_expectancy": 120,
            }
        },
        "high",
        {"waits_pct": {"PAGEIOLATCH_SH": 46.5, "CXPACKET": 20.0}, "page_life_expectancy": 120},
    ),
    (
        "MSSQL-PERF-003",
        "MSSQL-PERF-003",
        "mssql",
        "performance",
        {
            "tuning": {
                "missing_indexes": [
                    {
                        "table": "[shop].[dbo].[orders]",
                        "equality_columns": "[customer_id]",
                        "inequality_columns": None,
                        "included_columns": "[total]",
                        "user_seeks": 15000,
                        "user_scans": 0,
                        "avg_user_impact": 92.4,
                        "database_name": "shop",
                    }
                ]
            }
        },
        "medium",
        {"table": "[shop].[dbo].[orders]", "avg_user_impact": 92.4, "user_seeks": 15000},
    ),
    (
        "MSSQL-PERF-004",
        "MSSQL-PERF-004",
        "mssql",
        "performance",
        {"tuning": {"tempdb_data_files": 1, "cpu_count": 16}},
        "low",
        {"tempdb_data_files": 1, "cpu_count": 16},
    ),
    (
        "MSSQL-PERF-005",
        "MSSQL-PERF-005",
        "mssql",
        "performance",
        {"tuning": {"query_store_enabled": False}},
        "info",
        {"query_store_enabled": False},
    ),
    # ---- Oracle performance -------------------------------------------------------
    (
        "ORA-PERF-001-critical",
        "ORA-PERF-001",
        "oracle",
        "performance",
        {"tuning": {"tablespaces": [{"name": "USERS", "used_pct": 97.2, "autoextend": False}]}},
        "critical",
        {"name": "USERS", "used_pct": 97.2, "autoextend": False},
    ),
    (
        "ORA-PERF-001-high",
        "ORA-PERF-001",
        "oracle",
        "performance",
        {"tuning": {"tablespaces": [{"name": "DATA", "used_pct": 88.0, "autoextend": True}]}},
        "high",
        {"name": "DATA", "used_pct": 88.0},
    ),
    (
        "ORA-PERF-002",
        "ORA-PERF-002",
        "oracle",
        "performance",
        {
            "tuning": {
                "top_waits": [{"wait_class": "User I/O", "pct": 62.0}, {"wait_class": "Concurrency", "pct": 10.0}],
                "buffer_cache_hit_ratio": 81.3,
                "diagnostics_pack_licensed": False,
            }
        },
        "medium",
        {"wait_classes_pct": {"User I/O": 62.0, "Concurrency": 10.0}, "buffer_cache_hit_ratio": 81.3},
    ),
    (
        "ORA-PERF-003",
        "ORA-PERF-003",
        "oracle",
        "performance",
        {"tuning": {"pga_over_allocation_count": 37, "pga_aggregate_target_bytes": 2 * GiB}},
        "medium",
        {"over_allocation_count": 37, "pga_aggregate_target": "2.0 GiB"},
    ),
    (
        "ORA-PERF-004",
        "ORA-PERF-004",
        "oracle",
        "performance",
        {"tuning": {"stale_stats_tables": [{"owner": "APP", "table": "ORDERS", "last_analyzed_days": 120}]}},
        "low",
        {"owner": "APP", "table": "ORDERS", "last_analyzed_days": 120},
    ),
    # ---- Db2 performance ----------------------------------------------------------
    (
        "DB2-PERF-001",
        "DB2-PERF-001",
        "db2",
        "performance",
        {
            "tuning": {
                "bufferpools": [
                    {"name": "IBMDEFAULTBP", "hit_ratio": 82.5, "logical_reads": 1_000_000, "physical_reads": 175_000}
                ]
            }
        },
        "high",
        {"name": "IBMDEFAULTBP", "hit_ratio": 82.5},
    ),
    (
        "DB2-PERF-002",
        "DB2-PERF-002",
        "db2",
        "performance",
        {"tuning": {"tables_needing_reorg": [{"table": "APP.ORDERS", "f1": 12, "f2": 55, "f3": 60}]}},
        "medium",
        {"table": "APP.ORDERS", "f1": 12, "f2": 55, "f3": 60},
    ),
    (
        "DB2-PERF-003",
        "DB2-PERF-003",
        "db2",
        "performance",
        {"tuning": {"tables_needing_runstats": [{"table": "APP.ORDERS", "stats_time_days": 90}]}},
        "low",
        {"table": "APP.ORDERS", "stats_time_days": 90},
    ),
    (
        "DB2-PERF-005",
        "DB2-PERF-005",
        "db2",
        "performance",
        {"tuning": {"lock_escalations": 12}},
        "high",
        {"lock_escalations": 12},
    ),
    (
        "ZOS-PERF-002",
        "ZOS-PERF-002",
        "db2zos",
        "performance",
        {"tuning": {"runstats_recommended": [{"object": "DB1.TS1"}], "thresholds": {"runstats_pct": 20}}},
        "low",
        {"objects": [{"object": "DB1.TS1"}], "thresholds": {"runstats_pct": 20}},
    ),
    # ---- PostgreSQL security ------------------------------------------------------
    (
        "PG-SEC-009-log-connections",
        "PG-SEC-009",
        "postgresql",
        "security",
        {"security": {"settings": {"log_connections": "off"}, "extensions": {"pgaudit": "16.0"}}},
        "low",
        {"log_connections": "off"},
    ),
    (
        "PG-SEC-009-no-pgaudit",
        "PG-SEC-009",
        "postgresql",
        "security",
        {"security": {"settings": {"log_connections": "on"}, "extensions": {"pg_stat_statements": "1.10"}}},
        "info",
        {"extensions": ["pg_stat_statements"]},
    ),
    (
        "PG-SEC-010",
        "PG-SEC-010",
        "postgresql",
        "security",
        {"security": {"direct_table_grants": [{"grantee": "alice", "grants": 42}, {"grantee": "bob", "grants": 3}]}},
        "low",
        {"users": [{"grantee": "alice", "grants": 42}]},
    ),
    (
        "PG-SEC-011",
        "PG-SEC-011",
        "postgresql",
        "security",
        {"security": {"security_definer_without_search_path": [{"name": "public.do_admin", "owner": "postgres"}]}},
        "medium",
        {"functions": [{"name": "public.do_admin", "owner": "postgres"}]},
    ),
    (
        "PG-SEC-013",
        "PG-SEC-013",
        "postgresql",
        "security",
        {
            "security": {
                "empty_password_logins": ["svc_batch"],
                "hba_rules": [
                    {"type": "host", "auth_method": "scram-sha-256"},
                    {"type": "host", "auth_method": "trust"},
                ],
            }
        },
        "high",
        {"roles": ["svc_batch"]},
    ),
    # ---- MySQL / MariaDB security -------------------------------------------------
    (
        "MYSQL-SEC-001",
        "MYSQL-SEC-001",
        "mysql",
        "security",
        {"security": {"wildcard_host_admins": ["root@%", "dba@%"]}},
        "high",
        {"accounts": ["root@%", "dba@%"]},
    ),
    (
        "MYSQL-SEC-002",
        "MYSQL-SEC-002",
        "mariadb",
        "security",
        {"security": {"anonymous_accounts": ["@localhost"]}},
        "critical",
        {"accounts": ["@localhost"]},
    ),
    (
        "MYSQL-SEC-003",
        "MYSQL-SEC-003",
        "mysql",
        "security",
        {"security": {"require_secure_transport": "OFF"}},
        "medium",
        {"require_secure_transport": "OFF"},
    ),
    (
        "MYSQL-SEC-004",
        "MYSQL-SEC-004",
        "mysql",
        "security",
        {"security": {"local_infile": "ON"}},
        "medium",
        {"local_infile": "ON"},
    ),
    (
        "MYSQL-SEC-005",
        "MYSQL-SEC-005",
        "mysql",
        "security",
        {"security": {"native_password_accounts": ["app@%"]}},
        "medium",
        {"accounts": ["app@%"]},
    ),
    (
        "MYSQL-SEC-006-unrestricted",
        "MYSQL-SEC-006",
        "mysql",
        "security",
        {"security": {"file_privilege_holders": ["etl@%"], "secure_file_priv": ""}},
        "high",
        {"accounts": ["etl@%"], "secure_file_priv": ""},
    ),
    (
        "MYSQL-SEC-006-restricted",
        "MYSQL-SEC-006",
        "mysql",
        "security",
        {"security": {"file_privilege_holders": ["etl@%"], "secure_file_priv": "/var/lib/mysql-files/"}},
        "medium",
        {"accounts": ["etl@%"], "secure_file_priv": "/var/lib/mysql-files/"},
    ),
    (
        "MYSQL-SEC-007",
        "MYSQL-SEC-007",
        "mysql",
        "security",
        {"security": {"audit_plugin_active": False, "plugins": {"validate_password": "ACTIVE", "InnoDB": "ACTIVE"}}},
        "medium",
        {"active_plugins": ["InnoDB", "validate_password"]},
    ),
    (
        "MYSQL-SEC-008-mysql57",
        "MYSQL-SEC-008",
        "mysql",
        "security",
        {"security": {"version": "5.7.44-log"}},
        "high",
        {"version": "5.7.44-log", "end_of_life": "2023-10-31"},
    ),
    (
        "MYSQL-SEC-008-mysql56",
        "MYSQL-SEC-008",
        "mysql",
        "security",
        {"security": {"version": "5.6.51"}},
        "high",
        {"version": "5.6.51", "end_of_life": "2020-02-01"},
    ),
    (
        "MYSQL-SEC-008-mariadb104",
        "MYSQL-SEC-008",
        "mariadb",
        "security",
        {"security": {"version": "10.4.32-MariaDB"}},
        "high",
        {"version": "10.4.32-MariaDB", "end_of_life": "2024-06-18"},
    ),
    (
        "MYSQL-SEC-009",
        "MYSQL-SEC-009",
        "mysql",
        "security",
        {"security": {"accounts_without_password": ["guest@%"]}},
        "critical",
        {"accounts": ["guest@%"]},
    ),
    (
        "MYSQL-SEC-010",
        "MYSQL-SEC-010",
        "mariadb",
        "security",
        {"security": {"validate_password_component": False}},
        "low",
        {"validate_password": False},
    ),
    # ---- SQL Server security ------------------------------------------------------
    (
        "MSSQL-SEC-003",
        "MSSQL-SEC-003",
        "mssql",
        "security",
        {"security": {"sysadmin_members": ["sa", "CORP\\dba", "app_owner", "etl"]}},
        "high",
        {"members": ["sa", "CORP\\dba", "app_owner", "etl"]},
    ),
    (
        "MSSQL-SEC-004-trustworthy",
        "MSSQL-SEC-004",
        "mssql",
        "security",
        {"security": {"trustworthy_dbs": ["shop"]}},
        "high",
        {"database": "shop"},
    ),
    (
        "MSSQL-SEC-004-guest",
        "MSSQL-SEC-004",
        "mssql",
        "security",
        {"security": {"guest_enabled_dbs": ["reports"]}},
        "medium",
        {"database": "reports"},
    ),
    # ---- Oracle security ----------------------------------------------------------
    (
        "ORA-SEC-001",
        "ORA-SEC-001",
        "oracle",
        "security",
        {"security": {"default_accounts_open": ["SCOTT", "DBSNMP"]}},
        "high",
        {"accounts": ["SCOTT", "DBSNMP"]},
    ),
    (
        "ORA-SEC-002-many",
        "ORA-SEC-002",
        "oracle",
        "security",
        {"security": {"public_execute_packages": ["UTL_FILE", "UTL_HTTP"]}},
        "high",
        {"packages": ["UTL_FILE", "UTL_HTTP"]},
    ),
    (
        "ORA-SEC-002-one",
        "ORA-SEC-002",
        "oracle",
        "security",
        {"security": {"public_execute_packages": ["UTL_TCP"]}},
        "medium",
        {"packages": ["UTL_TCP"]},
    ),
    (
        "ORA-SEC-004",
        "ORA-SEC-004",
        "oracle",
        "security",
        {"security": {"unlimited_password_profiles": ["DEFAULT", "APP_PROFILE"]}},
        "low",
        {"profiles": ["DEFAULT", "APP_PROFILE"]},
    ),
    (
        "ORA-SEC-006",
        "ORA-SEC-006",
        "oracle",
        "security",
        {"security": {"dba_role_grantees": ["APP_OWNER"]}},
        "high",
        {"grantees": ["APP_OWNER"]},
    ),
    (
        "ORA-SEC-007-medium",
        "ORA-SEC-007",
        "oracle",
        "security",
        {"security": {"any_privilege_grantees": {"REPORTER": ["SELECT ANY TABLE"]}}},
        "medium",
        {"grantees": {"REPORTER": ["SELECT ANY TABLE"]}},
    ),
    (
        "ORA-SEC-007-high",
        "ORA-SEC-007",
        "oracle",
        "security",
        {"security": {"any_privilege_grantees": {"APP": ["SELECT ANY TABLE", "GRANT ANY ROLE"]}}},
        "high",
        {"grantees": {"APP": ["SELECT ANY TABLE", "GRANT ANY ROLE"]}},
    ),
    (
        "ORA-SEC-008",
        "ORA-SEC-008",
        "oracle",
        "security",
        {"security": {"weak_password_versions": [{"username": "LEGACY", "password_versions": "10G"}]}},
        "high",
        {"accounts": [{"username": "LEGACY", "password_versions": "10G"}]},
    ),
    (
        "ORA-SEC-009",
        "ORA-SEC-009",
        "oracle",
        "security",
        {"security": {"end_of_support": "2022-07-31", "version": "12.1.0.2.0"}},
        "high",
        {"version": "12.1.0.2.0", "end_of_support": "2022-07-31"},
    ),
    # ---- Db2 security -------------------------------------------------------------
    (
        "DB2-SEC-002",
        "DB2-SEC-002",
        "db2",
        "security",
        {"security": {"dbadm_holders": ["DB2INST1", "ETLUSER"]}},
        "high",
        {"holders": ["ETLUSER"]},
    ),
]


@pytest.mark.parametrize(
    ("rule_id", "engine", "kind", "kw", "severity", "evidence"),
    [pytest.param(*case[1:], id=case[0]) for case in FIRE],
)
def test_rule_fires_on_realistic_evidence(
    rule_id: str, engine: str, kind: str, kw: dict[str, Any], severity: str, evidence: dict[str, Any]
) -> None:
    c = ctx(engine, **kw)
    findings = run(rule_id, c)
    assert findings, f"{rule_id} did not fire"
    matching = [sev for sev, ev in findings if _contains(ev, evidence)]
    assert matching, f"no {rule_id} finding carries {evidence}: {findings}"
    assert severity in matching, f"{rule_id}: expected severity {severity}, got {matching}"
    # end-to-end: registered under the right kind and scoped to this engine
    assert rule_id in {f.rule.rule_id for f in evaluate(c, kind)}  # type: ignore[arg-type]


# (case id, rule id, engine, context kwargs) - healthy or borderline evidence
NO_FIRE: list[tuple[str, str, str, dict[str, Any]]] = [
    ("GEN-PERF-003", "GEN-PERF-003", "postgresql", {"metrics": {"sessions.blocked": mw(0.2, 0, 1, 0)}}),
    ("GEN-PERF-004", "GEN-PERF-004", "mysql", {"metrics": {"deadlocks.count": mw(0, 0, 0, 0)}}),
    ("GEN-PERF-005", "GEN-PERF-005", "postgresql", {"metrics": {"replication.lag_seconds": mw(5, 0, 45, 2)}}),
    ("GEN-PERF-006", "GEN-PERF-006", "oracle", {"metrics": {"transactions.longest_seconds": mw(60, 0, 1800, 30)}}),
    ("GEN-PERF-008", "GEN-PERF-008", "postgresql", {"metrics": {"temp.bytes_per_sec": mw(MiB, 0, 50 * MiB, 0)}}),
    ("GEN-PERF-009", "GEN-PERF-009", "postgresql", {"metrics": {"cpu.percent": mw(40, 10, 99, 35)}}),
    ("GEN-PERF-009-few-samples", "GEN-PERF-009", "postgresql", {"metrics": {"cpu.percent": mw(95, 90, 99, 95, 3)}}),
    # a past peak alone must not fire when the latest reading is below the threshold
    (
        "GEN-PERF-010-peak-only",
        "GEN-PERF-010",
        "postgresql",
        {"metrics": {"storage.utilization": mw(75, 70, 97, 80, 30)}},
    ),
    (
        "GEN-PERF-010-few-samples",
        "GEN-PERF-010",
        "postgresql",
        {"metrics": {"storage.utilization": mw(96, 96, 96, 96, 2)}},
    ),
    (
        "GEN-PERF-011",
        "GEN-PERF-011",
        "oracle",
        {"query_regressions": [{"fingerprint": "x", "regressed": False, "plan_changed": True}]},
    ),
    (
        "GEN-PERF-012",
        "GEN-PERF-012",
        "postgresql",
        {
            "capacity_forecasts": [
                {"scope": "server", "days_to_full": {"earliest": 120.0, "likely": 150.0}},
                {"scope": "server", "days_to_full": None},
            ]
        },
    ),
    (
        "PG-PERF-001",
        "PG-PERF-001",
        "postgresql",
        {"tuning": {"settings": {"shared_buffers": {"value": "524288", "unit": "8kB", "bytes": 4 * GiB}}}},
    ),
    (
        "PG-PERF-002",
        "PG-PERF-002",
        "postgresql",
        {
            "metrics": {
                "pg.checkpoints_requested.per_sec": mw(0.0005, 0, 0.001, 0, 30),
                "pg.checkpoints_timed.per_sec": mw(0.003, 0, 0.004, 0.003, 30),
            }
        },
    ),
    (
        "PG-PERF-004",
        "PG-PERF-004",
        "postgresql",
        {
            "tuning": {
                "seq_scan_tables": [
                    {
                        "schemaname": "public",
                        "relname": "orders",
                        "seq_scan": 5000,
                        "seq_tup_read": 9_000_000,
                        "idx_scan": 900_000,
                        "total_bytes": 2 * GiB,
                    },
                    {
                        "schemaname": "public",
                        "relname": "tiny",
                        "seq_scan": 99_999,
                        "seq_tup_read": 10,
                        "idx_scan": 0,
                        "total_bytes": 8 * MiB,
                    },
                ]
            }
        },
    ),
    (
        "PG-PERF-005",
        "PG-PERF-005",
        "postgresql",
        {
            "tuning": {
                "unused_indexes": [
                    {"schemaname": "public", "table_name": "t", "index_name": "ix", "size_bytes": 8 * MiB}
                ]
            }
        },
    ),
    ("PG-PERF-006", "PG-PERF-006", "postgresql", {"tuning": {"duplicate_indexes": []}}),
    (
        "PG-PERF-007",
        "PG-PERF-007",
        "postgresql",
        {"metrics": {"pg.idle_in_transaction_longest_seconds": mw(5, 0, 60, 0)}},
    ),
    ("PG-PERF-008", "PG-PERF-008", "postgresql", {"tuning": {"pg_stat_statements": True}}),
    (
        "PG-PERF-009",
        "PG-PERF-009",
        "postgresql",
        {
            "tuning": {
                "dead_tuple_tables": [
                    {"schemaname": "public", "relname": "big", "n_live_tup": 10_000_000, "n_mod_since_analyze": 60_000},
                    {"schemaname": "public", "relname": "small", "n_live_tup": 1_000, "n_mod_since_analyze": 20_000},
                ]
            }
        },
    ),
    (
        "MYSQL-PERF-001",
        "MYSQL-PERF-001",
        "mysql",
        {"tuning": {"innodb_buffer_pool_size": 40 * GiB, "ram_bytes": 64 * GiB}},
    ),
    ("MYSQL-PERF-001-ram-unknown", "MYSQL-PERF-001", "mysql", {"tuning": {"innodb_buffer_pool_size": 128 * MiB}}),
    ("MYSQL-PERF-002", "MYSQL-PERF-002", "mysql", {"tuning": {"tmp_disk_tables_pct": 3.0}}),
    ("MYSQL-PERF-003", "MYSQL-PERF-003", "mysql", {"tuning": {"slow_query_log": "ON"}}),
    ("MYSQL-PERF-004", "MYSQL-PERF-004", "mysql", {"tuning": {"tables_without_pk": []}}),
    ("MYSQL-PERF-005", "MYSQL-PERF-005", "mysql", {"tuning": {"unused_indexes": [], "unused_index_evidence_days": 45}}),
    ("MYSQL-PERF-006", "MYSQL-PERF-006", "mysql", {"tuning": {"redundant_indexes": []}}),
    (
        "MYSQL-PERF-007",
        "MYSQL-PERF-007",
        "mysql",
        {
            "tuning": {
                "full_scan_statements": [
                    {"db": "shop", "digest_text": "SELECT 1", "calls": 10, "rows_examined": 50_000, "rows_sent": 10},
                    {
                        "db": "shop",
                        "digest_text": "SELECT 2",
                        "calls": 10,
                        "rows_examined": 500_000,
                        "rows_sent": 50_000,
                    },
                ]
            }
        },
    ),
    ("MYSQL-PERF-008", "MYSQL-PERF-008", "mysql", {"tuning": {"max_used_connections": 40, "max_connections": 151}}),
    ("MYSQL-PERF-009", "MYSQL-PERF-009", "mysql", {"tuning": {"query_cache_enabled": False}}),
    ("MSSQL-PERF-001", "MSSQL-PERF-001", "mssql", {"tuning": {"max_server_memory_mb": 57344}}),
    (
        "MSSQL-PERF-002",
        "MSSQL-PERF-002",
        "mssql",
        {"tuning": {"top_waits": [{"wait_type": "PAGEIOLATCH_SH", "pct": 10.0}, {"wait_type": "CXPACKET", "pct": 60}]}},
    ),
    (
        "MSSQL-PERF-003",
        "MSSQL-PERF-003",
        "mssql",
        {"tuning": {"missing_indexes": [{"table": "[db].[dbo].[t]", "avg_user_impact": 35.0, "user_seeks": 10}]}},
    ),
    ("MSSQL-PERF-004", "MSSQL-PERF-004", "mssql", {"tuning": {"tempdb_data_files": 8, "cpu_count": 16}}),
    ("MSSQL-PERF-004-small", "MSSQL-PERF-004", "mssql", {"tuning": {"tempdb_data_files": 4, "cpu_count": 4}}),
    ("MSSQL-PERF-005", "MSSQL-PERF-005", "mssql", {"tuning": {"query_store_enabled": True}}),
    ("MSSQL-PERF-005-unknown", "MSSQL-PERF-005", "mssql", {"tuning": {"query_store_enabled": None}}),
    ("ORA-PERF-001", "ORA-PERF-001", "oracle", {"tuning": {"tablespaces": [{"name": "USERS", "used_pct": 60.0}]}}),
    (
        "ORA-PERF-002",
        "ORA-PERF-002",
        "oracle",
        {"tuning": {"top_waits": [{"wait_class": "User I/O", "pct": 15.0}, {"wait_class": "Commit", "pct": 70.0}]}},
    ),
    ("ORA-PERF-003", "ORA-PERF-003", "oracle", {"tuning": {"pga_over_allocation_count": 0}}),
    (
        "ORA-PERF-004",
        "ORA-PERF-004",
        "oracle",
        {"tuning": {"stale_stats_tables": [{"owner": "APP", "table": "T", "last_analyzed_days": 10}]}},
    ),
    ("DB2-PERF-001", "DB2-PERF-001", "db2", {"tuning": {"bufferpools": [{"name": "BP1", "hit_ratio": 99.1}]}}),
    ("DB2-PERF-002", "DB2-PERF-002", "db2", {"tuning": {"tables_needing_reorg": []}}),
    (
        "DB2-PERF-003",
        "DB2-PERF-003",
        "db2",
        {"tuning": {"tables_needing_runstats": [{"table": "APP.T", "stats_time_days": 5}]}},
    ),
    ("DB2-PERF-005", "DB2-PERF-005", "db2", {"tuning": {"lock_escalations": 0}}),
    ("ZOS-PERF-002", "ZOS-PERF-002", "db2zos", {"tuning": {"runstats_recommended": []}}),
    (
        "PG-SEC-009",
        "PG-SEC-009",
        "postgresql",
        {"security": {"settings": {"log_connections": "on"}, "extensions": {"pgaudit": "16.0"}}},
    ),
    (
        "PG-SEC-009-non-production",
        "PG-SEC-009",
        "postgresql",
        {"environment_tier": "development", "security": {"settings": {"log_connections": "on"}, "extensions": {}}},
    ),
    ("PG-SEC-010", "PG-SEC-010", "postgresql", {"security": {"direct_table_grants": [{"grantee": "a", "grants": 3}]}}),
    ("PG-SEC-011", "PG-SEC-011", "postgresql", {"security": {"security_definer_without_search_path": []}}),
    (
        "PG-SEC-013",
        "PG-SEC-013",
        "postgresql",
        {
            "security": {
                "empty_password_logins": ["svc_batch"],
                "hba_rules": [{"type": "host", "auth_method": "scram-sha-256"}],
            }
        },
    ),
    (
        "PG-SEC-013-hba-unknown",
        "PG-SEC-013",
        "postgresql",
        {"security": {"empty_password_logins": ["svc_batch"], "hba_rules": None}},
    ),
    ("MYSQL-SEC-001", "MYSQL-SEC-001", "mysql", {"security": {"wildcard_host_admins": []}}),
    ("MYSQL-SEC-002", "MYSQL-SEC-002", "mysql", {"security": {"anonymous_accounts": []}}),
    ("MYSQL-SEC-003", "MYSQL-SEC-003", "mysql", {"security": {"require_secure_transport": "ON"}}),
    ("MYSQL-SEC-004", "MYSQL-SEC-004", "mysql", {"security": {"local_infile": "OFF"}}),
    ("MYSQL-SEC-005", "MYSQL-SEC-005", "mysql", {"security": {"native_password_accounts": []}}),
    ("MYSQL-SEC-006", "MYSQL-SEC-006", "mysql", {"security": {"file_privilege_holders": [], "secure_file_priv": ""}}),
    ("MYSQL-SEC-007", "MYSQL-SEC-007", "mysql", {"security": {"audit_plugin_active": True}}),
    (
        "MYSQL-SEC-007-non-production",
        "MYSQL-SEC-007",
        "mysql",
        {"environment_tier": "development", "security": {"audit_plugin_active": False}},
    ),
    ("MYSQL-SEC-008-mysql84", "MYSQL-SEC-008", "mysql", {"security": {"version": "8.4.2"}}),
    ("MYSQL-SEC-008-mariadb1011", "MYSQL-SEC-008", "mariadb", {"security": {"version": "10.11.8-MariaDB"}}),
    ("MYSQL-SEC-008-unparsable", "MYSQL-SEC-008", "mysql", {"security": {"version": "unknown"}}),
    ("MYSQL-SEC-009", "MYSQL-SEC-009", "mysql", {"security": {"accounts_without_password": []}}),
    ("MYSQL-SEC-010", "MYSQL-SEC-010", "mysql", {"security": {"validate_password_component": True}}),
    ("MYSQL-SEC-010-unknown", "MYSQL-SEC-010", "mysql", {"security": {"validate_password_component": None}}),
    ("MSSQL-SEC-003", "MSSQL-SEC-003", "mssql", {"security": {"sysadmin_members": ["sa", "CORP\\dba"]}}),
    ("MSSQL-SEC-004", "MSSQL-SEC-004", "mssql", {"security": {"trustworthy_dbs": [], "guest_enabled_dbs": []}}),
    ("ORA-SEC-001", "ORA-SEC-001", "oracle", {"security": {"default_accounts_open": []}}),
    ("ORA-SEC-002", "ORA-SEC-002", "oracle", {"security": {"public_execute_packages": []}}),
    ("ORA-SEC-004", "ORA-SEC-004", "oracle", {"security": {"unlimited_password_profiles": []}}),
    ("ORA-SEC-006", "ORA-SEC-006", "oracle", {"security": {"dba_role_grantees": []}}),
    ("ORA-SEC-006-bad-type", "ORA-SEC-006", "oracle", {"security": {"dba_role_grantees": "APP_OWNER"}}),
    ("ORA-SEC-007", "ORA-SEC-007", "oracle", {"security": {"any_privilege_grantees": {}}}),
    ("ORA-SEC-008", "ORA-SEC-008", "oracle", {"security": {"weak_password_versions": []}}),
    ("ORA-SEC-009", "ORA-SEC-009", "oracle", {"security": {"end_of_support": None, "version": "19.24.0.0.0"}}),
    ("DB2-SEC-002", "DB2-SEC-002", "db2", {"security": {"dbadm_holders": ["DB2INST1"]}}),
]


@pytest.mark.parametrize(
    ("rule_id", "engine", "kw"),
    [pytest.param(*case[1:], id=case[0]) for case in NO_FIRE],
)
def test_rule_quiet_on_healthy_evidence(rule_id: str, engine: str, kw: dict[str, Any]) -> None:
    assert run(rule_id, ctx(engine, **kw)) == []


#: rules that report the *absence* of a control, so they fire when the evidence is empty
#: and the server is production (the collectors always populate their inputs)
FIRES_ON_ABSENCE = {"PG-SEC-009", "MYSQL-SEC-007"}


def _empty_cases() -> list[Any]:
    out = []
    for r in sorted(RULES, key=lambda r: r.rule_id):
        for eng in r.engines:
            engine = "postgresql" if eng == "*" else eng
            out.append(pytest.param(r.rule_id, engine, id=f"{r.rule_id}-{engine}"))
    return out


@pytest.mark.parametrize(("rule_id", "engine"), _empty_cases())
def test_rule_quiet_and_safe_without_evidence(rule_id: str, engine: str) -> None:
    """With no metrics, tuning or security evidence a rule neither raises nor fires."""
    tier = "development" if rule_id in FIRES_ON_ABSENCE else "production"
    assert run(rule_id, ctx(engine, environment_tier=tier)) == []


def test_db2_sec_004_unknown_dbm_cfg_does_not_fire() -> None:
    sec = {"authentication": None, "ssl_svcename": None, "dbm_cfg_readable": False}
    assert run("DB2-SEC-004", ctx("db2", security=sec)) == []


def test_db2_sec_004_still_fires_on_known_plain_authentication() -> None:
    sec = {"authentication": "SERVER", "ssl_svcename": None, "dbm_cfg_readable": True}
    [(sev, ev)] = run("DB2-SEC-004", ctx("db2", security=sec))
    assert sev == "medium" and ev["authentication"] == "SERVER"


@pytest.mark.parametrize("version", ["10.3.39-MariaDB", "10.1.48-MariaDB", "10.8.8-MariaDB", "11.2.5-MariaDB"])
def test_mysql_sec_008_flags_old_and_short_term_mariadb(version: str) -> None:
    findings = run("MYSQL-SEC-008", ctx("mariadb", security={"version": version}))
    assert findings and findings[0][0] == "high"


# ---------------------------------------------------------------------------------------
# meta: every registered rule is named by at least one unit test
# ---------------------------------------------------------------------------------------
def test_every_rule_is_named_in_a_unit_test() -> None:
    text = "\n".join(p.read_text(encoding="utf-8") for p in sorted(Path(__file__).parent.glob("test_*.py")))
    missing = [r.rule_id for r in RULES if not re.search(rf"(?<![\w-]){re.escape(r.rule_id)}(?!\d)", text)]
    assert not missing, f"rules without a unit test: {sorted(missing)}"


def _regression(**kw: Any) -> dict[str, Any]:
    base = {
        "database": "app",
        "fingerprint": "f1",
        "regressed": True,
        "plan_changed": False,
        "baseline_mean_ms": 0.0,
        "recent_mean_ms": 300.0,
        "change_pct": None,
        "delta_ms": 300.0,
        "baseline_calls": 500,
        "recent_calls": 200,
        "recent_window_hours": 1,
        "baseline_days": 7,
        "excess_ms": 60000.0,
    }
    return {**base, **kw}


def test_gen_perf_011_unknown_percentage_is_judged_on_the_absolute_increase() -> None:
    # baseline mean 0 ms: no percentage; +300 ms per execution is a large regression
    [(sev, ev)] = run("GEN-PERF-011", ctx("postgresql", query_regressions=[_regression()]))
    assert sev == "high" and ev["change_pct"] is None
    [(sev, _)] = run(
        "GEN-PERF-011", ctx("postgresql", query_regressions=[_regression(recent_mean_ms=20.0, delta_ms=20.0)])
    )
    assert sev == "medium"


def test_gen_perf_012_never_says_negative_days() -> None:
    f = {
        "scope": "server",
        "object": None,
        "current_bytes": 101 * GiB,
        "capacity_bytes": 100 * GiB,
        "capacity_basis": "volume",
        "growth_bytes_per_day": 1 * GiB,
        "growth_low": 0.5 * GiB,
        "growth_high": 1.5 * GiB,
        "days_to_full": {"likely": -1.0, "earliest": -2.0, "latest": 0.5},
        "history_days": 30.0,
    }
    findings = RULES_BY_ID["GEN-PERF-012"].evaluate(ctx("postgresql", capacity_forecasts=[f]))
    assert findings and "-" not in findings[0].finding.split("reaches")[1].split("(")[0]
    assert "in -" not in findings[0].finding and "now at the earliest" in findings[0].finding
