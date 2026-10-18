"""Database driver registry and health checks (Platform → Database Drivers).

Drivers are imported lazily: an engine whose driver is missing in a given image still loads,
registers and shows up in inventory, and every connection attempt fails with a clear
``DRIVER_NOT_INSTALLED`` error instead of an import error at start-up.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import logging
from dataclasses import dataclass
from types import ModuleType
from typing import Any

from app.adapters.base import DriverNotInstalled

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DriverSpec:
    key: str
    module: str
    distribution: str
    engines: tuple[str, ...]
    description: str
    license: str
    client_dependency: str | None = None


DRIVERS: dict[str, DriverSpec] = {
    d.key: d
    for d in (
        DriverSpec(
            "asyncpg",
            "asyncpg",
            "asyncpg",
            ("postgresql",),
            "Native asyncio PostgreSQL protocol implementation",
            "Apache-2.0",
        ),
        DriverSpec(
            "pymysql",
            "pymysql",
            "PyMySQL",
            ("mysql", "mariadb"),
            "Pure-Python MySQL/MariaDB client protocol (TLS, caching_sha2_password)",
            "MIT",
        ),
        DriverSpec(
            "pyodbc",
            "pyodbc",
            "pyodbc",
            ("mssql",),
            "ODBC bridge used with the Microsoft ODBC Driver 18 for SQL Server",
            "MIT",
            client_dependency="Microsoft ODBC Driver 17/18 for SQL Server (msodbcsql18) + unixODBC",
        ),
        DriverSpec(
            "oracledb",
            "oracledb",
            "oracledb",
            ("oracle",),
            "python-oracledb in thin mode (no Oracle Client libraries required)",
            "Apache-2.0 / UPL",
        ),
        DriverSpec(
            "ibm_db",
            "ibm_db_dbi",
            "ibm_db",
            ("db2", "db2zos"),
            "IBM Db2 driver with the bundled IBM Data Server Driver (clidriver)",
            "Apache-2.0 (clidriver: IBM license)",
            client_dependency="IBM Data Server Driver for ODBC and CLI (bundled clidriver); "
            "Db2 for z/OS additionally requires a Db2 Connect license",
        ),
    )
}

_loaded: dict[str, ModuleType] = {}

#: what an operator has to install when a driver is missing (shown on Platform → Database
#: drivers, in the registration wizard and in test-connection errors)
REMEDIATION: dict[str, str] = {
    "pyodbc": "Microsoft ODBC Driver 18 is required. Rebuild the backend image with INSTALL_MSODBC=true "
    "(the default) and network access to packages.microsoft.com, or install msodbcsql18 and unixODBC on "
    "the backend host.",
    "ibm_db": "The ibm_db wheel (with its bundled IBM clidriver) is required: rebuild the backend image.",
    "oracledb": "python-oracledb is required: rebuild the backend image.",
    "pymysql": "PyMySQL is required: rebuild the backend image.",
    "asyncpg": "asyncpg is required: rebuild the backend image.",
}
#: statuses: installed | degraded (works with a limitation) | missing (connections will fail)
DRIVER_LABELS = {"installed": "Installed", "degraded": "Degraded", "missing": "Missing"}


def load(key: str) -> ModuleType:
    """Import a driver module or raise :class:`DriverNotInstalled`."""
    if key in _loaded:
        return _loaded[key]
    spec = DRIVERS[key]
    try:
        mod = importlib.import_module(spec.module)
    except Exception as exc:  # ImportError or a missing native library (OSError)
        raise DriverNotInstalled(
            f"database driver {spec.distribution!r} is not available in this backend image ({type(exc).__name__}: {exc})"
        ) from exc
    _loaded[key] = mod
    return mod


def _version(spec: DriverSpec) -> str | None:
    try:
        return importlib.metadata.version(spec.distribution)
    except importlib.metadata.PackageNotFoundError:
        return None


def _odbc_drivers() -> list[str]:
    try:
        mod = load("pyodbc")
        return [d for d in mod.drivers() if "SQL Server" in d]
    except Exception:  # noqa: BLE001
        return []


def health() -> list[dict[str, Any]]:
    """Installed state of every driver (no network access)."""
    out = []
    for spec in DRIVERS.values():
        row: dict[str, Any] = {
            "key": spec.key,
            "driver": spec.distribution,
            "engines": list(spec.engines),
            "description": spec.description,
            "license": spec.license,
            "client_dependency": spec.client_dependency,
            "version": _version(spec),
            "remediation": None,
        }
        try:
            mod = load(spec.key)
            row["status"] = "installed"
            row["error"] = None
            if spec.key == "pyodbc":
                drivers = _odbc_drivers()
                row["details"] = {"odbc_drivers": drivers, "pyodbc_version": getattr(mod, "version", None)}
                if not drivers:
                    row["status"] = "missing"
                    row["error"] = (
                        "pyodbc is installed but no Microsoft ODBC Driver for SQL Server is registered with unixODBC"
                    )
                    row["remediation"] = REMEDIATION["pyodbc"]
                elif "ODBC Driver 18 for SQL Server" not in drivers:
                    row["status"] = "degraded"
                    row["error"] = (
                        f"only {', '.join(drivers)} is registered; ODBC Driver 18 is recommended (TLS 1.3, strict encryption)"
                    )
            elif spec.key == "oracledb":
                row["details"] = {"mode": "thin" if mod.is_thin_mode() else "thick"}
            elif spec.key == "ibm_db":
                import ibm_db  # noqa: PLC0415 - imported only when the DBAPI wrapper loaded

                row["details"] = {"ibm_db": getattr(ibm_db, "__version__", None) or row["version"]}
        except DriverNotInstalled as exc:
            row["status"] = "missing"
            row["error"] = str(exc)
            row["remediation"] = REMEDIATION.get(spec.key)
        out.append(row)
    return out


def engine_driver(engine: str) -> dict[str, Any] | None:
    """Driver health row for one engine (``None`` when the engine needs no driver, e.g. demo)."""
    for row in health():
        if engine in row["engines"]:
            return row
    return None


def require_engine_driver(engine: str) -> None:
    """Fail fast, before any network I/O, when the engine's driver cannot work in this backend."""
    row = engine_driver(engine)
    if row is not None and row["status"] == "missing":
        raise DriverNotInstalled(row["remediation"] or row["error"] or f"driver for {engine} is missing")


def odbc_driver_name(preferred: str | None = None) -> str:
    """Pick the newest installed Microsoft ODBC driver (18 preferred) unless one is configured."""
    if preferred:
        return preferred
    drivers = _odbc_drivers()
    for name in ("ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server"):
        if name in drivers:
            return name
    mssql = [d for d in drivers if "SQL Server" in d]
    if mssql:
        return mssql[-1]
    raise DriverNotInstalled(REMEDIATION["pyodbc"])
