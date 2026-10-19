"""Regression tests for defects found by the real-engine validation run of 0.12.0."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.adapters.base import AdapterError
from app.backup import engines
from app.services import ldap_auth


def test_ldap_groups_found_twice_are_listed_once() -> None:
    dns = [
        "cn=dbadmins,ou=groups,dc=example,dc=org",
        "CN=DBAdmins, OU=groups, DC=example, DC=org",
        "cn=developers,ou=groups,dc=example,dc=org",
    ]
    assert ldap_auth.unique_dns(dns) == [dns[0], dns[2]]


def test_pg_restore_script_drops_only_settings_the_server_lacks(tmp_path: Path) -> None:
    script = tmp_path / "r.sql"
    script.write_text(
        "SET statement_timeout = 0;\nSET transaction_timeout = 0;\n"
        "COPY t (note) FROM stdin;\nSET transaction_timeout = 0;\n\\.\n"
    )
    # PostgreSQL 16 does not know transaction_timeout (pg_dump / pg_restore 17 emit it)
    assert engines.filter_settings(script, 16) == ["SET transaction_timeout = 0;", "SET transaction_timeout = 0;"]
    assert "transaction_timeout" not in script.read_text() and "statement_timeout" in script.read_text()
    script.write_text("SET transaction_timeout = 0;\n")
    assert engines.filter_settings(script, 17) == [] and "transaction_timeout" in script.read_text()


def test_definers_are_removed_for_least_privilege_restores(tmp_path: Path) -> None:
    src, dst = tmp_path / "a.sql", tmp_path / "b.sql"
    src.write_bytes(
        b"/*!50017 DEFINER=`root`@`localhost`*/ /*!50003 TRIGGER trg BEFORE INSERT ON t */;\n"
        b"CREATE DEFINER=`app``x`@`%` PROCEDURE p() SELECT 1;\nSELECT 'DEFINER = x' AS literal;\n"
    )
    assert engines.strip_definers(src, dst) == 2
    out = dst.read_text()
    assert "root" not in out and "app" not in out and "'DEFINER = x'" in out


class _FailingAdapter:
    """Like SyncDBAPIAdapter._run: every exception surfaces as an AdapterError."""

    async def _run(self, fn, *args, timeout=None):  # type: ignore[no-untyped-def]  # noqa: ASYNC109
        try:
            return fn(*args)
        except Exception as exc:
            raise AdapterError(str(exc)) from exc


async def test_engine_errors_from_the_adapter_thread_are_backup_errors() -> None:
    def refuse() -> None:
        raise engines.BackupProviderError("database 'x' already exists: restores never overwrite a database")

    with pytest.raises(engines.BackupProviderError, match="already exists"):
        await engines._in_adapter(_FailingAdapter(), refuse)

    def driver_failure() -> None:
        raise RuntimeError("[42000] permission denied")

    with pytest.raises(engines.BackupProviderError, match="permission denied"):
        await engines._in_adapter(_FailingAdapter(), driver_failure)


def test_db2_restore_commands_are_complete() -> None:
    rec = SimpleNamespace(location="/database/dbm-it-backups#20261020101500", database_name="TESTDB")
    cmd = engines.provider("db2").restore_command(rec, "DBMRST") or ""
    assert "RESTORE DATABASE TESTDB FROM /database/dbm-it-backups TAKEN AT 20261020101500 INTO DBMRST" in cmd
    assert "LOGTARGET /database/dbm-it-backups/dbm-restore-logs-20261020101500" in cmd
    assert "OVERFLOW LOG PATH (/database/dbm-it-backups/dbm-restore-logs-20261020101500)" in cmd
    assert "(...)" not in cmd
