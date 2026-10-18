import pytest

from app.domain.risk import RiskClass
from app.domain.sql_classifier import classify_sql


@pytest.mark.parametrize(
    ("sql", "risk", "stype"),
    [
        ("SELECT * FROM orders WHERE id = 1", RiskClass.READ, "SELECT"),
        ("-- comment\nselect 1", RiskClass.READ, "SELECT"),
        ("WITH x AS (SELECT 1) SELECT * FROM x", RiskClass.READ, "SELECT"),
        ("SHOW work_mem", RiskClass.READ, "SHOW"),
        ("EXPLAIN SELECT * FROM t", RiskClass.READ, "EXPLAIN"),
        ("INSERT INTO t VALUES (1)", RiskClass.LOW_RISK_CHANGE, "DML"),
        ("UPDATE t SET a = 1 WHERE id = 2", RiskClass.LOW_RISK_CHANGE, "DML"),
        ("UPDATE t SET a = 1", RiskClass.DESTRUCTIVE, "DML"),
        ("DELETE FROM t", RiskClass.DESTRUCTIVE, "DML"),
        ("DELETE FROM t WHERE id = 3", RiskClass.LOW_RISK_CHANGE, "DML"),
        ("TRUNCATE t", RiskClass.DESTRUCTIVE, "DDL"),
        ("DROP TABLE t", RiskClass.DESTRUCTIVE, "DDL"),
        ("DROP DATABASE prod", RiskClass.DESTRUCTIVE, "DDL"),
        ("CREATE TABLE t (a int)", RiskClass.PRIVILEGED_CHANGE, "DDL"),
        ("CREATE OR REPLACE VIEW v AS SELECT 1", RiskClass.DESTRUCTIVE, "DDL"),
        ("ALTER TABLE t ADD COLUMN b int", RiskClass.PRIVILEGED_CHANGE, "DDL"),
        ("ALTER TABLE t DROP COLUMN b", RiskClass.DESTRUCTIVE, "DDL"),
        ("GRANT SELECT ON t TO u", RiskClass.PRIVILEGED_CHANGE, "DCL"),
        ("REVOKE SELECT ON t FROM u", RiskClass.DESTRUCTIVE, "DCL"),
        ("CREATE ROLE bob LOGIN PASSWORD 'x'", RiskClass.PRIVILEGED_CHANGE, "ROLE_DDL"),
        ("ALTER SYSTEM SET work_mem = '64MB'", RiskClass.DESTRUCTIVE, "SYSTEM_CONFIG"),
        ("VACUUM t", RiskClass.LOW_RISK_CHANGE, "MAINTENANCE"),
        ("SELECT pg_terminate_backend(123)", RiskClass.PRIVILEGED_CHANGE, "SELECT"),
        ("SELECT * FROM t FOR UPDATE", RiskClass.LOW_RISK_CHANGE, "SELECT"),
        ("WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d", RiskClass.DESTRUCTIVE, "DML"),
        ("COPY t TO PROGRAM 'rm -rf /'", RiskClass.DESTRUCTIVE, "COPY"),
        ("SELECT * INTO new_t FROM t", RiskClass.PRIVILEGED_CHANGE, "DDL"),
        ("DO $$ BEGIN PERFORM 1; END $$", RiskClass.PRIVILEGED_CHANGE, "ANONYMOUS_BLOCK"),
    ],
)
def test_classification(sql: str, risk: RiskClass, stype: str) -> None:
    c = classify_sql(sql)
    assert c.risk is risk, c.reasons
    assert c.statement_type == stype


def test_multiple_statements_rejected() -> None:
    c = classify_sql("SELECT 1; DROP TABLE t")
    assert c.statement_type == "MULTIPLE"
    assert c.statement_count == 2
    assert not c.read_only


def test_semicolon_inside_string_is_single_statement() -> None:
    c = classify_sql("SELECT 'a;b' AS x")
    assert c.statement_count == 1 and c.read_only


def test_explain_analyze_requires_permissions() -> None:
    c = classify_sql("EXPLAIN (ANALYZE, BUFFERS) DELETE FROM t WHERE id = 1")
    assert c.explain_analyze
    assert "query.explain_analyze" in c.required_permissions
    assert "query.modify" in c.required_permissions
    c2 = classify_sql("EXPLAIN ANALYZE SELECT 1")
    assert c2.required_permissions == ["query.explain_analyze"]


def test_read_requires_execute_only() -> None:
    assert classify_sql("select 1").required_permissions == ["query.execute"]
    perms = classify_sql("grant select on t to u").required_permissions
    assert "privilege.manage" in perms and "query.modify" in perms


def test_unknown_statement_fails_safe() -> None:
    c = classify_sql("FROBNICATE everything")
    assert c.risk in (RiskClass.PRIVILEGED_CHANGE, RiskClass.DESTRUCTIVE)
    assert not c.read_only


def test_empty() -> None:
    assert classify_sql("  -- nothing\n").statement_count == 0


def test_native_backup_and_restore_statements_are_not_unknown() -> None:
    b = classify_sql("BACKUP DATABASE [Sales] TO DISK = N'/var/opt/mssql/backup/sales.bak' WITH COPY_ONLY", "mssql")
    assert b.statement_type == "BACKUP" and b.risk == RiskClass.PRIVILEGED_CHANGE
    r = classify_sql("RESTORE DATABASE [Sales] FROM DISK = N'/var/opt/mssql/backup/sales.bak' WITH REPLACE", "mssql")
    assert r.statement_type == "RESTORE" and r.risk == RiskClass.DESTRUCTIVE
