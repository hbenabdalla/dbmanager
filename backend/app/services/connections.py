"""Builds adapters for registered servers and maps adapter errors to API errors."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import (
    AdapterConnectionError,
    AdapterError,
    AdapterNotSupported,
    AdapterPermissionError,
    BaseDatabaseAdapter,
    Capability,
    CapabilityLevel,
    ConnectionSpec,
)
from app.adapters.registry import create_adapter
from app.core.config import get_settings
from app.core.errors import AppError, CapabilityNotSupported, TargetUnavailable, ValidationFailed
from app.core.metrics import TARGET_CONNECTIONS
from app.models import Server
from app.services import secrets


class TargetError(AppError):
    status_code = 422
    code = "target_error"


class TargetPermissionError(AppError):
    status_code = 422
    code = "target_permission_denied"


def build_spec(
    server: Server, username: str | None, password: str | None, *, database: str | None = None
) -> ConnectionSpec:
    s = get_settings()
    return ConnectionSpec(
        host=server.host,
        port=server.port,
        database=database or server.default_database,
        username=username,
        password=password,
        tls_mode=server.tls_mode,
        tls_ca_pem=server.tls_ca_pem,
        connect_timeout=float(server.options.get("connect_timeout", s.target_connect_timeout_seconds)),
        statement_timeout=float(server.options.get("statement_timeout", s.target_statement_timeout_seconds)),
        licensed_features=list(server.licensed_features or []),
        options=dict(server.options or {}),
        demo_profile=(server.options or {}).get("demo_profile") if server.is_demo else None,
    )


class MonitoringOnly(AppError):
    status_code = 409
    code = "monitoring_only"


def management_mode(server: Server) -> str:
    return str((server.options or {}).get("management_mode") or "monitoring")


def require_administration(server: Server) -> None:
    """Change actions are refused for servers registered as *Monitoring only*, even when the
    stored credential would technically allow them."""
    if management_mode(server) == "monitoring":
        raise MonitoringOnly(
            "This server is registered as Monitoring only: DBManager does not perform changes on it. "
            "Switch it to Administration enabled under Server → Settings to allow guarded changes.",
            details={"management_mode": "monitoring"},
        )


async def adapter_for(db: AsyncSession, server: Server, purpose: str = "monitoring") -> BaseDatabaseAdapter:
    """Create an adapter for ``server`` using its monitoring, read-only or admin credential."""
    if purpose == "admin":
        require_administration(server)
    if server.is_demo:
        return create_adapter(server.engine, build_spec(server, "dbm_monitor", None), demo=True)
    cred = await secrets.get_credential(db, server.id, purpose)
    if cred is None and purpose == "admin":
        raise ValidationFailed(
            "This action needs an administration credential for the server. Add one under Server → Credentials.",
            code="admin_credential_required",
        )
    if cred is None:
        raise ValidationFailed("No monitoring credential is configured for this server", code="credential_required")
    password = await secrets.resolve_password_async(cred)
    return create_adapter(server.engine, build_spec(server, cred.username, password))


async def adapter_preferring(db: AsyncSession, server: Server, first: str, fallback: str) -> BaseDatabaseAdapter:
    if first == "admin" and management_mode(server) == "monitoring":
        # read paths on monitoring-only servers use the read-only or monitoring credential
        first = "readonly"
    if not server.is_demo and await secrets.get_credential(db, server.id, first) is None:
        return await adapter_for(db, server, fallback)
    return await adapter_for(db, server, first)


@asynccontextmanager
async def target(adapter: BaseDatabaseAdapter) -> AsyncIterator[BaseDatabaseAdapter]:
    """Run adapter calls with consistent error mapping and guaranteed cleanup."""
    TARGET_CONNECTIONS.inc()
    try:
        yield adapter
    except AdapterNotSupported as exc:
        raise CapabilityNotSupported(
            f"Not available for this server: {exc.reason}",
            details={"capability": exc.capability, "reason": exc.reason, "error_code": exc.code.value},
        ) from exc
    except AdapterConnectionError as exc:
        raise TargetUnavailable(
            f"Cannot reach the database server: {exc}", details={"error_code": exc.code.value}
        ) from exc
    except AdapterPermissionError as exc:
        raise TargetPermissionError(
            f"The database refused the request: {exc}", details={"error_code": exc.code.value}
        ) from exc
    except AdapterError as exc:
        raise TargetError(f"The database returned an error: {exc}", details={"error_code": exc.code.value}) from exc
    finally:
        TARGET_CONNECTIONS.dec()
        await adapter.close()


def require_capability(server: Server, capability: Capability) -> None:
    """Fail fast (without connecting) when discovery says a capability is unusable."""
    caps: dict[str, Any] = server.capabilities or {}
    entry = caps.get(capability.value)
    if not entry:
        return  # not discovered yet: let the adapter decide
    level = CapabilityLevel(entry.get("level", "FULL"))
    if not level.usable:
        raise CapabilityNotSupported(
            f"{capability.value.replace('_', ' ').capitalize()} is not available for this server: "
            f"{entry.get('note') or level.value}",
            details={"capability": capability.value, "level": level.value, "note": entry.get("note")},
        )
