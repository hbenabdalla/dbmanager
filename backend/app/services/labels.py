"""Human readable labels for scope references (used in RBAC views)."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Cluster, Environment, Server, Site


async def scope_labels(db: AsyncSession, scopes: list[tuple[str, str]]) -> dict[tuple[str, str], str]:
    ids: dict[str, set[uuid.UUID]] = {"environment": set(), "site": set(), "cluster": set(), "server": set()}
    for t, sid in scopes:
        key = "server" if t in ("server", "database", "schema") else t
        if key in ids:
            try:
                ids[key].add(uuid.UUID(sid.split("/")[0]))
            except ValueError:
                pass
    names: dict[tuple[str, uuid.UUID], str] = {}
    for key, model in (("environment", Environment), ("site", Site), ("cluster", Cluster), ("server", Server)):
        if ids[key]:
            for oid, name in (await db.execute(select(model.id, model.name).where(model.id.in_(ids[key])))).all():
                names[(key, oid)] = name
    out: dict[tuple[str, str], str] = {}
    for t, sid in scopes:
        if t == "platform":
            out[(t, sid)] = "Entire platform"
            continue
        parts = sid.split("/")
        key = "server" if t in ("server", "database", "schema") else t
        try:
            base = names.get((key, uuid.UUID(parts[0])), parts[0])
        except ValueError:
            base = parts[0]
        out[(t, sid)] = " / ".join([base, *parts[1:]])
    return out
