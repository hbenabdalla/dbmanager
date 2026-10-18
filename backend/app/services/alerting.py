"""Alert evaluation: rules → alert instances → notifications.

Runs as the global ``evaluate_alerts`` job (``DBM_ALERT_EVALUATION_SECONDS``). Each rule is
evaluated against the collector's latest snapshot of every server in its scope
(``servers.latest_metrics`` — the same values the dashboards show, available even when the
time-series database is down), the server health state, or the collector components.

Lifecycle per (rule, server) — at most one active alert, enforced by a partial unique index:

    condition true  → pending (``for_seconds`` > 0) → firing  → notify "firing"
    acknowledged by a user  → still active, no repeat notifications
    condition false → resolved (``resolved_by = auto``) → notify "resolved" if the rule asks
                      a pending alert that never fired is deleted silently

Maintenance windows matching the server mark the alert ``suppressed`` and skip notifications
(a "skipped" delivery is logged). Alerts of simulated servers are flagged ``is_demo`` and only
notified through channels with ``send_for_simulated``.
"""

from __future__ import annotations

import logging
import operator as op
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import Alert, AlertRule, MaintenanceWindow, NotificationChannel, NotificationDelivery, Server
from app.models.alerting import ACTIVE_STATES
from app.services import notify

log = logging.getLogger("app.alerting")

OPERATORS = {">": op.gt, ">=": op.ge, "<": op.lt, "<=": op.le, "==": op.eq, "!=": op.ne}
KINDS = ("metric", "health", "collector")
SEVERITIES = ("critical", "high", "medium", "low", "info")
SCOPES = ("all", "environment", "server")
HEALTH_STATES = ("unavailable", "critical", "warning", "unknown")
#: metric values older than this many collection intervals are treated as "no data"
STALE_INTERVALS = 3


# ---- scope helpers ----------------------------------------------------------------------------
def in_scope(scope_type: str, scope_id: str | None, server: Server) -> bool:
    if scope_type == "all":
        return True
    if scope_type == "environment":
        return bool(server.environment_id) and str(server.environment_id) == scope_id
    if scope_type == "server":
        return str(server.id) == scope_id
    return False


def active_window(windows: list[MaintenanceWindow], server: Server, now: datetime) -> MaintenanceWindow | None:
    for w in windows:
        if w.starts_at <= now < w.ends_at and in_scope(w.scope_type, w.scope_id, server):
            return w
    return None


# ---- condition ----------------------------------------------------------------------------------
def _metric_value(rule: AlertRule, server: Server, now: datetime) -> float | None:
    latest = server.latest_metrics or {}
    raw = latest.get(rule.metric or "")
    if raw is None:
        return None
    at = latest.get("_collected_at")
    if at:
        try:
            age = (now - datetime.fromisoformat(at)).total_seconds()
        except ValueError:
            age = 0
        if age > STALE_INTERVALS * max(int(server.collection_interval_s or 60), 15):
            return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def condition(rule: AlertRule, server: Server, now: datetime) -> tuple[bool | None, float | None, str]:
    """(holds?, observed value, human description). ``None`` = no data: keep the current state."""
    if rule.kind == "metric":
        v = _metric_value(rule, server, now)
        if v is None or rule.operator not in OPERATORS or rule.threshold is None:
            return None, None, "no current value"
        holds = OPERATORS[rule.operator](v, rule.threshold)
        return holds, v, f"{rule.metric} = {v:g} ({rule.operator} {rule.threshold:g})"
    if rule.kind == "health":
        states = rule.health_states or ["unavailable"]
        holds = server.health_status in states
        reasons = "; ".join(server.health_reasons or []) or server.last_error or ""
        return holds, None, f"health {server.health_status}" + (f": {reasons}" if reasons else "")
    if rule.kind == "collector":
        comps = (server.collector_state or {}).get("components") or {}
        failing = sorted(n for n, c in comps.items() if (c or {}).get("status") in ("failed", "unavailable"))
        threshold = int(rule.threshold or get_settings().circuit_failure_threshold)
        holds = bool(failing) or server.consecutive_failures >= threshold
        return (
            holds,
            float(server.consecutive_failures),
            (
                f"failing collectors: {', '.join(failing)}"
                if failing
                else f"{server.consecutive_failures} consecutive failures"
            ),
        )
    return None, None, "unknown rule kind"


def alert_dict(alert: Alert, rule: AlertRule, server: Server) -> dict[str, Any]:
    base = get_settings().public_base_url.rstrip("/")
    return {
        "id": str(alert.id),
        "rule_id": str(rule.id),
        "rule": rule.name,
        "status": alert.status,
        "severity": alert.severity,
        "title": alert.title,
        "message": alert.message,
        "metric": alert.metric,
        "operator": rule.operator,
        "value": alert.value,
        "threshold": alert.threshold,
        "server_id": str(server.id),
        "server_name": server.name,
        "engine": server.engine,
        "environment": server.environment.name if server.environment else None,
        "is_demo": alert.is_demo,
        "fired_at": alert.fired_at.isoformat() if alert.fired_at else None,
        "resolved_at": alert.resolved_at.isoformat() if alert.resolved_at else None,
        "url": f"{base}/alerts?id={alert.id}",
    }


# ---- notifications -------------------------------------------------------------------------------
async def deliver(
    db: AsyncSession,
    channel: NotificationChannel,
    event: str,
    alert: Alert | None,
    data: dict[str, Any] | None,
    *,
    skip_reason: str | None = None,
) -> NotificationDelivery:
    d = NotificationDelivery(
        channel_id=channel.id, alert_id=alert.id if alert else None, event=event, status="skipped", attempts=0
    )
    if skip_reason:
        d.error = skip_reason
    else:
        for attempt in (1, 2):  # one retry for transient failures
            d.attempts = attempt
            try:
                d.response_code = await notify.send(channel, event, data)
                d.status, d.error = "sent", None
                break
            except notify.DeliveryError as exc:
                d.status, d.error, d.response_code = "failed", str(exc)[:2000], exc.code
                if exc.code is not None and 400 <= exc.code < 500:
                    break  # the receiver rejected it: retrying will not help
            except Exception as exc:  # noqa: BLE001 - recorded on the delivery row
                d.status, d.error = "failed", f"{type(exc).__name__}: {exc}"[:2000]
    db.add(d)
    return d


async def _notify(
    db: AsyncSession,
    rule: AlertRule,
    alert: Alert,
    server: Server,
    event: str,
    channels: dict[str, NotificationChannel],
    window: MaintenanceWindow | None,
) -> int:
    sent = 0
    data = alert_dict(alert, rule, server)
    for cid in rule.channel_ids or []:
        ch = channels.get(str(cid))
        if ch is None or not ch.enabled:
            continue
        if notify.SEVERITY_ORDER.get(alert.severity, 0) < notify.SEVERITY_ORDER.get(ch.min_severity, 0):
            continue
        reason = None
        if window is not None:
            reason = f"suppressed by maintenance window {window.name!r}"
        elif server.is_demo and not ch.send_for_simulated:
            reason = "simulated server: channel does not send for simulated servers"
        d = await deliver(db, ch, event, alert, data, skip_reason=reason)
        sent += d.status == "sent"
    return sent


# ---- evaluation -------------------------------------------------------------------------------------
async def evaluate(db: AsyncSession, now: datetime | None = None) -> dict[str, int]:
    now = now or datetime.now(UTC)
    rules = list((await db.execute(select(AlertRule).where(AlertRule.enabled.is_(True)))).scalars())
    stats = {"rules": len(rules), "fired": 0, "resolved": 0, "pending": 0, "notified": 0, "suppressed": 0}
    if not rules:
        return stats
    servers = list((await db.execute(select(Server).where(Server.monitoring_enabled.is_(True)))).scalars())
    windows = list(
        (
            await db.execute(
                select(MaintenanceWindow).where(MaintenanceWindow.starts_at <= now, MaintenanceWindow.ends_at > now)
            )
        ).scalars()
    )
    channels = {str(c.id): c for c in (await db.execute(select(NotificationChannel))).scalars()}
    active = {
        (a.rule_id, a.server_id): a
        for a in (await db.execute(select(Alert).where(Alert.status.in_(ACTIVE_STATES)))).scalars()
    }
    for rule in rules:
        for server in servers:
            if not in_scope(rule.scope_type, rule.scope_id, server):
                continue
            holds, value, desc = condition(rule, server, now)
            alert = active.get((rule.id, server.id))
            window = active_window(windows, server, now)
            if holds is None:
                if alert is not None:
                    alert.last_evaluated_at = now
                continue
            if holds:
                if alert is None:
                    alert = Alert(
                        id=uuid.uuid4(),
                        rule_id=rule.id,
                        server_id=server.id,
                        status="pending",
                        severity=rule.severity,
                        title=f"{rule.name} on {server.name}",
                        message=desc,
                        metric=rule.metric,
                        threshold=rule.threshold,
                        is_demo=server.is_demo,
                        pending_since=now,
                        last_evaluated_at=now,
                    )
                    db.add(alert)
                    active[(rule.id, server.id)] = alert
                alert.value, alert.message, alert.last_evaluated_at = value, desc, now
                alert.suppressed, alert.suppressed_by = window is not None, window.id if window else None
                if alert.status == "pending":
                    if (now - alert.pending_since) >= timedelta(seconds=rule.for_seconds):
                        alert.status, alert.fired_at = "firing", now
                        await db.flush()
                        stats["fired"] += 1
                        stats["suppressed"] += window is not None
                        stats["notified"] += await _notify(db, rule, alert, server, "firing", channels, window)
                    else:
                        stats["pending"] += 1
            elif alert is not None:
                del active[(rule.id, server.id)]
                if alert.status == "pending":
                    await db.delete(alert)  # never fired: nothing to tell anyone
                    continue
                alert.status, alert.resolved_at, alert.resolved_by = "resolved", now, "auto"
                alert.value, alert.message, alert.last_evaluated_at = value, desc, now
                await db.flush()
                stats["resolved"] += 1
                if rule.notify_on_resolve:
                    stats["notified"] += await _notify(db, rule, alert, server, "resolved", channels, window)
    await db.flush()
    return stats


async def resolve_for_rule(db: AsyncSession, rule: AlertRule, actor: str) -> int:
    """Close active alerts of a rule that was disabled or deleted (no notifications)."""
    now = datetime.now(UTC)
    rows = list(
        (await db.execute(select(Alert).where(Alert.rule_id == rule.id, Alert.status.in_(ACTIVE_STATES)))).scalars()
    )
    for a in rows:
        a.status, a.resolved_at, a.resolved_by = "resolved", now, actor
    return len(rows)
