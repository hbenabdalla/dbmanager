"""Adapter registry: maps engine identifiers to adapter implementations.

New engines register here (or via ``register``) — no other core code changes are needed.
"""

from __future__ import annotations

from app.adapters.base import BaseDatabaseAdapter, ConnectionSpec
from app.adapters.db2.adapter import Db2Adapter
from app.adapters.db2zos.adapter import Db2zOSAdapter
from app.adapters.demo.adapter import DemoAdapter
from app.adapters.mssql.adapter import SQLServerAdapter
from app.adapters.mysql.adapter import MariaDBAdapter, MySQLAdapter
from app.adapters.oracle.adapter import OracleAdapter
from app.adapters.postgres.adapter import PostgreSQLAdapter

_REGISTRY: dict[str, type[BaseDatabaseAdapter]] = {}


def register(cls: type[BaseDatabaseAdapter]) -> type[BaseDatabaseAdapter]:
    _REGISTRY[cls.engine] = cls
    return cls


for _cls in (
    PostgreSQLAdapter,
    MySQLAdapter,
    MariaDBAdapter,
    SQLServerAdapter,
    OracleAdapter,
    Db2Adapter,
    Db2zOSAdapter,
):
    register(_cls)

ENGINE_ALIASES = {"postgres": "postgresql", "pg": "postgresql", "sqlserver": "mssql", "db2luw": "db2"}

# Deployment flavours layered on engine adapters (cloud extensions, Phase 7).
CLOUD_DEPLOYMENTS = {
    "self-managed": "Self-managed",
    "aws-rds": "Amazon RDS",
    "aws-aurora": "Amazon Aurora",
    "azure-sql": "Azure SQL Database",
    "azure-flexible": "Azure Database (Flexible Server)",
    "gcp-cloudsql": "Google Cloud SQL",
    "oci-autonomous": "Oracle Autonomous Database",
    "ibm-db2-cloud": "IBM Db2 on Cloud",
}


def normalize_engine(engine: str) -> str:
    e = engine.lower().strip()
    return ENGINE_ALIASES.get(e, e)


def supported_engines() -> dict[str, type[BaseDatabaseAdapter]]:
    return dict(_REGISTRY)


def adapter_class_for(engine: str) -> type[BaseDatabaseAdapter]:
    try:
        return _REGISTRY[normalize_engine(engine)]
    except KeyError as exc:
        raise ValueError(f"unsupported engine {engine!r}") from exc


def create_adapter(engine: str, spec: ConnectionSpec, *, demo: bool = False) -> BaseDatabaseAdapter:
    """Instantiate the adapter for a server.

    ``demo=True`` (simulated servers) always yields the simulation adapter, which never opens a
    network connection. A spec carrying a simulation profile is refused by real adapters, so a
    simulated server can never be routed to a real database by mistake.
    """
    if demo:
        return DemoAdapter(spec)
    if spec.demo_profile is not None:
        raise ValueError("simulated servers must use the simulation adapter")
    return adapter_class_for(engine)(spec)
