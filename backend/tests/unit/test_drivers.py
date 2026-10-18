"""Driver health reflects runtime reality (Platform → Database drivers, registration)."""

from __future__ import annotations

import pytest

from app.adapters import drivers
from app.adapters.base import DriverNotInstalled


def _row(key: str) -> dict:  # type: ignore[type-arg]
    return next(r for r in drivers.health() if r["key"] == key)


def test_sql_server_driver_missing_when_no_odbc_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(drivers, "_odbc_drivers", lambda: [])
    row = _row("pyodbc")
    assert row["status"] == "missing"
    assert row["remediation"].startswith("Microsoft ODBC Driver 18 is required.")
    with pytest.raises(DriverNotInstalled, match="Microsoft ODBC Driver 18 is required"):
        drivers.require_engine_driver("mssql")
    with pytest.raises(DriverNotInstalled, match="Microsoft ODBC Driver 18 is required"):
        drivers.odbc_driver_name()


def test_sql_server_driver_17_only_is_degraded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(drivers, "_odbc_drivers", lambda: ["ODBC Driver 17 for SQL Server"])
    row = _row("pyodbc")
    assert row["status"] == "degraded" and "ODBC Driver 18" in row["error"]
    drivers.require_engine_driver("mssql")  # still usable
    assert drivers.odbc_driver_name() == "ODBC Driver 17 for SQL Server"


def test_sql_server_driver_18_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(drivers, "_odbc_drivers", lambda: ["ODBC Driver 18 for SQL Server"])
    assert _row("pyodbc")["status"] == "installed"


def test_python_module_missing_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(key: str):  # type: ignore[no-untyped-def]
        raise DriverNotInstalled(f"database driver {key!r} is not available")

    real = drivers.load
    monkeypatch.setattr(drivers, "load", lambda k: boom(k) if k == "ibm_db" else real(k))
    row = _row("ibm_db")
    assert row["status"] == "missing" and row["remediation"]
    with pytest.raises(DriverNotInstalled):
        drivers.require_engine_driver("db2zos")


def test_engines_without_driver_need_nothing() -> None:
    assert drivers.engine_driver("demo") is None
    drivers.require_engine_driver("demo")
