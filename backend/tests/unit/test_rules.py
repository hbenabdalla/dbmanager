from datetime import UTC, datetime, timedelta

from app.advisors.engine import EvidenceContext, MetricWindow, catalog, evaluate


def ctx(engine: str = "postgresql", **kw) -> EvidenceContext:  # type: ignore[no-untyped-def]
    base = dict(
        server_id="s1",
        server_name="srv",
        engine=engine,
        environment_tier="production",
        tls_mode="verify-full",
        is_demo=False,
        window_hours=6,
    )
    base.update(kw)
    return EvidenceContext(**base)  # type: ignore[arg-type]


def ids(findings) -> set[str]:  # type: ignore[no-untyped-def]
    return {f.rule.rule_id for f in findings}


def test_rule_ids_unique_and_well_formed() -> None:
    cat = catalog()
    keys = [r["rule_id"] for r in cat]
    assert len(keys) == len(set(keys))
    for r in cat:
        assert r["severity"] in {"critical", "high", "medium", "low", "info"}


def test_healthy_server_produces_no_performance_findings() -> None:
    c = ctx(
        metrics={
            "connections.utilization": MetricWindow(20, 10, 30, 20, 50),
            "cache.hit_ratio": MetricWindow(99.5, 99, 99.9, 99.5, 50),
        }
    )
    assert evaluate(c, "performance") == []


def test_connection_and_cache_rules_fire_with_evidence() -> None:
    c = ctx(
        metrics={
            "connections.utilization": MetricWindow(85, 70, 97, 90, 50),
            "cache.hit_ratio": MetricWindow(88, 80, 93, 88, 50),
            "io.blocks_read_per_sec": MetricWindow(5000, 100, 9000, 5000, 50),
        }
    )
    found = evaluate(c, "performance")
    assert {"GEN-PERF-001", "GEN-PERF-002"} <= ids(found)
    conn = next(f for f in found if f.rule.rule_id == "GEN-PERF-001")
    # a peak without a sustained period is only a medium-severity observation
    assert conn.severity == "medium" and conn.result.evidence["peak_utilization_pct"] == 97


def test_pg_security_rules() -> None:
    sec = {
        "settings": {"ssl": "off", "password_encryption": "md5", "log_connections": "off"},
        "roles": [
            {"name": "postgres", "is_superuser": True, "can_login": True},
            {"name": "alice", "is_superuser": True, "can_login": True},
            {"name": "etl", "can_login": True, "attributes": {"createrole": True}},
        ],
        "hba_rules": [{"type": "host", "auth_method": "trust"}],
        "md5_password_roles": ["bob"],
        "public_schema_create": True,
        "version_num": 120010,
        "version": "12.10",
        "current_user_superuser": True,
        "non_ssl_connections": 3,
        "remote_connections": 10,
    }
    found = ids(evaluate(ctx(security=sec, tls_mode="prefer"), "security"))
    assert {
        "PG-SEC-001",
        "PG-SEC-002",
        "PG-SEC-003",
        "PG-SEC-004",
        "PG-SEC-005",
        "PG-SEC-006",
        "PG-SEC-007",
        "PG-SEC-008",
        "PG-SEC-012",
        "GEN-SEC-001",
    } <= found


def test_dormant_admin_is_high() -> None:
    old = (datetime.now(UTC) - timedelta(days=200)).isoformat()
    sec = {"roles": [{"name": "SCOTT", "can_login": True, "last_login_at": old, "admin_reasons": ["DBA role"]}]}
    f = [x for x in evaluate(ctx("oracle", security=sec), "security") if x.rule.rule_id == "GEN-SEC-002"]
    assert f and f[0].severity == "high"


def test_rules_are_engine_scoped() -> None:
    sec = {"xp_cmdshell": True, "sa_enabled": True}
    assert "MSSQL-SEC-002" in ids(evaluate(ctx("mssql", security=sec), "security"))
    assert "MSSQL-SEC-002" not in ids(evaluate(ctx("postgresql", security=sec), "security"))


def test_broken_rule_does_not_stop_engine() -> None:
    # malformed evidence must not raise
    c = ctx(tuning={"dead_tuple_tables": [{"bogus": 1}]})
    evaluate(c, "performance")


def test_connection_rule_requires_sustained_high_utilization() -> None:
    m = {"connections.utilization": MetricWindow(88, 86, 92, 90, 60)}
    # high average but never sustained (and no 95% peak): no finding
    assert "GEN-PERF-001" not in ids(evaluate(ctx(metrics=m), "performance"))
    sustained = {
        "connections.utilization": {
            "sustained": True,
            "threshold": 85.0,
            "minutes": 15,
            "bound": 86.0,
            "samples": 15,
        }
    }
    found = evaluate(ctx(metrics=m, sustained=sustained), "performance")
    conn = next(f for f in found if f.rule.rule_id == "GEN-PERF-001")
    assert conn.severity == "high" and conn.result.evidence["sustained_minutes"] == 15
    assert "15 minutes" in conn.result.finding
    sustained["connections.utilization"]["bound"] = 96.0
    found = evaluate(ctx(metrics=m, sustained=sustained), "performance")
    assert next(f for f in found if f.rule.rule_id == "GEN-PERF-001").severity == "critical"


def test_mssql_rules_fire_on_evidence() -> None:
    sec = {
        "sa_enabled": True,
        "xp_cmdshell": False,
        "ole_automation": True,
        "clr_enabled": True,
        "clr_strict_security": 0,
        "end_of_support": "2024-07-09",
        "product_name": "SQL Server 2014",
        "version": "12.0.6024.0",
        "cloud_provider": None,
        "orphaned_users": {"shop": ["old_app"]},
        "logins_without_policy": ["legacy", "##MS_PolicyTsqlExecutionLogin##"],
        "force_encryption": False,
        "server_audits": None,
    }
    tun = {
        "auto_shrink_dbs": ["shop"],
        "auto_close_dbs": [],
        "unused_indexes": [{"index_name": "ix_old", "user_updates": 5000}],
        "uptime_seconds": 30 * 86400,
        "database": "shop",
        "stale_statistics": [{"stats_name": "st1", "modification_counter": 50000}],
        "log_backup_waits": ["shop"],
        "maxdop": 0,
        "cost_threshold_for_parallelism": 5,
        "cpu_count": 16,
    }
    c = ctx("mssql", security=sec, tuning=tun)
    s = ids(evaluate(c, "security"))
    assert {"MSSQL-SEC-001", "MSSQL-SEC-006", "MSSQL-SEC-007", "MSSQL-SEC-008", "MSSQL-SEC-009", "MSSQL-SEC-010"} <= s
    assert "MSSQL-SEC-005" not in s  # audit visibility unknown -> no finding
    p = ids(evaluate(c, "performance"))
    assert {"MSSQL-PERF-006", "MSSQL-PERF-007", "MSSQL-PERF-008", "MSSQL-PERF-009", "MSSQL-PERF-010"} <= p
    azure = ctx("mssql", security={**sec, "cloud_provider": "azure-sql"})
    assert not {"MSSQL-SEC-007", "MSSQL-SEC-010"} & ids(evaluate(azure, "security"))


def test_oracle_rules_fire_on_evidence() -> None:
    sec = {
        "default_accounts_open": ["DBSNMP", "SCOTT"],
        "network_encryption": "NOT_REQUIRED",
        "monitoring_session_protocol": "tcp",
        "dba_role_grantees": ["APP_OWNER"],
        "any_privilege_grantees": {"APP_OWNER": ["SELECT ANY TABLE", "GRANT ANY ROLE"]},
        "weak_password_versions": [{"username": "LEGACY", "password_versions": "10G"}],
        "end_of_support": "2022-07-31",
        "version": "12.1.0.2.0",
        "public_system_privileges": ["CREATE SESSION"],
        "unlimited_failed_login_profiles": ["APP_PROFILE"],
        "audit_trail": "DB",
    }
    tun = {
        "hard_parse_ratio": 45.0,
        "hard_parses": 2_000_000,
        "log_switches_24h": 300,
        "redo_log_bytes_min": 50 * 2**20,
        "fra_used_percent": 96.0,
        "invalid_objects": [{"owner": "APP", "object_type": "PACKAGE BODY", "n": 3}],
        "unusable_indexes": [{"owner": "APP", "index_name": "IX1", "table_name": "T"}],
        "log_mode": "NOARCHIVELOG",
    }
    c = ctx("oracle", security=sec, tuning=tun)
    s = ids(evaluate(c, "security"))
    assert {f"ORA-SEC-00{i}" for i in (1, 5, 6, 7, 8, 9)} | {"ORA-SEC-010", "ORA-SEC-011"} <= s
    assert "ORA-SEC-003" not in s
    p = ids(evaluate(c, "performance"))
    assert {"ORA-PERF-005", "ORA-PERF-006", "ORA-PERF-007", "ORA-PERF-008", "ORA-PERF-009"} <= p
    tls = ctx("oracle", security={"network_encryption": "TCPS"})
    assert "ORA-SEC-005" not in ids(evaluate(tls, "security"))


def test_db2_and_zos_rules_fire_on_evidence() -> None:
    sec = {
        "dataaccess_holders": ["ETLUSER"],
        "accessctrl_holders": [],
        "secadm_holders": ["SECUSR"],
        "not_fenced_holders": [],
        "end_of_support": "2020-04-30",
        "version": "10.5.0.11",
        "public_table_select_grants": 4,
        "public_grants": ["CREATETAB", "CONNECT"],
        "audit_active": None,
        "authentication": "SERVER",
        "ssl_svcename": "50001",
    }
    tun = {
        "log_used_percent": 92.0,
        "reorg_pending_tables": ["APP.T1"],
        "package_cache_hit_ratio": 55.0,
        "deadlocks": 30,
        "lock_timeouts": 0,
        "sort_overflow_pct": 0.0,
        "package_cache_overflows": None,
        "lock_escalations": 0,
    }
    c = ctx("db2", security=sec, tuning=tun)
    s = ids(evaluate(c, "security"))
    assert {"DB2-SEC-001", "DB2-SEC-005", "DB2-SEC-006", "DB2-SEC-007"} <= s
    assert not {"DB2-SEC-003", "DB2-SEC-004"} & s  # audit unknown / TLS configured
    p = ids(evaluate(c, "performance"))
    assert {"DB2-PERF-006", "DB2-PERF-007", "DB2-PERF-008", "DB2-PERF-009"} <= p
    assert "DB2-PERF-004" not in p  # unknown package-cache overflows must not fire
    z = ctx(
        "db2zos",
        security={"sysadm_holders": ["A", "B", "C"], "sysctrl_holders": ["D"], "public_table_grants": 2},
        tuning={
            "reorg_recommended": [{"object": "DB.TS"}],
            "runstats_recommended": [],
            "copy_recommended": [{"object": "DB.TS"}],
        },
    )
    assert {"ZOS-SEC-001", "ZOS-SEC-002"} <= ids(evaluate(z, "security"))
    zp = ids(evaluate(z, "performance"))
    assert {"ZOS-PERF-001", "ZOS-PERF-003"} <= zp and "ZOS-PERF-002" not in zp
    assert not any(r.startswith("DB2-") for r in ids(evaluate(z, "security")) | zp)
