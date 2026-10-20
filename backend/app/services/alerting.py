"""Alert evaluation: rules → alert instances → notifications.

Runs as the global ``evaluate_alerts`` job (``DBM_ALERT_EVALUATION_SECONDS``); failed deliveries
are retried by the ``notification_retries`` job. Metric rules compare either the collector's
latest snapshot of every server in scope (``servers.latest_metrics``, the values the dashboards
show, available even when the time-series database is down) or an average / minimum / maximum
over a window of history read from the time-series database. Health and collector rules use the
server health state and the collector components.

Lifecycle per (rule, server) — at most one active alert, enforced by a partial unique index:

    condition true  → pending (``for_seconds`` > 0) → firing  → notify "firing"
    acknowledged by a user  → still active, no further notifications
    still firing and unacknowledged after ``renotify_minutes`` → notify "firing" again
    condition false → resolved (``resolved_by = auto``) → notify "resolved" if the rule asks
                      a pending alert that never fired is deleted silently

An alert that fires inside a maintenance window is marked ``suppressed`` and not notified (a
"skipped" delivery is logged). When the window ends and the alert is still firing (and was
never notified), the "firing" notification is sent then. Alerts of simulated servers are flagged
``is_demo`` and only notified through channels with ``send_for_simulated``.

Routing: an alert goes to the channels its rule lists and to channels that subscribe to all
alerts (``routes.subscribe_all``); a channel only receives alerts at or above its minimum
severity and matching its route filters (environments, tags, engines).
"""

from __future__ import annotations

import logging
import operator as op
import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import Alert, AlertRule, MaintenanceWindow, NotificationChannel, NotificationDelivery, Server
from app.models.alerting import ACTIVE_STATES
from app.services import notify

log = logging.getLogger("app.alerting")

OPERATORS = {">": op.gt, ">=": op.ge, "<": op.lt, "<=": op.le, "==": op.eq, "!=": op.ne}
KINDS = ("metric", "health", "collector")
SEVERITIES = ("critical", "high", "medium", "low", "info")
SCOPES = ("all", "environment", "server", "tag")
AGGREGATIONS = ("latest", "avg", "min", "max")
RECURRENCES = ("none", "daily", "weekly")
HEALTH_STATES = ("unavailable", "critical", "warning", "unknown")
#: metric values older than this many collection intervals are treated as "no data"
STALE_INTERVALS = 3
#: longest single occurrence of a recurring maintenance window
MAX_RECURRING_DURATION = timedelta(hours=24)
#: delivery failures worth retrying: network errors (no HTTP code), 408, 425, 429 and 5xx
RETRYABLE_CODES = {408, 425, 429}


# ---- scope helpers ----------------------------------------------------------------------------
def tag_matches(spec: str | None, server: Server) -> bool:
    """``key`` matches any value of that tag, ``key=value`` an exact value (case-insensitive key)."""
    if not spec:
        return False
    key, _, value = spec.partition("=")
    key = key.strip().lower()
    for t in server.tags or []:
        if t.key.lower() == key and ("=" not in spec or (t.value or "") == value.strip()):
            return True
    return False


def in_scope(scope_type: str, scope_id: str | None, server: Server) -> bool:
    if scope_type == "all":
        return True
    if scope_type == "environment":
        return bool(server.environment_id) and str(server.environment_id) == scope_id
    if scope_type == "server":
        return str(server.id) == scope_id
    if scope_type == "tag":
        return tag_matches(scope_id, server)
    return False


def zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def occurrences(w: MaintenanceWindow, around: datetime) -> list[tuple[datetime, datetime]]:
    """Occurrences of a window that may contain ``around`` (and the next one, for display).

    A recurring window repeats the *local* time of day of its first occurrence in its time zone,
    so "02:00–04:00 Europe/Berlin" stays 02:00–04:00 local across daylight-saving changes.
    """
    if w.recurrence not in ("daily", "weekly"):
        return [(w.starts_at, w.ends_at)]
    tz = zone(w.time_zone)
    first = w.starts_at.astimezone(tz)
    duration = w.ends_at - w.starts_at
    t0 = time(first.hour, first.minute, first.second)
    local_day = around.astimezone(tz).date()
    out = []
    for offset in range(-1, 9):  # yesterday (spill-over) … next week (next occurrence)
        d: date = local_day + timedelta(days=offset)
        if w.recurrence == "weekly" and d.isoweekday() not in (w.weekdays or [first.isoweekday()]):
            continue
        start = datetime.combine(d, t0, tzinfo=tz)
        if start < w.starts_at - timedelta(seconds=1):
            continue
        if w.repeat_until is not None and start >= w.repeat_until:
            continue
        out.append((start.astimezone(UTC), (start + duration).astimezone(UTC)))
    return out


def window_state(w: MaintenanceWindow, now: datetime) -> tuple[str, datetime | None, datetime | None]:
    """(active | scheduled | ended, current-or-next start, its end)."""
    for start, end in occurrences(w, now):
        if start <= now < end:
            return "active", start, end
    upcoming = [(s, e) for s, e in occurrences(w, now) if s > now]
    if upcoming:
        return "scheduled", *upcoming[0]
    return "ended", None, None


def window_active(w: MaintenanceWindow, now: datetime) -> bool:
    return window_state(w, now)[0] == "active"


def active_window(windows: list[MaintenanceWindow], server: Server, now: datetime) -> MaintenanceWindow | None:
    for w in windows:
        if in_scope(w.scope_type, w.scope_id, server) and window_active(w, now):
            return w
    return None


def channel_accepts(channel: NotificationChannel, rule: AlertRule, alert: Alert, server: Server) -> bool:
    """Routing: rule's channels or subscribe-all channels, then severity and route filters."""
    routes = channel.routes or {}
    listed = str(channel.id) in [str(c) for c in rule.channel_ids or []]
    if not (listed or routes.get("subscribe_all")):
        return False
    if notify.SEVERITY_ORDER.get(alert.severity, 0) < notify.SEVERITY_ORDER.get(channel.min_severity, 0):
        return False
    envs = [str(e) for e in routes.get("environment_ids") or []]
    if envs and str(server.environment_id or "") not in envs:
        return False
    tags = routes.get("tags") or []
    if tags and not any(tag_matches(t, server) for t in tags):
        return False
    engines = routes.get("engines") or []
    return not (engines and server.engine not in engines)


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


def condition(
    rule: AlertRule, server: Server, now: datetime, window_values: dict[str, Any] | None = None
) -> tuple[bool | None, float | None, str]:
    """(holds?, observed value, human description). ``None`` = no data: keep the current state.

    ``window_values``: server id → aggregate over the rule's window (range rules only)."""
    if rule.kind == "metric" and rule.aggregation == "zscore":
        return _zscore_condition(rule, server, now, window_values)
    if rule.kind == "metric":
        ranged = rule.aggregation in ("avg", "min", "max") and rule.window_seconds > 0
        if ranged:
            v = (window_values or {}).get(str(server.id))
        else:
            v = _metric_value(rule, server, now)
        if v is None or rule.operator not in OPERATORS or rule.threshold is None:
            return None, None, "no data in the window" if ranged else "no current value"
        holds = OPERATORS[rule.operator](v, rule.threshold)
        what = f"{rule.aggregation}({rule.metric}, {_dur(rule.window_seconds)})" if ranged else rule.metric
        return holds, v, f"{what} = {v:g} ({rule.operator} {rule.threshold:g})"
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


def _zscore_condition(
    rule: AlertRule, server: Server, now: datetime, window_values: dict[str, Any] | None
) -> tuple[bool | None, float | None, str]:
    """``zscore`` rules: how many standard deviations the current value (latest, or the
    window average) is from the server's own baseline; the threshold applies to the z-score."""
    from app.services.perf_intel import zscore

    data = (window_values or {}).get(str(server.id)) or {}
    current = data.get("current") if rule.window_seconds > 0 else _metric_value(rule, server, now)
    z, reason = zscore(current, data.get("baseline"))
    if z is None or rule.operator not in OPERATORS or rule.threshold is None:
        return None, None, f"no z-score: {reason}"
    base = data["baseline"]
    holds = OPERATORS[rule.operator](z, rule.threshold)
    what = f"avg({rule.metric}, {_dur(rule.window_seconds)})" if rule.window_seconds > 0 else rule.metric
    return (
        holds,
        round(z, 3),
        f"z-score of {what} = {z:.2f} ({rule.operator} {rule.threshold:g}): current {current:g}, "
        f"baseline {base['avg']:.4g} ± {base['stddev']:.4g} over {rule.baseline_days}d",
    )


async def zscore_inputs(rule: AlertRule, servers: list[Server], now: datetime) -> dict[str, Any] | None:
    """Per server: the baseline (mean, stddev, samples over ``baseline_days``) and, for
    windowed rules, the window average. ``None`` when the time-series storage is down."""
    from app.services.perf_intel import baselines
    from app.timeseries.repository import TimeSeriesError

    if not servers or not rule.metric:
        return {}
    ids = [str(s.id) for s in servers]
    try:
        base = await baselines(
            rule.metric, ids, days=max(rule.baseline_days, 1), exclude_recent_s=rule.window_seconds, now=now
        )
        current = await window_aggregates(rule, servers, now) if rule.window_seconds > 0 else {}
    except TimeSeriesError as exc:
        log.warning("alert rule %s: baseline query failed: %s", rule.name, exc)
        return None
    if current is None:
        return None
    return {sid: {"baseline": base.get(sid), "current": current.get(sid)} for sid in ids}


def _dur(seconds: int) -> str:
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    if seconds % 60 == 0:
        return f"{seconds // 60}m"
    return f"{seconds}s"


async def window_aggregates(rule: AlertRule, servers: list[Server], now: datetime) -> dict[str, float] | None:
    """avg/min/max of the rule's metric over its window for each server, from the time-series
    database. ``None`` when the storage is unavailable (the rule then sees "no data")."""
    from app.timeseries.catalog import name_for
    from app.timeseries.model import Selector
    from app.timeseries.repository import TimeSeriesError, get_repository

    if not servers or not rule.metric:
        return {}
    sel = Selector.of([name_for(rule.metric)], server_ids=[str(s.id) for s in servers])
    try:
        values = await get_repository().aggregate(
            sel,
            now - timedelta(seconds=rule.window_seconds),
            now,
            "avg" if rule.aggregation == "zscore" else rule.aggregation,  # type: ignore[arg-type]
        )
    except TimeSeriesError as exc:
        log.warning("alert rule %s: time-series query failed: %s", rule.name, exc)
        return None
    return {v.labels.get("server_id", ""): float(v.value) for v in values if v.labels.get("server_id")}


def alert_dict(alert: Alert, rule: AlertRule, server: Server, *, renotify: bool = False) -> dict[str, Any]:
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
        "renotify": renotify,
        "notify_count": alert.notify_count,
        "url": f"{base}/alerts?id={alert.id}",
    }


# ---- notifications -------------------------------------------------------------------------------
def backoff_seconds(attempt: int) -> int:
    """Delay before attempt ``attempt + 1`` (attempt 1 failed → first backoff step)."""
    steps = [int(x) for x in get_settings().notify_retry_backoff_seconds if str(x).strip().isdigit()] or [60]
    return steps[min(attempt - 1, len(steps) - 1)]


def _retryable(exc: notify.DeliveryError) -> bool:
    return exc.code is None or exc.code >= 500 or exc.code in RETRYABLE_CODES


async def _attempt(d: NotificationDelivery, channel: NotificationChannel, now: datetime, *, retries: bool) -> None:
    """One delivery attempt; on a retryable failure schedule the next one (with backoff)."""
    d.attempts += 1
    try:
        d.response_code = await notify.send(channel, d.event, (d.payload or {}).get("alert"))
        d.status, d.error, d.next_attempt_at = "sent", None, None
        return
    except notify.DeliveryError as exc:
        d.error, d.response_code = str(exc)[:2000], exc.code
        retry = _retryable(exc)
    except Exception as exc:  # noqa: BLE001 - recorded on the delivery row
        d.error, retry = f"{type(exc).__name__}: {exc}"[:2000], True
    if retries and retry and d.attempts < get_settings().notify_max_attempts:
        d.status, d.next_attempt_at = "retrying", now + timedelta(seconds=backoff_seconds(d.attempts))
    else:
        d.status, d.next_attempt_at = "failed", None


async def deliver(
    db: AsyncSession,
    channel: NotificationChannel,
    event: str,
    alert: Alert | None,
    data: dict[str, Any] | None,
    *,
    skip_reason: str | None = None,
    retries: bool = True,
    now: datetime | None = None,
) -> NotificationDelivery:
    """Send now; failures are retried later by :func:`retry_due` unless ``retries`` is off
    (test notifications report their result immediately)."""
    d = NotificationDelivery(
        channel_id=channel.id,
        alert_id=alert.id if alert else None,
        event=event,
        status="skipped",
        attempts=0,
        payload={"alert": data} if data is not None else None,
    )
    if skip_reason:
        d.error = skip_reason
    else:
        await _attempt(d, channel, now or datetime.now(UTC), retries=retries)
    db.add(d)
    return d


async def retry_due(db: AsyncSession, now: datetime | None = None, limit: int = 200) -> dict[str, int]:
    """Global job ``notification_retries``: re-send deliveries whose backoff has elapsed."""
    now = now or datetime.now(UTC)
    rows = list(
        (
            await db.execute(
                select(NotificationDelivery)
                .where(NotificationDelivery.status == "retrying", NotificationDelivery.next_attempt_at <= now)
                .order_by(NotificationDelivery.next_attempt_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).scalars()
    )
    stats = {"due": len(rows), "sent": 0, "retrying": 0, "failed": 0}
    for d in rows:
        channel = await db.get(NotificationChannel, d.channel_id)
        if channel is None or not channel.enabled:
            d.status, d.next_attempt_at = "failed", None
            d.error = "channel removed or disabled before the retry"
        else:
            await _attempt(d, channel, now, retries=True)
        stats[d.status] = stats.get(d.status, 0) + 1
    await db.flush()
    return stats


async def _notify(
    db: AsyncSession,
    rule: AlertRule,
    alert: Alert,
    server: Server,
    event: str,
    channels: dict[str, NotificationChannel],
    window: MaintenanceWindow | None,
    now: datetime,
    *,
    renotify: bool = False,
) -> int:
    sent = 0
    data = alert_dict(alert, rule, server, renotify=renotify)
    for ch in channels.values():
        if not ch.enabled or not channel_accepts(ch, rule, alert, server):
            continue
        reason = None
        if window is not None:
            reason = f"suppressed by maintenance window {window.name!r}"
        elif server.is_demo and not ch.send_for_simulated:
            reason = "simulated server: channel does not send for simulated servers"
        d = await deliver(db, ch, event, alert, data, skip_reason=reason, now=now)
        sent += d.status == "sent"
    return sent


async def _fire_notification(
    db: AsyncSession,
    rule: AlertRule,
    alert: Alert,
    server: Server,
    channels: dict[str, NotificationChannel],
    now: datetime,
    *,
    renotify: bool = False,
) -> int:
    alert.notify_count += 1
    alert.notified_at = now
    await db.flush()
    return await _notify(db, rule, alert, server, "firing", channels, None, now, renotify=renotify)


# ---- evaluation -------------------------------------------------------------------------------------
async def _current_windows(db: AsyncSession, now: datetime) -> list[MaintenanceWindow]:
    rows = (
        await db.execute(
            select(MaintenanceWindow).where(
                MaintenanceWindow.starts_at <= now,
                or_(
                    MaintenanceWindow.ends_at > now,
                    (MaintenanceWindow.recurrence != "none")
                    & or_(
                        MaintenanceWindow.repeat_until.is_(None),
                        MaintenanceWindow.repeat_until > now - timedelta(days=1),
                    ),
                ),
            )
        )
    ).scalars()
    return [w for w in rows if window_active(w, now)]


async def evaluate(db: AsyncSession, now: datetime | None = None) -> dict[str, int]:
    now = now or datetime.now(UTC)
    rules = list((await db.execute(select(AlertRule).where(AlertRule.enabled.is_(True)))).scalars())
    stats = {
        "rules": len(rules),
        "fired": 0,
        "resolved": 0,
        "pending": 0,
        "notified": 0,
        "suppressed": 0,
        "renotified": 0,
        "released": 0,
    }
    if not rules:
        return stats
    servers = list((await db.execute(select(Server).where(Server.monitoring_enabled.is_(True)))).scalars())
    windows = await _current_windows(db, now)
    channels = {str(c.id): c for c in (await db.execute(select(NotificationChannel))).scalars()}
    active = {
        (a.rule_id, a.server_id): a
        for a in (await db.execute(select(Alert).where(Alert.status.in_(ACTIVE_STATES)))).scalars()
    }
    visited: set[tuple[uuid.UUID, uuid.UUID]] = set()
    for rule in rules:
        scoped = [s for s in servers if in_scope(rule.scope_type, rule.scope_id, s)]
        visited.update((rule.id, s.id) for s in scoped)
        ranged = rule.kind == "metric" and rule.aggregation in ("avg", "min", "max") and rule.window_seconds > 0
        window_values: dict[str, Any] | None = await window_aggregates(rule, scoped, now) if ranged else None
        if rule.kind == "metric" and rule.aggregation == "zscore":
            window_values = await zscore_inputs(rule, scoped, now)
        for server in scoped:
            holds, value, desc = condition(rule, server, now, window_values)
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
                        notify_count=0,
                    )
                    db.add(alert)
                    active[(rule.id, server.id)] = alert
                alert.value, alert.message, alert.last_evaluated_at = value, desc, now
                # suppressed_by keeps the window an alert was raised in after the window ended
                alert.suppressed = window is not None
                if window is not None:
                    alert.suppressed_by = window.id
                if alert.status == "pending":
                    if (now - alert.pending_since) >= timedelta(seconds=rule.for_seconds):
                        alert.status, alert.fired_at = "firing", now
                        await db.flush()
                        stats["fired"] += 1
                        if window is not None:
                            stats["suppressed"] += 1
                            await _notify(db, rule, alert, server, "firing", channels, window, now)
                        else:
                            stats["notified"] += await _fire_notification(db, rule, alert, server, channels, now)
                    else:
                        stats["pending"] += 1
                elif alert.status == "firing" and window is None:
                    if alert.notified_at is None:
                        # fired inside a maintenance window that has ended: tell people now
                        stats["released"] += 1
                        stats["notified"] += await _fire_notification(db, rule, alert, server, channels, now)
                    elif rule.renotify_minutes > 0 and now - alert.notified_at >= timedelta(
                        minutes=rule.renotify_minutes
                    ):
                        stats["renotified"] += 1
                        stats["notified"] += await _fire_notification(
                            db, rule, alert, server, channels, now, renotify=True
                        )
            elif alert is not None:
                del active[(rule.id, server.id)]
                if alert.status == "pending":
                    await db.delete(alert)  # never fired: nothing to tell anyone
                    continue
                was_notified = alert.notified_at is not None
                alert.status, alert.resolved_at, alert.resolved_by = "resolved", now, "auto"
                alert.value, alert.message, alert.last_evaluated_at = value, desc, now
                await db.flush()
                stats["resolved"] += 1
                if rule.notify_on_resolve and was_notified:
                    stats["notified"] += await _notify(db, rule, alert, server, "resolved", channels, window, now)
    # alerts of enabled rules for servers that are no longer evaluated (left the rule's scope,
    # monitoring disabled, deleted): nobody can tell whether the condition still holds
    enabled = {r.id for r in rules}
    for (rule_id, server_id), alert in list(active.items()):
        if rule_id in enabled and (rule_id, server_id) not in visited:
            if alert.status == "pending":
                await db.delete(alert)
                continue
            alert.status, alert.resolved_at, alert.resolved_by = "resolved", now, "scope"
            alert.last_evaluated_at = now
            stats["resolved"] += 1
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
