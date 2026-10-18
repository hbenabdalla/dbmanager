"""Real / simulated filtering for fleet-wide views.

``source`` query parameter on fleet-wide endpoints:

* ``all``        — every visible server (the default for API compatibility)
* ``real``       — registered servers only
* ``simulated``  — the simulated demo fleet only
* ``auto``       — ``real`` when the caller can see at least one real server, else ``all``
  (what the UI uses, so simulated systems never blend into an operational view unnoticed)
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import Query
from sqlalchemy import ColumnElement, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DataSource, Server
from app.services.principal import Principal

FleetSource = Literal["auto", "all", "real", "simulated"]
SourceQuery = Annotated[
    FleetSource | None,
    Query(description="all | real | simulated | auto (real when real servers exist, otherwise all)"),
]


def condition(source: str) -> ColumnElement[bool] | None:
    if source == "real":
        return Server.data_source != DataSource.SIMULATED
    if source == "simulated":
        return Server.data_source == DataSource.SIMULATED
    return None


async def resolve(db: AsyncSession, p: Principal, source: str | None, default: str = "all") -> str:
    s = source or default
    if s != "auto":
        return s
    stmt = select(Server.id).where(Server.data_source != DataSource.SIMULATED)
    flt = p.authz.server_filter("server.view")
    if flt is not None:
        stmt = stmt.where(flt)
    has_real = (await db.execute(select(exists(stmt)))).scalar()
    return "real" if has_real else "all"
