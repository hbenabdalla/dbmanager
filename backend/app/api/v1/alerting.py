"""Alerting API: alerts, alert rules, maintenance windows, notification channels."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import case, func, select

from app.api.deps import DB, CurrentPrincipal, rate_limit
from app.core.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from app.models import (
    Alert,
    AlertComment,
    AlertRule,
    Environment,
    MaintenanceWindow,
    NotificationChannel,
    NotificationDelivery,
    Server,
)
from app.models.alerting import ACTIVE_STATES
from app.schemas.common import Message, Page, PageParams, apply_sort
from app.services import alerting, audit, notify
from app.services.authz import server_ref

router = APIRouter(tags=["alerting"])

Severity = Literal["critical", "high", "medium", "low", "info"]
ScopeT = Literal["all", "environment", "server", "tag"]
TAG_SPEC = r"^[A-Za-z0-9_.:/-]{1,64}(=[^\x00-\x1f]{0,64})?$"
SEVERITY_RANK = case({"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}, value=Alert.severity, else_=5)


# ---- helpers ------------------------------------------------------------------------------------
async def _visible_servers(db: DB, p: CurrentPrincipal) -> list[Server] | None:
    """Servers the caller may see alerts of; ``None`` = all (platform-wide permission)."""
    flt = p.authz.server_filter("alerts.view")
    if flt is None:
        return None
    return list((await db.execute(select(Server).where(flt))).scalars())


def _scope_visible(scope_type: str, scope_id: str | None, visible: list[Server] | None) -> bool:
    if visible is None or scope_type == "all":
        return True
    return any(alerting.in_scope(scope_type, scope_id, s) for s in visible)


async def _check_scope(db: DB, scope_type: str, scope_id: str | None) -> None:
    import re

    if scope_type == "all":
        return
    if not scope_id:
        raise ValidationFailed(f"scope_id is required for scope {scope_type}")
    if scope_type == "tag":
        if not re.match(TAG_SPEC, scope_id):
            raise ValidationFailed("tag scope must be 'key' or 'key=value' (key: letters, digits, _ . : / -)")
        return
    try:
        sid = uuid.UUID(scope_id)
    except ValueError as exc:
        raise ValidationFailed("scope_id must be a UUID") from exc
    model = Environment if scope_type == "environment" else Server
    if await db.get(model, sid) is None:
        raise ValidationFailed(f"{scope_type} {scope_id} does not exist")


def known_metrics() -> set[str]:
    from app.services import metric_catalog
    from app.timeseries.catalog import BY_KEY

    return set(metric_catalog.CATALOG) | set(BY_KEY)


def _rule_out(r: AlertRule) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "name": r.name,
        "description": r.description,
        "enabled": r.enabled,
        "kind": r.kind,
        "metric": r.metric,
        "operator": r.operator,
        "threshold": r.threshold,
        "health_states": r.health_states,
        "for_seconds": r.for_seconds,
        "severity": r.severity,
        "scope_type": r.scope_type,
        "scope_id": r.scope_id,
        "aggregation": r.aggregation,
        "window_seconds": r.window_seconds,
        "baseline_days": r.baseline_days,
        "channel_ids": r.channel_ids,
        "notify_on_resolve": r.notify_on_resolve,
        "renotify_minutes": r.renotify_minutes,
        "created_by": r.created_by,
        "created_at": r.created_at,
        "updated_at": r.updated_at,
    }


def _alert_out(a: Alert, r: AlertRule, s: Server) -> dict[str, Any]:
    return {
        "id": str(a.id),
        "rule_id": str(r.id),
        "rule_name": r.name,
        "rule_kind": r.kind,
        "operator": r.operator,
        "server_id": str(s.id),
        "server_name": s.name,
        "engine": s.engine,
        "environment": s.environment.name if s.environment else None,
        "status": a.status,
        "severity": a.severity,
        "title": a.title,
        "message": a.message,
        "metric": a.metric,
        "value": a.value,
        "threshold": a.threshold,
        "is_demo": a.is_demo,
        "suppressed": a.suppressed,
        "pending_since": a.pending_since,
        "fired_at": a.fired_at,
        "acknowledged_at": a.acknowledged_at,
        "acknowledged_by": a.acknowledged_by,
        "ack_comment": a.ack_comment,
        "resolved_at": a.resolved_at,
        "resolved_by": a.resolved_by,
        "last_evaluated_at": a.last_evaluated_at,
        "notified_at": a.notified_at,
        "notify_count": a.notify_count,
    }


# ---- alerts -------------------------------------------------------------------------------------------
@router.get("/alerts", summary="Alerts of visible servers")
async def list_alerts(
    db: DB,
    p: CurrentPrincipal,
    params: PageParams = Depends(),
    status: list[str] | None = Query(None, description="pending, firing, acknowledged, resolved (default: active)"),
    severity: list[str] | None = Query(None),
    server_id: uuid.UUID | None = None,
    rule_id: uuid.UUID | None = None,
    include_demo: bool = True,
) -> Page[dict[str, Any]]:
    p.authz.require_anywhere("alerts.view")
    stmt = (
        select(Alert, AlertRule, Server)
        .join(AlertRule, AlertRule.id == Alert.rule_id)
        .join(Server, Server.id == Alert.server_id)
    )
    flt = p.authz.server_filter("alerts.view")
    if flt is not None:
        stmt = stmt.where(flt)
    stmt = stmt.where(Alert.status.in_(status or ["firing", "acknowledged"]))
    if severity:
        stmt = stmt.where(Alert.severity.in_(severity))
    if server_id:
        stmt = stmt.where(Alert.server_id == server_id)
    if rule_id:
        stmt = stmt.where(Alert.rule_id == rule_id)
    if not include_demo:
        stmt = stmt.where(Alert.is_demo.is_(False))
    if params.q:
        like = f"%{params.q.lower()}%"
        stmt = stmt.where(func.lower(Alert.title).like(like) | func.lower(Server.name).like(like))
    stmt = apply_sort(
        stmt,
        params.sort,
        {
            "severity": SEVERITY_RANK,
            "fired_at": Alert.fired_at,
            "server": Server.name,
            "resolved_at": Alert.resolved_at,
        },
        SEVERITY_RANK,
    )
    total = (await db.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))).scalar_one()
    rows = (await db.execute(stmt.limit(params.page_size).offset((params.page - 1) * params.page_size))).all()
    return Page(
        items=[_alert_out(a, r, s) for a, r, s in rows], total=total, page=params.page, page_size=params.page_size
    )


@router.get("/alerts/summary", summary="Active alert counts for the top bar, dashboard and server badges")
async def alert_summary(db: DB, p: CurrentPrincipal, by_server: bool = False) -> dict[str, Any]:
    if not p.authz.has_anywhere("alerts.view"):
        return {"active": 0, "by_severity": {}, "visible": False, **({"by_server": {}} if by_server else {})}
    stmt = (
        select(Alert.severity, func.count())
        .join(Server, Server.id == Alert.server_id)
        .where(Alert.status.in_(["firing", "acknowledged"]))
        .group_by(Alert.severity)
    )
    flt = p.authz.server_filter("alerts.view")
    if flt is not None:
        stmt = stmt.where(flt)
    by = dict((await db.execute(stmt)).all())
    unacked = (
        select(func.count())
        .select_from(Alert)
        .join(Server, Server.id == Alert.server_id)
        .where(Alert.status == "firing")
    )
    if flt is not None:
        unacked = unacked.where(flt)
    out: dict[str, Any] = {
        "active": sum(by.values()),
        "unacknowledged": (await db.execute(unacked)).scalar_one(),
        "by_severity": by,
        "visible": True,
    }
    if by_server:
        per = (
            select(Alert.server_id, Alert.severity, Alert.status, func.count())
            .join(Server, Server.id == Alert.server_id)
            .where(Alert.status.in_(["firing", "acknowledged"]))
            .group_by(Alert.server_id, Alert.severity, Alert.status)
        )
        if flt is not None:
            per = per.where(flt)
        servers: dict[str, dict[str, Any]] = {}
        for sid, sev, st, n in (await db.execute(per)).all():
            e = servers.setdefault(str(sid), {"active": 0, "unacknowledged": 0, "top_severity": sev})
            e["active"] += n
            e["unacknowledged"] += n if st == "firing" else 0
            if notify.SEVERITY_ORDER.get(sev, 0) > notify.SEVERITY_ORDER.get(e["top_severity"], 0):
                e["top_severity"] = sev
        out["by_server"] = servers
    return out


class AckIn(BaseModel):
    comment: str | None = Field(None, max_length=2000)


async def _load_alert(db: DB, p: CurrentPrincipal, alert_id: uuid.UUID, perm: str) -> tuple[Alert, AlertRule, Server]:
    row = (
        await db.execute(
            select(Alert, AlertRule, Server)
            .join(AlertRule, AlertRule.id == Alert.rule_id)
            .join(Server, Server.id == Alert.server_id)
            .where(Alert.id == alert_id)
        )
    ).first()
    if row is None:
        raise NotFound("Alert not found")
    a, r, s = row
    ref = server_ref(s)
    if not p.authz.can_see("alerts.view", ref):
        raise NotFound("Alert not found")
    if not p.authz.has(perm, ref):
        raise PermissionDenied(f"Missing permission '{perm}' for this server", details={"permission": perm})
    return a, r, s


class CommentIn(BaseModel):
    body: str = Field(..., min_length=1, max_length=4000)


def _comment_out(c: AlertComment) -> dict[str, Any]:
    return {"id": str(c.id), "author": c.author, "body": c.body, "created_at": c.created_at}


@router.get("/alerts/{alert_id}", summary="One alert with its comments and notification history")
async def get_alert(alert_id: uuid.UUID, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    a, r, s = await _load_alert(db, p, alert_id, "alerts.view")
    comments = (
        await db.execute(select(AlertComment).where(AlertComment.alert_id == a.id).order_by(AlertComment.created_at))
    ).scalars()
    deliveries = (
        await db.execute(
            select(NotificationDelivery, NotificationChannel.name)
            .join(NotificationChannel, NotificationChannel.id == NotificationDelivery.channel_id)
            .where(NotificationDelivery.alert_id == a.id)
            .order_by(NotificationDelivery.created_at)
        )
    ).all()
    return {
        **_alert_out(a, r, s),
        "comments": [_comment_out(c) for c in comments],
        "notifications": [
            {
                "channel": name,
                "event": d.event,
                "status": d.status,
                "attempts": d.attempts,
                "error": d.error,
                "created_at": d.created_at,
                "next_attempt_at": d.next_attempt_at,
            }
            for d, name in deliveries
        ],
    }


@router.post("/alerts/{alert_id}/comments", status_code=201, summary="Comment on an alert")
async def comment_alert(alert_id: uuid.UUID, body: CommentIn, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    a, _r, s = await _load_alert(db, p, alert_id, "alerts.acknowledge")
    c = AlertComment(id=uuid.uuid4(), alert_id=a.id, author=p.username, body=body.body, created_at=datetime.now(UTC))
    db.add(c)
    await db.flush()
    await audit.record(
        "alert.comment", actor=p, category="alerting", server=s, object_name=a.title, details={"comment": body.body}
    )
    return _comment_out(c)


@router.post("/alerts/{alert_id}/acknowledge", summary="Acknowledge a firing alert")
async def acknowledge(alert_id: uuid.UUID, body: AckIn, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    a, r, s = await _load_alert(db, p, alert_id, "alerts.acknowledge")
    if a.status != "firing":
        raise Conflict(f"only firing alerts can be acknowledged (this one is {a.status})")
    a.status, a.acknowledged_at, a.acknowledged_by, a.ack_comment = (
        "acknowledged",
        datetime.now(UTC),
        p.username,
        body.comment,
    )
    await audit.record(
        "alert.acknowledge",
        actor=p,
        category="alerting",
        server=s,
        object_name=a.title,
        details={"comment": body.comment},
    )
    return _alert_out(a, r, s)


@router.post("/alerts/{alert_id}/resolve", summary="Close an alert manually")
async def resolve(alert_id: uuid.UUID, body: AckIn, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    a, r, s = await _load_alert(db, p, alert_id, "alerts.acknowledge")
    if a.status not in ACTIVE_STATES:
        raise Conflict("the alert is already resolved")
    a.status, a.resolved_at, a.resolved_by = "resolved", datetime.now(UTC), p.username
    if body.comment:
        a.ack_comment = body.comment
    await audit.record(
        "alert.resolve", actor=p, category="alerting", server=s, object_name=a.title, details={"comment": body.comment}
    )
    return _alert_out(a, r, s)


@router.post("/alerts/evaluate", summary="Evaluate all alert rules now")
async def evaluate_now(db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    p.authz.require("alerts.manage")
    stats = await alerting.evaluate(db)
    await audit.record("alert.evaluate", actor=p, category="alerting", details=stats)
    return stats


# ---- rules ----------------------------------------------------------------------------------------------
class RuleIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: str | None = Field(None, max_length=2000)
    enabled: bool = True
    kind: Literal["metric", "health", "collector"]
    metric: str | None = Field(None, max_length=128)
    operator: Literal[">", ">=", "<", "<=", "==", "!="] | None = None
    threshold: float | None = None
    health_states: list[Literal["unavailable", "critical", "warning", "unknown"]] = Field(default_factory=list)
    for_seconds: int = Field(0, ge=0, le=7 * 86400)
    severity: Severity
    scope_type: ScopeT = "all"
    scope_id: str | None = None
    #: metric rules: latest collected value, avg/min/max over window_seconds of history, or
    #: zscore: deviations of the current value (latest, or avg over window_seconds) from the
    #: server's own baseline over baseline_days — the threshold is then a z-score (e.g. > 3)
    aggregation: Literal["latest", "avg", "min", "max", "zscore"] = "latest"
    window_seconds: int = Field(0, ge=0, le=7 * 86400)
    baseline_days: int = Field(7, ge=1, le=90)
    channel_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)
    notify_on_resolve: bool = True
    renotify_minutes: int = Field(0, ge=0, le=7 * 24 * 60)

    @model_validator(mode="after")
    def _kind_fields(self) -> RuleIn:
        if self.kind == "metric":
            if not self.metric or self.operator is None or self.threshold is None:
                raise ValueError("metric rules need metric, operator and threshold")
            if self.aggregation in ("avg", "min", "max") and self.window_seconds < 60:
                raise ValueError("avg/min/max rules need window_seconds of at least 60")
            if self.aggregation == "zscore" and 0 < self.window_seconds < 60:
                raise ValueError("zscore rules use the latest value (window_seconds 0) or a window of at least 60 s")
            if self.aggregation == "zscore" and self.window_seconds >= self.baseline_days * 86400:
                raise ValueError("the zscore window must be shorter than the baseline period")
            if self.aggregation == "latest":
                self.window_seconds = 0
        else:
            self.aggregation, self.window_seconds = "latest", 0
            if self.kind == "health" and not self.health_states:
                self.health_states = ["unavailable"]
        return self


async def _validate_rule(db: DB, body: RuleIn) -> None:
    await _check_scope(db, body.scope_type, body.scope_id)
    if body.kind == "metric":
        if body.metric not in known_metrics():
            raise ValidationFailed(f"unknown metric {body.metric!r}: pick one from /metrics/catalog")
    if body.channel_ids:
        found = set(
            (
                await db.execute(select(NotificationChannel.id).where(NotificationChannel.id.in_(body.channel_ids)))
            ).scalars()
        )
        missing = [str(c) for c in body.channel_ids if c not in found]
        if missing:
            raise ValidationFailed("unknown notification channels", details={"missing": missing})


def _apply_rule(r: AlertRule, body: RuleIn) -> None:
    data = body.model_dump()
    data["channel_ids"] = [str(c) for c in body.channel_ids]
    if body.kind != "metric":
        data["metric"] = data["operator"] = None
        if body.kind == "health":
            data["threshold"] = None
    if body.kind != "health":
        data["health_states"] = []
    if body.scope_type == "all":
        data["scope_id"] = None
    for k, v in data.items():
        setattr(r, k, v)


@router.get("/alert-rules", summary="Alert rules (scoped viewers see rules that can apply to their servers)")
async def list_rules(db: DB, p: CurrentPrincipal, params: PageParams = Depends()) -> Page[dict[str, Any]]:
    p.authz.require_anywhere("alerts.view")
    stmt = select(AlertRule)
    if params.q:
        stmt = stmt.where(func.lower(AlertRule.name).like(f"%{params.q.lower()}%"))
    stmt = apply_sort(stmt, params.sort, {"name": AlertRule.name, "severity": AlertRule.severity}, AlertRule.name)
    visible = await _visible_servers(db, p)
    rows = [r for r in (await db.execute(stmt)).scalars() if _scope_visible(r.scope_type, r.scope_id, visible)]
    page = rows[(params.page - 1) * params.page_size : params.page * params.page_size]
    return Page(items=[_rule_out(r) for r in page], total=len(rows), page=params.page, page_size=params.page_size)


@router.post("/alert-rules", status_code=201, summary="Create an alert rule")
async def create_rule(body: RuleIn, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    p.authz.require("alerts.manage")
    await _validate_rule(db, body)
    if (await db.execute(select(AlertRule.id).where(AlertRule.name == body.name))).first():
        raise Conflict("an alert rule with this name already exists")
    r = AlertRule(id=uuid.uuid4(), created_by=p.username)
    _apply_rule(r, body)
    db.add(r)
    await db.flush()
    await audit.record("alert_rule.create", actor=p, category="alerting", object_name=r.name, details=_audit_rule(r))
    return _rule_out(r)


def _audit_rule(r: AlertRule) -> dict[str, Any]:
    return {k: v for k, v in _rule_out(r).items() if k not in ("created_at", "updated_at")}


@router.put("/alert-rules/{rule_id}", summary="Replace an alert rule")
async def update_rule(rule_id: uuid.UUID, body: RuleIn, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    p.authz.require("alerts.manage")
    r = await db.get(AlertRule, rule_id)
    if r is None:
        raise NotFound("Alert rule not found")
    await _validate_rule(db, body)
    dup = (await db.execute(select(AlertRule.id).where(AlertRule.name == body.name, AlertRule.id != rule_id))).first()
    if dup:
        raise Conflict("an alert rule with this name already exists")
    was_enabled = r.enabled
    _apply_rule(r, body)
    closed = (
        await alerting.resolve_for_rule(db, r, f"rule disabled by {p.username}") if was_enabled and not r.enabled else 0
    )
    await audit.record(
        "alert_rule.update",
        actor=p,
        category="alerting",
        object_name=r.name,
        details={**_audit_rule(r), "closed": closed},
    )
    return _rule_out(r)


@router.delete("/alert-rules/{rule_id}", summary="Delete an alert rule and its alerts")
async def delete_rule(rule_id: uuid.UUID, db: DB, p: CurrentPrincipal) -> Message:
    p.authz.require("alerts.manage")
    r = await db.get(AlertRule, rule_id)
    if r is None:
        raise NotFound("Alert rule not found")
    name = r.name
    await db.delete(r)
    await audit.record("alert_rule.delete", actor=p, category="alerting", object_name=name)
    return Message(message=f"Alert rule {name} deleted")


# ---- maintenance windows -------------------------------------------------------------------------------
class WindowIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    reason: str | None = Field(None, max_length=2000)
    scope_type: ScopeT = "all"
    scope_id: str | None = None
    #: first (or only) occurrence, ISO 8601 with offset
    starts_at: datetime
    ends_at: datetime
    recurrence: Literal["none", "daily", "weekly"] = "none"
    #: weekly: ISO weekdays 1 = Monday … 7 = Sunday (default: the weekday of starts_at)
    weekdays: list[int] = Field(default_factory=list, max_length=7)
    #: IANA time zone the recurrence follows (e.g. Europe/Berlin), default UTC
    time_zone: str = Field("UTC", max_length=64)
    repeat_until: datetime | None = None

    @model_validator(mode="after")
    def _order(self) -> WindowIn:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        if self.starts_at.tzinfo is None or self.ends_at.tzinfo is None:
            raise ValueError("starts_at and ends_at need a time zone (ISO 8601 with offset)")
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        try:
            tz = ZoneInfo(self.time_zone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown time zone {self.time_zone!r}") from exc
        if self.recurrence == "none":
            self.weekdays, self.repeat_until = [], None
            return self
        if self.ends_at - self.starts_at > alerting.MAX_RECURRING_DURATION:
            raise ValueError("an occurrence of a recurring window can last at most 24 hours")
        if self.repeat_until is not None:
            if self.repeat_until.tzinfo is None:
                raise ValueError("repeat_until needs a time zone")
            if self.repeat_until <= self.starts_at:
                raise ValueError("repeat_until must be after starts_at")
        if self.recurrence == "weekly":
            if any(d < 1 or d > 7 for d in self.weekdays):
                raise ValueError("weekdays are ISO weekday numbers 1 (Monday) … 7 (Sunday)")
            self.weekdays = sorted(set(self.weekdays)) or [self.starts_at.astimezone(tz).isoweekday()]
        else:
            self.weekdays = []
        return self


def _window_out(w: MaintenanceWindow, now: datetime | None = None) -> dict[str, Any]:
    state, start, end = alerting.window_state(w, now or datetime.now(UTC))
    return {
        "id": str(w.id),
        "name": w.name,
        "reason": w.reason,
        "scope_type": w.scope_type,
        "scope_id": w.scope_id,
        "starts_at": w.starts_at,
        "ends_at": w.ends_at,
        "recurrence": w.recurrence,
        "weekdays": w.weekdays,
        "time_zone": w.time_zone,
        "repeat_until": w.repeat_until,
        "state": state,
        "current_or_next_start": start,
        "current_or_next_end": end,
        "created_by": w.created_by,
    }


@router.get("/maintenance-windows", summary="Maintenance windows")
async def list_windows(
    db: DB,
    p: CurrentPrincipal,
    params: PageParams = Depends(),
    include_ended: bool = False,
    server_id: uuid.UUID | None = Query(None, description="only windows that apply to this server"),
) -> Page[dict[str, Any]]:
    p.authz.require_anywhere("alerts.view")
    stmt = apply_sort(
        select(MaintenanceWindow), params.sort, {"starts_at": MaintenanceWindow.starts_at}, MaintenanceWindow.starts_at
    )
    now = datetime.now(UTC)
    visible = await _visible_servers(db, p)
    target: Server | None = None
    if server_id is not None:
        target = await db.get(Server, server_id)
        if target is None or (visible is not None and target.id not in {s.id for s in visible}):
            raise NotFound("Server not found")
    rows = []
    for w in (await db.execute(stmt)).scalars():
        if not include_ended and alerting.window_state(w, now)[0] == "ended":
            continue
        if not _scope_visible(w.scope_type, w.scope_id, visible):
            continue
        if target is not None and not alerting.in_scope(w.scope_type, w.scope_id, target):
            continue
        rows.append(w)
    page = rows[(params.page - 1) * params.page_size : params.page * params.page_size]
    return Page(
        items=[_window_out(w, now) for w in page], total=len(rows), page=params.page, page_size=params.page_size
    )


def _window_values(body: WindowIn) -> dict[str, Any]:
    return {**body.model_dump(), "scope_id": None if body.scope_type == "all" else body.scope_id}


@router.post("/maintenance-windows", status_code=201, summary="Schedule a maintenance window")
async def create_window(body: WindowIn, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    p.authz.require("alerts.manage")
    await _check_scope(db, body.scope_type, body.scope_id)
    w = MaintenanceWindow(id=uuid.uuid4(), created_by=p.username, **_window_values(body))
    db.add(w)
    await db.flush()
    await audit.record(
        "maintenance_window.create", actor=p, category="alerting", object_name=w.name, details=_window_out(w)
    )
    return _window_out(w)


@router.put("/maintenance-windows/{window_id}", summary="Change a maintenance window")
async def update_window(window_id: uuid.UUID, body: WindowIn, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    p.authz.require("alerts.manage")
    w = await db.get(MaintenanceWindow, window_id)
    if w is None:
        raise NotFound("Maintenance window not found")
    await _check_scope(db, body.scope_type, body.scope_id)
    for k, v in _window_values(body).items():
        setattr(w, k, v)
    await db.flush()
    await audit.record(
        "maintenance_window.update", actor=p, category="alerting", object_name=w.name, details=_window_out(w)
    )
    return _window_out(w)


@router.delete("/maintenance-windows/{window_id}", summary="Cancel or delete a maintenance window")
async def delete_window(window_id: uuid.UUID, db: DB, p: CurrentPrincipal) -> Message:
    p.authz.require("alerts.manage")
    w = await db.get(MaintenanceWindow, window_id)
    if w is None:
        raise NotFound("Maintenance window not found")
    name = w.name
    await db.delete(w)
    await audit.record("maintenance_window.delete", actor=p, category="alerting", object_name=name)
    return Message(message=f"Maintenance window {name} removed")


# ---- notification channels --------------------------------------------------------------------------
class RoutesIn(BaseModel):
    #: also receive alerts of rules that do not list this channel
    subscribe_all: bool = False
    environment_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)
    tags: list[str] = Field(default_factory=list, max_length=50)
    engines: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("tags")
    @classmethod
    def _tags(cls, v: list[str]) -> list[str]:
        import re

        bad = [x for x in v if not re.match(TAG_SPEC, x)]
        if bad:
            raise ValueError(f"tag filters must be 'key' or 'key=value': {', '.join(bad)}")
        return v

    def stored(self) -> dict[str, Any]:
        return {
            "subscribe_all": self.subscribe_all,
            "environment_ids": [str(e) for e in self.environment_ids],
            "tags": self.tags,
            "engines": self.engines,
        }


class ChannelIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    kind: Literal["webhook", "email"]
    enabled: bool = True
    min_severity: Severity = "info"
    send_for_simulated: bool = False
    #: webhook payload format
    preset: Literal["generic", "slack", "teams"] = "generic"
    #: routing filters (see app.services.alerting.channel_accepts)
    routes: RoutesIn = Field(default_factory=lambda: RoutesIn())
    #: webhook: url, headers{}; email: recipients[], optional smtp_host/smtp_port/smtp_user/from/security
    config: dict[str, Any] = Field(default_factory=dict)
    #: write-only: webhook signing_secret / email smtp_password. Omit to keep the stored value,
    #: send an empty string to clear it.
    signing_secret: str | None = Field(None, max_length=512)
    smtp_password: str | None = Field(None, max_length=512)

    @field_validator("config")
    @classmethod
    def _no_secrets_in_config(cls, v: dict[str, Any]) -> dict[str, Any]:
        bad = [k for k in v if any(s in k.lower() for s in ("password", "secret", "token"))]
        if bad:
            raise ValueError(f"put secrets in signing_secret / smtp_password, not in config ({', '.join(bad)})")
        headers = v.get("headers") or {}
        if any(k.lower() == "authorization" for k in headers):
            raise ValueError("an Authorization header would be stored unencrypted: use signing_secret instead")
        return v


def _channel_out(c: NotificationChannel) -> dict[str, Any]:
    stored = []
    if c.secret:
        try:
            stored = sorted(notify.open_secret(c))
        except Exception:  # noqa: BLE001 - unreadable (rotated key): report as present
            stored = ["unreadable"]
    return {
        "id": str(c.id),
        "name": c.name,
        "kind": c.kind,
        "enabled": c.enabled,
        "min_severity": c.min_severity,
        "send_for_simulated": c.send_for_simulated,
        "preset": c.preset,
        "routes": c.routes or {},
        "config": c.config,
        # only which secrets are stored, never their values
        "has_signing_secret": "signing_secret" in stored,
        "has_smtp_password": "smtp_password" in stored,
        "created_by": c.created_by,
        "created_at": c.created_at,
    }


def _validate_channel(body: ChannelIn) -> dict[str, Any]:
    cfg = dict(body.config)
    if body.kind == "webhook":
        notify.validate_webhook_url(str(cfg.get("url") or ""))
        cfg = {"url": cfg["url"], "headers": {str(k): str(v) for k, v in (cfg.get("headers") or {}).items()}}
    else:
        recipients = cfg.get("recipients") or []
        if isinstance(recipients, str):
            recipients = [r.strip() for r in recipients.split(",")]
        recipients = [r for r in recipients if r]
        if not recipients or any("@" not in r for r in recipients):
            raise ValidationFailed("email channels need at least one valid recipient")
        cfg = {
            "recipients": recipients,
            **{k: cfg[k] for k in ("smtp_host", "smtp_port", "smtp_user", "from", "starttls", "security") if k in cfg},
        }
        if "security" in cfg and cfg["security"] not in notify.SMTP_SECURITY:
            raise ValidationFailed("security must be starttls, tls or none")
    return cfg


def _secret_values(body: ChannelIn, existing: dict[str, str]) -> dict[str, str]:
    vals = dict(existing)
    for key in ("signing_secret", "smtp_password"):
        v = getattr(body, key)
        if v is not None:
            vals[key] = v
    return {k: v for k, v in vals.items() if v}


@router.get("/notification-channels", summary="Notification channels")
async def list_channels(db: DB, p: CurrentPrincipal) -> list[dict[str, Any]]:
    if not (p.authz.has("channels.manage") or p.authz.has("alerts.manage")):
        raise PermissionDenied("Missing permission 'channels.manage'", details={"permission": "channels.manage"})
    rows = (await db.execute(select(NotificationChannel).order_by(NotificationChannel.name))).scalars()
    return [_channel_out(c) for c in rows]


@router.post("/notification-channels", status_code=201, summary="Create a notification channel")
async def create_channel(body: ChannelIn, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    p.authz.require("channels.manage")
    cfg = _validate_channel(body)
    if (await db.execute(select(NotificationChannel.id).where(NotificationChannel.name == body.name))).first():
        raise Conflict("a channel with this name already exists")
    cid = uuid.uuid4()
    c = NotificationChannel(
        id=cid,
        name=body.name,
        kind=body.kind,
        enabled=body.enabled,
        min_severity=body.min_severity,
        send_for_simulated=body.send_for_simulated,
        preset=body.preset if body.kind == "webhook" else "generic",
        routes=body.routes.stored(),
        config=cfg,
        secret=notify.seal_secret(cid, _secret_values(body, {})),
        created_by=p.username,
    )
    db.add(c)
    await db.flush()
    await audit.record(
        "notification_channel.create",
        actor=p,
        category="alerting",
        object_name=c.name,
        details={"kind": c.kind, "preset": c.preset, "routes": c.routes, "config": cfg},
    )
    return _channel_out(c)


@router.put("/notification-channels/{channel_id}", summary="Update a notification channel")
async def update_channel(channel_id: uuid.UUID, body: ChannelIn, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    p.authz.require("channels.manage")
    c = await db.get(NotificationChannel, channel_id)
    if c is None:
        raise NotFound("Notification channel not found")
    if body.kind != c.kind:
        raise ValidationFailed("the channel kind cannot be changed; create a new channel")
    cfg = _validate_channel(body)
    c.name, c.enabled, c.min_severity, c.send_for_simulated, c.config = (
        body.name,
        body.enabled,
        body.min_severity,
        body.send_for_simulated,
        cfg,
    )
    c.preset = body.preset if body.kind == "webhook" else "generic"
    c.routes = body.routes.stored()
    c.secret = notify.seal_secret(c.id, _secret_values(body, notify.open_secret(c)))
    await audit.record(
        "notification_channel.update",
        actor=p,
        category="alerting",
        object_name=c.name,
        details={
            "config": cfg,
            "preset": c.preset,
            "routes": c.routes,
            "secrets_changed": [k for k in ("signing_secret", "smtp_password") if getattr(body, k) is not None],
        },
    )
    return _channel_out(c)


@router.delete("/notification-channels/{channel_id}", summary="Delete a notification channel")
async def delete_channel(channel_id: uuid.UUID, db: DB, p: CurrentPrincipal) -> Message:
    p.authz.require("channels.manage")
    c = await db.get(NotificationChannel, channel_id)
    if c is None:
        raise NotFound("Notification channel not found")
    name, cid = c.name, str(c.id)
    for r in (await db.execute(select(AlertRule))).scalars():
        if cid in (r.channel_ids or []):
            r.channel_ids = [x for x in r.channel_ids if x != cid]
    await db.delete(c)
    await audit.record("notification_channel.delete", actor=p, category="alerting", object_name=name)
    return Message(message=f"Channel {name} deleted")


@router.post(
    "/notification-channels/{channel_id}/test",
    summary="Send a test notification",
    dependencies=[rate_limit("expensive")],
)
async def test_channel(channel_id: uuid.UUID, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    p.authz.require("channels.manage")
    c = await db.get(NotificationChannel, channel_id)
    if c is None:
        raise NotFound("Notification channel not found")
    d = await alerting.deliver(db, c, "test", None, None, retries=False)
    await audit.record(
        "notification_channel.test",
        actor=p,
        category="alerting",
        object_name=c.name,
        result="success" if d.status == "sent" else "failure",
        failure_reason=d.error,
    )
    return {"status": d.status, "response_code": d.response_code, "error": d.error, "attempts": d.attempts}


@router.get("/notification-channels/{channel_id}/deliveries", summary="Delivery log of a channel")
async def channel_deliveries(
    channel_id: uuid.UUID, db: DB, p: CurrentPrincipal, limit: int = Query(50, ge=1, le=500)
) -> list[dict[str, Any]]:
    p.authz.require("channels.manage")
    rows = (
        await db.execute(
            select(NotificationDelivery)
            .where(NotificationDelivery.channel_id == channel_id)
            .order_by(NotificationDelivery.created_at.desc())
            .limit(limit)
        )
    ).scalars()
    return [
        {
            "id": d.id,
            "alert_id": str(d.alert_id) if d.alert_id else None,
            "event": d.event,
            "status": d.status,
            "attempts": d.attempts,
            "response_code": d.response_code,
            "error": d.error,
            "created_at": d.created_at,
            "next_attempt_at": d.next_attempt_at,
        }
        for d in rows
    ]
