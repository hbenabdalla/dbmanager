"""Shared test configuration.

Unit tests need nothing external. API tests need a PostgreSQL database reachable via
DBM_TEST_DATABASE_URL (defaults to a local dbmanager_test database); they are skipped
automatically when it is unavailable.
"""

from __future__ import annotations

import base64
import os

os.environ.setdefault("DBM_TEST_DATABASE_URL", "postgresql+asyncpg://dbm:dbm_dev_pw@localhost:5432/dbmanager_test")
os.environ["DBM_DATABASE_URL"] = os.environ["DBM_TEST_DATABASE_URL"]
os.environ.setdefault("DBM_MASTER_KEY", base64.b64encode(b"k" * 32).decode())
os.environ["DBM_ENV"] = "test"
os.environ["DBM_BOOTSTRAP_ADMIN_PASSWORD"] = "Admin-Passw0rd!2026"
os.environ["DBM_COOKIE_SECURE"] = "false"
os.environ["DBM_LOG_JSON"] = "false"
os.environ["DBM_LOG_LEVEL"] = "WARNING"
os.environ["DBM_LOGIN_RATE_PER_IP_PER_MINUTE"] = "1000"
# rate limits are exercised by tests/api/test_rate_limits.py with explicit, small limits
for _name in ("API_RATE_PER_USER_PER_MINUTE", "API_RATE_TARGET_PER_MINUTE", "API_RATE_EXPENSIVE_PER_MINUTE"):
    os.environ[f"DBM_{_name}"] = "100000"
# monitoring telemetry goes to an in-process time-series repository in tests (the
# VictoriaMetrics integration tests opt in explicitly via DBM_IT_VM_URL)
os.environ.setdefault("DBM_TIMESERIES_BACKEND", "memory")


# ---- markers: fast unit tests vs API tests vs real-database integration tests ------------
_ENGINE_MARKERS = {
    "test_postgres_adapter.py": "postgresql",
    "test_mssql.py": "sqlserver",
    "test_oracle.py": "oracle",
    "test_db2.py": "db2",
    "test_victoriametrics.py": "timeseries",
}


def pytest_collection_modifyitems(config, items):  # type: ignore[no-untyped-def]
    import pytest

    for item in items:
        parts = item.path.parts
        if "unit" in parts:
            item.add_marker(pytest.mark.unit)
        elif "api" in parts:
            item.add_marker(pytest.mark.api)
        elif "integration" in parts:
            item.add_marker(pytest.mark.integration)
            if item.path.name != "test_victoriametrics.py":
                item.add_marker(pytest.mark.real_database)
            if item.path.name == "test_mysql_family.py":
                flavor = getattr(getattr(item, "callspec", None), "params", {}).get("flavor")
                item.add_marker(getattr(pytest.mark, flavor or "mysql"))
            elif item.path.name in _ENGINE_MARKERS:
                item.add_marker(getattr(pytest.mark, _ENGINE_MARKERS[item.path.name]))
