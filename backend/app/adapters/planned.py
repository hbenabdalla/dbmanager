"""Scaffolding for adapters of engines that are not implemented yet.

All shipped adapters (PostgreSQL, MySQL, MariaDB, SQL Server, Oracle, Db2 LUW, Db2 for z/OS) are
implemented; this module keeps the helpers used to describe an engine before its adapter
exists. Such a class declares:

* ``engine_capabilities`` — an honest description of what the *engine* offers to a
  management tool (including licensing / extension prerequisites). This drives the
  published support matrix.
* ``static_capabilities`` — what this adapter implements today. Until the phase lands, every
  capability is ``PLANNED`` and every method raises :class:`AdapterNotSupported`, so the UI
  shows the server in inventory but disables live features instead of faking data.
"""

from __future__ import annotations

from typing import ClassVar

from app.adapters.base import (
    FULL,
    NOT_SUPPORTED,
    PARTIAL,
    PLANNED,
    REQUIRES_EXTENSION,
    REQUIRES_LICENSE,
    REQUIRES_PRIVILEGE,
    BaseDatabaseAdapter,
    Capability,
    CapabilityMap,
    cap,
)

C = Capability


def _planned(engine_caps: CapabilityMap) -> CapabilityMap:
    return {c: cap(PLANNED) if s.level is not NOT_SUPPORTED else s for c, s in engine_caps.items()}


_COMMON_FULL = {
    C.CONNECTION,
    C.SERVER_INFO,
    C.DATABASES,
    C.SCHEMAS,
    C.OBJECTS,
    C.USERS,
    C.ROLES,
    C.PRIVILEGES,
    C.SESSIONS,
    C.SESSION_CANCEL,
    C.SESSION_TERMINATE,
    C.LOCKS,
    C.PERFORMANCE_METRICS,
    C.WAIT_EVENTS,
    C.EXPLAIN,
    C.CONFIGURATION,
    C.STORAGE,
    C.SECURITY_SCAN,
    C.SQL_WORKSPACE,
}


def _base(**overrides: object) -> CapabilityMap:
    m: CapabilityMap = {c: cap(FULL) for c in _COMMON_FULL}
    m.update(overrides)  # type: ignore[arg-type]
    return m


# Every engine of phases 3–6 now has a real adapter (mysql/, mssql/, oracle/, db2/, db2zos/).
# New engines start here: subclass BaseDatabaseAdapter, describe engine_capabilities and set
# static_capabilities = _planned(engine_capabilities) until the adapter is implemented.
__all__ = [
    "_COMMON_FULL",
    "_base",
    "_planned",
    "BaseDatabaseAdapter",
    "PARTIAL",
    "FULL",
    "REQUIRES_EXTENSION",
    "REQUIRES_LICENSE",
    "REQUIRES_PRIVILEGE",
    "ClassVar",
]
