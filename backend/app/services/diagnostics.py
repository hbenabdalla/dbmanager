"""Collector diagnostic bundle of one server (sprint 0.10.5).

Everything support needs to understand why monitoring of a server misbehaves — and nothing
that could leak access to it. The bundle holds the connection settings (no credentials: only
which credentials exist and where they come from), runtime health and collector state, the
capability snapshot, recent collector jobs with timings and errors, the database driver
versions and the platform version. Every string goes through :mod:`app.core.redact`, so
passwords, tokens, keys and ``user:password@`` in error messages or options are masked.
"""

from __future__ import annotations

import platform as _platform
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app import __version__
from app.core import redact
from app.models import CollectorJob, Credential, Server

FORMAT = "dbmanager-diagnostics/1"
MAX_JOBS = 50


def _iso(v: datetime | None) -> str | None:
    return v.isoformat() if v else None


def _mask_user(name: str | None) -> str | None:
    if not name:
        return name
    return name[0] + "***" if len(name) > 1 else "*"


async def bundle(db: AsyncSession, server: Server, requested_by: str) -> dict[str, Any]:
    from app.adapters import drivers
    from app.services import inventory
    from app.services.connections import management_mode

    creds = (await db.execute(select(Credential).where(Credential.server_id == server.id))).scalars()
    jobs = (
        await db.execute(
            select(CollectorJob)
            .where(CollectorJob.server_id == server.id)
            .order_by(CollectorJob.created_at.desc())
            .limit(MAX_JOBS)
        )
    ).scalars()
    try:
        schema = (await db.execute(text("SELECT version_num FROM alembic_version"))).scalar_one_or_none()
    except Exception:  # noqa: BLE001 - informational
        schema = None
    driver = drivers.engine_driver(server.engine)
    out: dict[str, Any] = {
        "format": FORMAT,
        "generated_at": datetime.now(UTC).isoformat(),
        "generated_by": requested_by,
        "platform": {
            "dbmanager": __version__,
            "schema": schema,
            "python": _platform.python_version(),
        },
        "server": {
            "id": str(server.id),
            "name": server.name,
            "engine": server.engine,
            "version": server.version,
            "data_source": server.data_source,
            "environment": server.environment.name if server.environment else None,
            "deployment": server.deployment,
            "host": server.host,
            "port": server.port,
            "default_database": server.default_database,
            "tls_mode": server.tls_mode,
            "tls_ca_configured": bool(server.tls_ca_pem),
            "management_mode": management_mode(server),
            "monitoring_enabled": server.monitoring_enabled,
            "collection_interval_s": server.collection_interval_s,
            "licensed_features": server.licensed_features,
            "connection_options": inventory.connection_options(server),
        },
        # which logins are configured (never their secrets); the key avoids the "credential" mask
        "logins": [
            {
                "purpose": c.purpose,
                "username": _mask_user(c.username),
                "provider": c.provider,
                # env/file references name where the secret lives, not the secret itself
                "reference": c.secret_ref if c.provider in ("env", "file", "vault") else None,
                "has_secret": bool(c.encrypted) or bool(c.secret_ref),
                "rotated_at": _iso(c.rotated_at),
            }
            for c in creds
        ],
        "health": {
            "status": server.health_status,
            "reasons": server.health_reasons,
            "connection_status": server.connection_status,
            "last_error": server.last_error,
            "last_connected_at": _iso(server.last_connected_at),
            "last_collected_at": _iso(server.last_collected_at),
            "consecutive_failures": server.consecutive_failures,
            "circuit_open_until": _iso(server.circuit_open_until),
        },
        "collector_state": server.collector_state,
        "capabilities": server.capabilities,
        "driver": driver,
        "jobs": [
            {
                "kind": j.kind,
                "status": j.status,
                "trigger": j.trigger,
                "attempts": j.attempts,
                "created_at": _iso(j.created_at),
                "started_at": _iso(j.started_at),
                "finished_at": _iso(j.finished_at),
                "duration_ms": j.duration_ms,
                "error": j.last_error,
                "result": j.result,
            }
            for j in jobs
        ],
    }
    return redact.data(out)
