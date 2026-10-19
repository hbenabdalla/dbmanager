"""Audit trail writer.

Audit events are written in their *own* transaction so that failed or denied operations
are recorded even when the request's unit of work rolls back. Secrets are stripped.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.core.logging import request_id_ctx
from app.db.session import session_scope
from app.models import AuditEvent, Server
from app.services.principal import Principal

log = logging.getLogger("app.audit")
_SENSITIVE_KEY = re.compile(r"pass(word)?|secret|token|credential|private|api[_-]?key|cookie", re.I)


def sanitize(value: Any, depth: int = 0) -> Any:
    if depth > 6:
        return "…"
    if isinstance(value, dict):
        return {k: ("***" if _SENSITIVE_KEY.search(str(k)) else sanitize(v, depth + 1)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize(v, depth + 1) for v in list(value)[:200]]
    if isinstance(value, str) and len(value) > 4000:
        return value[:4000] + "…"
    if isinstance(value, (uuid.UUID, datetime, date, Decimal)):
        return str(value) if not isinstance(value, (datetime, date)) else value.isoformat()
    return value


#: audit event categories (SIEM destinations filter on these)
CATEGORIES = (
    "auth",
    "admin",
    "security",
    "platform",
    "alerting",
    "query",
    "backup",
    "collector",
    "operations",
    "report",
)


async def record(
    action: str,
    *,
    result: str = "success",
    actor: Principal | None = None,
    username: str | None = None,
    auth_source: str | None = None,
    source_ip: str | None = None,
    category: str = "admin",
    risk_class: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    server: Server | None = None,
    database: str | None = None,
    object_name: str | None = None,
    failure_reason: str | None = None,
    change_reason: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    event = AuditEvent(
        user_id=actor.user.id if actor else None,
        username=actor.username if actor else username,
        auth_source=actor.auth_source if actor else auth_source,
        source_ip=actor.source_ip if actor else source_ip,
        action=action,
        category=category,
        risk_class=risk_class,
        target_type=target_type,
        target_id=target_id,
        server_id=server.id if server else None,
        server_name=server.name if server else None,
        database=database,
        object_name=object_name,
        result=result,
        failure_reason=(failure_reason or None) and failure_reason[:2000],
        change_reason=change_reason,
        request_id=request_id_ctx.get(),
        details=sanitize(details) if details else None,
    )
    try:
        async with session_scope() as s:
            s.add(event)
    except Exception:  # noqa: BLE001 - audit failure must be visible but not crash the caller
        log.exception("failed to write audit event", extra={"action": action})
